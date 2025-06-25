# --- Python 標準庫導入 ---
import copy
import traceback
from datetime import datetime, date, timedelta
from threading import Lock, Thread
from typing import Any, Dict, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo
import calendar
import time
import threading
import asyncio
import pandas as pd

# --- VnPy 相關導入 -
from vnpy.trader.event import  EVENT_LOG, EVENT_TIMER
from vnpy.trader.engine import EventEngine
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    TickData, OrderData, TradeData, PositionData, AccountData, ContractData,
    OrderRequest, CancelRequest, SubscribeRequest, HistoryRequest, BarData, LogData
)
from vnpy.trader.constant import (
    Exchange, Product, Direction, OrderType, Offset, Status, Interval, OptionType
)
from vnpy.trader.utility import load_json

# --- Shioaji 相關導入 ---
import shioaji as sj
from shioaji.constant import (
    Action as SjAction,
    Exchange as SjExchange,
    OrderType as SjOrderType,
    StockPriceType as SjStockPriceType,
    FuturesPriceType as SjFuturesPriceType,
    StockOrderLot as SjStockOrderLot,
    StockOrderCond as SjStockOrderCond,
    FuturesOCType as SjFuturesOCType,
    SecurityType as SjSecurityType,
    Status as SjStatus,
    OrderState as SjOrderState,
    QuoteType as SjQuoteType,
    QuoteVersion as SjQuoteVersion,
    Unit as SjUnit,
    OptionRight as SjOptionRight,
)
from shioaji.account import Account as SjAccount
from shioaji.contracts import Contract as SjContract, FetchStatus as SjFetchStatus, Option as sjOption
from shioaji.order import Trade as SjTrade
from shioaji.stream_data_type import TickSTKv1, TickFOPv1, BidAskSTKv1, BidAskFOPv1

# --- 時區設定 ---
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# --- 輔助函式：安全日誌格式化 ---
def _format_log_msg(msg: str) -> str:
    """
    安全地格式化日誌訊息，逸出大括號以避免 loguru 格式化錯誤。
    """
    return msg.replace("{", "{{").replace("}", "}}")


# --- 映射表 ---
PRODUCT_MAP: Dict[Product, SjSecurityType] = {
    Product.EQUITY: SjSecurityType.Stock,
    Product.FUTURES: SjSecurityType.Future,
    Product.OPTION: SjSecurityType.Option,
    Product.INDEX: SjSecurityType.Index,
    Product.ETF: SjSecurityType.Stock,
    Product.WARRANT: SjSecurityType.Stock,
}
PRODUCT_MAP_REVERSE: Dict[SjSecurityType, Product] = {v: k for k, v in PRODUCT_MAP.items()}

DIRECTION_MAP: Dict[Direction, SjAction] = {
    Direction.LONG: SjAction.Buy,
    Direction.SHORT: SjAction.Sell,
}
DIRECTION_MAP_REVERSE: Dict[SjAction, Direction] = {v: k for k, v in DIRECTION_MAP.items()}

STATUS_MAP: Dict[SjStatus, Status] = {
    SjStatus.Cancelled: Status.CANCELLED,
    SjStatus.Filled: Status.ALLTRADED,
    SjStatus.PartFilled: Status.PARTTRADED,
    SjStatus.PendingSubmit: Status.SUBMITTING,
    SjStatus.PreSubmitted: Status.SUBMITTING,      # 方案A: 將券商端預約視為提交中
    SjStatus.Submitted: Status.NOTTRADED,
    SjStatus.Failed: Status.REJECTED,
    SjStatus.Inactive: Status.SUBMITTING          # 修正: 將傳輸中的暫態視為提交中，而非拒絕
}

ORDER_TYPE_STOCK_VT2SJ: Dict[OrderType, Tuple[SjStockPriceType, SjOrderType]] = {
    OrderType.LIMIT: (SjStockPriceType.LMT, SjOrderType.ROD),
    OrderType.MARKET: (SjStockPriceType.MKT, SjOrderType.IOC),
    OrderType.FAK: (SjStockPriceType.LMT, SjOrderType.IOC),
    OrderType.FOK: (SjStockPriceType.LMT, SjOrderType.FOK),
}

ORDER_COND_MAP: Dict[Tuple[Offset, Direction], SjStockOrderCond] = {
    (Offset.OPEN, Direction.LONG): SjStockOrderCond.Cash,
    (Offset.OPEN, Direction.SHORT): SjStockOrderCond.ShortSelling,
    (Offset.CLOSETODAY, Direction.LONG): SjStockOrderCond.ShortSelling,
    (Offset.CLOSETODAY, Direction.SHORT): SjStockOrderCond.Cash,
    (Offset.CLOSE, Direction.LONG): SjStockOrderCond.ShortSelling,
    (Offset.CLOSE, Direction.SHORT): SjStockOrderCond.Cash,
    (Offset.CLOSEYESTERDAY, Direction.LONG): SjStockOrderCond.ShortSelling,
    (Offset.CLOSEYESTERDAY, Direction.SHORT): SjStockOrderCond.Cash,
}

STOCK_LOT_MAP: Dict[str, SjStockOrderLot] = {
    "Common": SjStockOrderLot.Common,
    "Odd": SjStockOrderLot.Odd,
    "IntradayOdd": SjStockOrderLot.IntradayOdd,
    "Fixing": SjStockOrderLot.Fixing,
}

ORDER_TYPE_FUTURES_VT2SJ: Dict[OrderType, Tuple[SjFuturesPriceType, SjOrderType]] = {
    OrderType.LIMIT: (SjFuturesPriceType.LMT, SjOrderType.ROD),
    OrderType.MARKET: (SjFuturesPriceType.MKP, SjOrderType.IOC),
    OrderType.FAK: (SjFuturesPriceType.LMT, SjOrderType.IOC),
    OrderType.FOK: (SjFuturesPriceType.LMT, SjOrderType.FOK),
}

FUTURES_OFFSET_MAP: Dict[Offset, SjFuturesOCType] = {
    Offset.OPEN: SjFuturesOCType.New,
    Offset.CLOSE: SjFuturesOCType.Cover,
    Offset.CLOSETODAY: SjFuturesOCType.DayTrade,
    Offset.CLOSEYESTERDAY: SjFuturesOCType.Cover,
}

# --- 輔助函數 ---
def _helper_map_stock_order_cond(
    offset: Offset,
    direction: Direction,
    kwargs: Dict[str, Any]
) -> SjStockOrderCond:
    """根據 Offset, Direction 和 kwargs 中的 "order_cond" 決定 SjStockOrderCond。"""
    kwarg_order_cond_str = kwargs.get("order_cond")
    if kwarg_order_cond_str:
        if kwarg_order_cond_str == SjStockOrderCond.MarginTrading.value:
            if direction == Direction.LONG and offset == Offset.OPEN:
                return SjStockOrderCond.MarginTrading
            elif direction == Direction.SHORT and offset in [Offset.CLOSE, Offset.CLOSEYESTERDAY, Offset.CLOSETODAY]:
                return SjStockOrderCond.MarginTrading
        elif kwarg_order_cond_str == SjStockOrderCond.ShortSelling.value:
            if direction == Direction.SHORT and offset == Offset.OPEN:
                return SjStockOrderCond.ShortSelling
            elif direction == Direction.LONG and offset in [Offset.CLOSE, Offset.CLOSEYESTERDAY, Offset.CLOSETODAY]:
                return SjStockOrderCond.ShortSelling
        elif kwarg_order_cond_str == SjStockOrderCond.Cash.value:
            return SjStockOrderCond.Cash
    return ORDER_COND_MAP.get((offset, direction), SjStockOrderCond.Cash)

# === Shioaji v1.x 列舉名稱前後相容層 ===================================
# 使用 getattr 鏈式回退，優先嘗試 v1.x 新名稱，若失敗則回退至舊名稱
STOCK_ORDER   = getattr(SjOrderState, "StockOrder", getattr(SjOrderState, "SOrder", None))
FUTURES_ORDER = getattr(SjOrderState, "FuturesOrder", getattr(SjOrderState, "FOrder", None))
STOCK_DEAL    = getattr(SjOrderState, "StockDeal",  getattr(SjOrderState, "SDeal",  None))
FUTURES_DEAL  = getattr(SjOrderState, "FuturesDeal",getattr(SjOrderState, "FDeal", None))
# ========================================================================


def _helper_map_stock_order_lot(
    input_lot_str: Optional[str]
) -> SjStockOrderLot:
    """將來自 kwargs 的字串转换为 SjStockOrderLot 枚舉。"""
    if input_lot_str and isinstance(input_lot_str, str):
        sj_lot = STOCK_LOT_MAP.get(input_lot_str)
        if sj_lot:
            return sj_lot
    return SjStockOrderLot.Common

def third_wednesday(year: int, month: int) -> date:
    cal = calendar.monthcalendar(year, month)
    day = cal[0][calendar.WEDNESDAY] or cal[1][calendar.WEDNESDAY]
    if day <= 7:
        day += 14
    return date(year, month, day)

def is_business_day(d: date) -> bool:
    return d.weekday() < 5

def next_business_day(d: date) -> date:
    nd = d + timedelta(days=1)
    while not is_business_day(nd):
        nd += timedelta(days=1)
    return nd

def is_third_wednesday(dt: Optional[date]) -> bool:
    if not dt:
        return False
    return dt.weekday() == 2 and 15 <= dt.day <= 21

def calculate_listing_date(
    delivery_year: int,
    delivery_month: int,
    is_weekly: bool = False,
    weekly_expiry: Optional[date] = None
) -> Optional[date]:
    if not is_weekly:
        ym = (delivery_year, delivery_month - 1) if delivery_month > 1 else (delivery_year - 1, 12)
        last_trade = third_wednesday(ym[0], ym[1])
        return next_business_day(last_trade)
    else:
        if not weekly_expiry:
            return None
        listing = weekly_expiry - timedelta(days=14)
        while listing.weekday() != calendar.WEDNESDAY:
            listing += timedelta(days=1)
        if listing.day <= 7:
            listing += timedelta(weeks=1)
        return listing

class ShioajiGateway(BaseGateway):
    """
    單帳號單實例的 Shioaji Gateway
    """
    default_setting = {
        "APIKey": "", "SecretKey": "", "CA路徑": "", "CA密碼": "", "身分證字號": "",
        "simulation": True, "重連次數": 3,"重連間隔(秒)": 5, "下載合約": True, "contracts_cb_timeout_sec": 60.0,
        "conflation_interval_sec": 0.25,  "定時查詢(秒)": 120, "訂閱上限": 190
    }

    def __init__(self, event_engine: EventEngine, gateway_name: str = "SHIOAJI"):
        super().__init__(event_engine, gateway_name)
        self.setting_filename = "shioaji_connect.json"
        self.sj_exchange_map_vnpy_enum: Dict[SjExchange, Exchange] = {
            SjExchange.TSE: Exchange.TWSE, SjExchange.OTC: Exchange.TOTC,
            SjExchange.TAIFEX: Exchange.TAIFEX, SjExchange.OES: Exchange.TOES
        }
        self.sj2vnpy = self.sj_exchange_map_vnpy_enum
        self.vn2sj: Dict[str, SjExchange] = {vn_enum.value: sj_enum for sj_enum, vn_enum in self.sj2vnpy.items()}
        self.loop = asyncio.new_event_loop()
        self._async_thread: Optional[threading.Thread] = None
        self.api: Optional[sj.Shioaji] = None
        self.connected: bool = False
        self.logged_in: bool = False
        self._polling_active: bool = False
        self.connect_thread: Optional[Thread] = None
        self.reconnect_attempts: int = 0
        self._reconnect_timer: Optional[threading.Timer] = None
        self.session_setting: dict = {}
        self.vnpy_account_id: str = ""
        self.query_timer_count: int = 0
        self.query_interval: int = 120
        self.orders: Dict[str, OrderData] = {}
        self.shioaji_trades: Dict[str, SjTrade] = {}
        self.shioaji_deals: Set[Tuple[str, str]] = set()
        self.positions: Dict[Tuple[str, Direction], PositionData] = {}
        self.contracts: Dict[str, ContractData] = {}
        self.subscribed: Set[str] = set()
        self.tick_cache: Dict[str, TickData] = {}
        self.order_map_lock = Lock()
        self.position_lock = Lock()
        self.contract_lock = Lock()
        self.subscribed_lock = Lock()
        self.tick_cache_lock = Lock()
        self._reconnect_lock = Lock()
        self.latest_raw_ticks: Dict[str, TickSTKv1] = {}
        self.latest_raw_bidasks: Dict[str, BidAskSTKv1] = {}
        self.latest_raw_fop_ticks: Dict[str, TickFOPv1] = {}
        self.latest_raw_fop_bidasks: Dict[str, BidAskFOPv1] = {}
        self.raw_data_cache_lock = threading.Lock()
        self.pending_conflation_processing: Set[str] = set()
        self.pending_conflation_lock = threading.Lock()
        self.conflation_trigger = asyncio.Event()
        self._conflation_task: Optional[asyncio.Task] = None
        self.fetched_security_types: Set[SjSecurityType] = set()
        self.expected_security_types: Set[SjSecurityType] = {SjSecurityType.Index, SjSecurityType.Stock, SjSecurityType.Future, SjSecurityType.Option}
        self._contracts_processed_flag: bool = False
        self._processing_contracts_lock = threading.Lock()
        self._all_contracts_fetched_event = threading.Event()
        self._polling_thread: Optional[threading.Thread] = None
        self.stock_account: Optional[SjAccount] = None
        self.futopt_account: Optional[SjAccount] = None
        

    def connect(self, setting: dict):
        if self.connected: return
        self.session_setting = load_json(self.setting_filename)
        if not self.session_setting:
            self.write_log(f"無法從 {self.setting_filename} 載入設定檔。")
            return
        self.query_interval = int(self.session_setting.get("定時查詢(秒)", 120))
        self.write_log("開始連接 Shioaji API...")
        self._start_async_loop_thread()
        self.connect_thread = Thread(target=self._connect_worker, daemon=True)
        self.connect_thread.start()
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)

    def _connect_worker(self):
        # 【修正】為整個連線過程加上最外層的 try/except 
        try:
            self.api = sj.Shioaji(simulation=self.session_setting.get("simulation", True))
            # 【修正】確保 login 呼叫被捕獲
            self.api.login(
                api_key=self.session_setting["APIKey"], secret_key=self.session_setting["SecretKey"],
                fetch_contract=False, subscribe_trade=True, contracts_timeout=0
            )
            for acc in self.api.list_accounts(): self.api.set_default_account(acc)
            self.write_log("Shioaji API 登入成功。")
            self._fetch_and_process_contracts()
            if not self.session_setting.get("simulation"):
                self.write_log("啟用憑證 (CA)...")
                # 【修正】為 activate_ca 呼叫加上 try/except 
                try:
                    self.api.activate_ca(
                        ca_path=self.session_setting["CA路徑"], ca_passwd=self.session_setting["CA密碼"],
                        person_id=self.session_setting["身分證字號"],
                    )
                    self.write_log("憑證 (CA) 啟用成功。")
                except Exception as e_ca: self.write_log(_format_log_msg(f"憑證 (CA) 啟用失敗: {e_ca}"))

            self._set_callbacks()
            self.connected = True
            self.logged_in = True
            self.reconnect_attempts = 0  # 成功連接後重置重連計數器
            self.write_log("Gateway 連線成功。")

            self.write_log("正在執行初始狀態同步...")
            self.query_all()
            self._sync_orders()
            self.write_log("狀態同步完成。")
            
            # 【修正】啟動高可靠性的訂單狀態輪詢線程
            self._start_polling_thread()
            self._resubscribe_all()

        except Exception as e:
            self.write_log(_format_log_msg(f"Gateway 連線失敗: {e}\n{traceback.format_exc()}"))
            self._handle_disconnect()

    def _fetch_and_process_contracts(self):
        self.write_log("開始下載合約資料...")
        self._all_contracts_fetched_event.clear()
        self.fetched_security_types.clear()
        self._contracts_processed_flag = False
        contracts_cb_timeout = float(self.session_setting.get("contracts_cb_timeout_sec", 60.0))
        # 【修正】為 fetch_contracts 呼叫增加防禦性 
        try:
            self.api.fetch_contracts(
                contract_download=self.session_setting.get("下載合約", True),
                contracts_timeout=0, contracts_cb=self._contracts_cb
            )
            if not self._all_contracts_fetched_event.wait(timeout=contracts_cb_timeout):
                self.write_log("等待合約下載回調超時。")
            if not self._contracts_processed_flag:
                self.write_log("合約回調完成，開始處理合約資料。")
                self._process_contracts()
                self._contracts_processed_flag = True
        except Exception as e:
            self.write_log(_format_log_msg(f"下載合約過程中發生錯誤: {e}"))
    
    def _sync_orders(self):
        """
        [已修正] 對每一個存在的帳戶個別更新狀態，然後再獲取總成交列表。
        """
        self.write_log("正在同步當日委託狀態...")
        try:
            # 根據存在的帳戶物件，逐一更新狀態
            if self.stock_account:
                self.api.update_status(account=self.stock_account)
                self.write_log("已更新證券帳戶狀態。")
            if self.futopt_account:
                self.api.update_status(account=self.futopt_account)
                self.write_log("已更新期貨帳戶狀態。")

            # 獲取更新後的所有成交
            sj_trades: List[SjTrade] = self.api.list_trades()
            self.write_log(f"獲取到 {len(sj_trades)} 筆當日成交記錄。")
            
            with self.order_map_lock:
                for trade in sj_trades:
                    if not (trade.order and trade.status and trade.order.seqno):
                        continue
                    
                    vnpy_trade = TradeData(
                        gateway_name=self.gateway_name,
                        symbol=trade.contract.code,
                        exchange=self.sj2vnpy.get(trade.contract.exchange),
                        orderid=f"{self.gateway_name}.{trade.order.seqno}",
                        tradeid=f"{self.gateway_name}.{trade.seqno}",
                        direction=DIRECTION_MAP_REVERSE[trade.order.action],
                        price=float(trade.price),
                        volume=float(trade.quantity),
                        datetime=trade.ts.replace(tzinfo=TAIPEI_TZ)
                    )
                    super().on_trade(vnpy_trade)
                    
        except Exception as e:
            self.write_log(_format_log_msg(f"同步當日委託失敗: {e}"))
                
    def _start_async_loop_thread(self):
        if not self._async_thread or not self._async_thread.is_alive():
            self._async_thread = threading.Thread(target=self._run_async_loop, daemon=True)
            self._async_thread.start()

    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self._conflation_task = self.loop.create_task(self._conflation_processor())
            self.loop.run_forever()
        except Exception as e:
            self.write_log(_format_log_msg(f"Asyncio 迴圈錯誤: {e}"))
        finally:
            if self._conflation_task and not self._conflation_task.done():
                self._conflation_task.cancel()

            async def gather_tasks():
                if self._conflation_task:
                    await asyncio.gather(self._conflation_task, return_exceptions=True)

            if self.loop.is_running():
                self.loop.run_until_complete(gather_tasks())
                self.loop.stop()
            self.loop.close()
            self.write_log("Asyncio 迴圈已關閉。")

    def _on_order_update(self, order_state: SjOrderState, msg: dict):
        """
        [已修正] 處理即時訂單與成交回報，使用版本相容的列舉名稱。
        """
        try:
            # 使用新的版本相容常數
            if order_state in (STOCK_ORDER, FUTURES_ORDER):
                self._process_order_status_update(msg)
            elif order_state in (STOCK_DEAL, FUTURES_DEAL):
                self._process_deal_update(msg)
            else:
                self.write_log(f"收到未知 OrderState: {order_state}")
        except Exception as e:
            self.write_log(_format_log_msg(
                f"處理回調 ({order_state}) 時發生錯誤: {e}\n資料: {msg}\n{traceback.format_exc()}"
            ))

    def _process_order_status_update(self, msg: dict):
        """輔助函式，專門處理訂單狀態更新。"""
        status_info = msg.get("status")
        if not status_info or not (order_id := status_info.get("id")):
            return
            
        vt_orderid = f"{self.gateway_name}.{order_id}"
        with self.order_map_lock:
            order = self.orders.get(vt_orderid)
            if not order:
                return

            new_status = STATUS_MAP.get(SjStatus(status_info.get("status")), order.status)
            if order.status != new_status:
                order_copy = copy.copy(order)
                order_copy.status = new_status
                order_copy.reference = status_info.get("msg", order.reference)
                self.orders[vt_orderid] = order_copy
                super().on_order(order_copy)

    def _process_deal_update(self, msg: dict):
        """輔助函式，專門處理成交回報。"""
        # 使用 .get() 進行防禦性取值 
        order_id = msg.get("ordno")
        trade_id = msg.get("trade_id")
        deal_price = msg.get("price")
        deal_qty = msg.get("quantity")

        if not all([order_id, trade_id, deal_price, deal_qty]):
            self.write_log(f"成交回報缺少必要欄位: {msg}")
            return

        # 組合唯一成交 ID，避免重複處理
        unique_deal_id = (order_id, trade_id)
        if unique_deal_id in self.shioaji_deals:
            return
        self.shioaji_deals.add(unique_deal_id)

        vt_orderid = f"{self.gateway_name}.{order_id}"
        with self.order_map_lock:
            order = self.orders.get(vt_orderid)
            if not order:
                return

            order_copy = copy.copy(order)
            order_copy.traded += float(deal_qty)
            order_copy.traded = min(order_copy.traded, order_copy.volume) # 確保不超過總量

            if order_copy.traded == order_copy.volume:
                order_copy.status = Status.ALLTRADED
            else:
                order_copy.status = Status.PARTTRADED
            
            self.orders[vt_orderid] = order_copy
            super().on_order(order_copy)

            trade = TradeData(
                gateway_name=self.gateway_name,
                symbol=order.symbol,
                exchange=order.exchange,
                orderid=vt_orderid,
                tradeid=f"{self.gateway_name}.{trade_id}",
                direction=order.direction,
                offset=order.offset,
                price=float(deal_price),
                volume=float(deal_qty),
                datetime=datetime.now(TAIPEI_TZ)
            )
            super().on_trade(trade)

    def _set_callbacks(self):
        # 【修正】確保所有需要的 Callback 都已註冊 
        self.api.quote.set_on_tick_stk_v1_callback(self._on_tick_stk)
        self.api.quote.set_on_tick_fop_v1_callback(self._on_tick_fop)
        self.api.quote.set_on_bidask_stk_v1_callback(self._on_bidask_stk)
        self.api.quote.set_on_bidask_fop_v1_callback(self._on_bidask_fop)
        self.api.set_order_callback(self._on_order_update)
        # 【修正】設定連線事件回調以監控連線狀態 
        self.api.quote.set_event_callback(self._on_quote_event)
        if hasattr(self.api, "set_session_down_callback"):
            self.api.set_session_down_callback(self._on_session_down)
        self.write_log("Shioaji 回調函數設定完成。")

    def _on_quote_event(self, resp_code: int, event_code: int, info: str, event: str):
        """【已修正】處理行情連線事件，正確判斷成功與錯誤碼。"""
        log_msg = f"行情事件: resp_code={resp_code}, event_code={event_code}, info='{info}', event='{event}'"
        
        # 【修正】將 resp_code 200 (OK) 視為成功，而非錯誤
        if resp_code == 0 or resp_code == 200:
            self.write_log(log_msg)
        else:
            self.write_log(f"行情連線錯誤: {log_msg}", level="ERROR")
            # 可在此處根據特定 event_code 觸發重連或其他錯誤處理
            if event_code in [4, 8]: # 假設 4, 8 為斷線代碼
                self._handle_disconnect()

    def _handle_disconnect(self):
        if not self.connected and not self.logged_in:
            return
        self.connected = False
        self.logged_in = False
        self.write_log("Gateway 已斷線。")
        reconnect_limit = int(self.session_setting.get("重連次數", 3))
        if reconnect_limit > 0:
            self._start_reconnect()
        else:
            self.write_log("未設定重連，Gateway 將保持斷線狀態。")
            
    def _start_reconnect(self):
        with self._reconnect_lock:
            if self.reconnect_attempts < int(self.session_setting.get("重連次數", 3)):
                self.reconnect_attempts += 1
                self.write_log(f"準備進行第 {self.reconnect_attempts} 次重連...")
                reconnect_interval = int(self.session_setting.get("重連間隔(秒)", 5))
                self._reconnect_timer = threading.Timer(reconnect_interval, self.connect, args=[self.session_setting])
                self._reconnect_timer.start()
            else:
                self.write_log("已達最大重連次數，停止重連。")
                
    def _on_session_down(self, *args):
        self.write_log("偵測到 Session 中斷。")
        self._handle_disconnect()
        
    def process_timer_event(self, event: Any):
        self.query_timer_count += 1
        if self.query_timer_count >= self.query_interval > 0:
            self.query_timer_count = 0
            self.query_all()
            
    def query_all(self):
        self.query_account()
        self.query_position()
        
    def _resubscribe_all(self):
        with self.subscribed_lock:
            subscribed_copy = self.subscribed.copy()
        self.write_log(f"連線恢復，嘗試重新訂閱 {len(subscribed_copy)} 個商品...")
        for vt_symbol in subscribed_copy:
            try:
                symbol, exchange_str = vt_symbol.split('.')
                req = SubscribeRequest(symbol=symbol, exchange=Exchange(exchange_str))
                self.subscribe(req)
            except Exception as e:
                self.write_log(_format_log_msg(f"重新訂閱 {vt_symbol} 失敗: {e}"))

    def subscribe(self, req: SubscribeRequest):
        vt_symbol = req.vt_symbol
        if not self._check_connection():
            self.write_log(f"訂閱失敗 (未連線): {vt_symbol}")
            return
            
        # 【修正】安全查找合約 
        contract = self.find_sj_contract(req.symbol, req.exchange.value)
        if not contract:
            self.write_log(f"訂閱失敗 (找不到合約): {vt_symbol}")
            return
            
        # 【修正】增加訂閱上限檢查 
        with self.subscribed_lock:
            if vt_symbol in self.subscribed:
                self.write_log(f"重複訂閱，已略過: {vt_symbol}")
                return
            
            subscription_limit = self.session_setting.get("訂閱上限", 190)
            if len(self.subscribed) >= subscription_limit:
                self.write_log(f"訂閱失敗: 已達 {subscription_limit} 檔上限: {vt_symbol}")
                return

        # 【修正】為 subscribe 呼叫增加 try/except 和明確參數 
        try:
            self.api.quote.subscribe(contract, quote_type=SjQuoteType.Tick, version=SjQuoteVersion.v1)
            self.api.quote.subscribe(contract, quote_type=SjQuoteType.BidAsk, version=SjQuoteVersion.v1)
            with self.subscribed_lock:
                self.subscribed.add(vt_symbol)
            self.write_log(f"成功發送訂閱請求: {vt_symbol}")
        except Exception as e:
            self.write_log(_format_log_msg(f"訂閱 API 錯誤: {vt_symbol}, {e}"))

    def unsubscribe(self, req: SubscribeRequest):
        vt_symbol = req.vt_symbol
        if not self._check_connection():
            return
        with self.subscribed_lock:
            if vt_symbol not in self.subscribed:
                return
        contract = self.find_sj_contract(req.symbol, req.exchange.value)
        if not contract:
            return
        try:
            self.api.quote.unsubscribe(contract, quote_type=SjQuoteType.Tick, version=SjQuoteVersion.v1)
            self.api.quote.unsubscribe(contract, quote_type=SjQuoteType.BidAsk, version=SjQuoteVersion.v1)
            with self.subscribed_lock:
                self.subscribed.discard(vt_symbol)
            self.write_log(f"成功發送取消訂閱請求: {vt_symbol}")
        except Exception as e:
            self.write_log(_format_log_msg(f"取消訂閱 API 錯誤: {vt_symbol}, {e}"))

    def send_order(self, req: OrderRequest) -> str:
        # 【修正】保留下單前的連線與 CA 檢查 
        if not self._check_connection(check_ca=True):
            order = req.create_order_data(f"REJ_CONN_{int(time.time_ns())}", self.gateway_name)
            order.status, order.reference = Status.REJECTED, "Gateway 未連線或 CA 未簽署"
            super().on_order(order)
            return order.vt_orderid

        sj_contract = self.find_sj_contract(req.symbol, req.exchange.value)
        if not sj_contract:
            order = req.create_order_data(f"REJ_NO_CONTRACT_{int(time.time_ns())}", self.gateway_name)
            order.status, order.reference = Status.REJECTED, f"找不到合約: {req.vt_symbol}"
            super().on_order(order)
            return order.vt_orderid

        product = PRODUCT_MAP_REVERSE.get(sj_contract.security_type)
        extra = getattr(req, "extra", None) or {}
        order_args: Optional[Dict[str, Any]] = None

        if product == Product.EQUITY:
            order_args = self._prepare_stock_order_args(req, **extra)
        else:
            order_args = self._prepare_futures_order_args(req, **extra)

        if not order_args:
            err_msg = f"訂單參數準備失敗，不支援的訂單類型: {req.type.value}"
            order = req.create_order_data(f"REJ_ARGS_{int(time.time_ns())}", self.gateway_name)
            order.status, order.reference = Status.REJECTED, err_msg
            super().on_order(order)
            return order.vt_orderid

        # 【修正】保留下單前的本地訂單生成 
        temp_orderid = f"TEMP_{int(time.time_ns())}"
        order = req.create_order_data(temp_orderid, self.gateway_name)
        order.status = Status.SUBMITTING
        super().on_order(order)

        # 【修正】保留 place_order 的 try/except 
        try:
            sj_order = self.api.Order(**order_args)
            trade_resp: SjTrade = self.api.place_order(sj_contract, sj_order)
            
            # 從 Shioaji 回報中獲取真實的委託序號
            order_id = trade_resp.order.seqno
            if not order_id: # 防禦性檢查
                 raise ValueError("place_order 未返回有效的委託序號(seqno)")

            vt_orderid = f"{self.gateway_name}.{order_id}"
            
            # 更新訂單的真實 ID
            order.orderid = vt_orderid
            
            with self.order_map_lock:
                self.orders[vt_orderid] = order
                # 【修正】儲存 Trade 物件以供後續取消或查詢使用 
                self.shioaji_trades[order_id] = trade_resp
            
            # 推送帶有真實 ID 的訂單更新
            super().on_order(copy.copy(order))
            
            if trade_resp.status.status in [SjStatus.Failed, SjStatus.Cancelled]:
                self._on_order_update(SjOrderState.StockOrder, {"status": trade_resp.status.__dict__})

            return vt_orderid

        except Exception as e:
            self.write_log(_format_log_msg(f"下單 API 呼叫失敗: {e}"))
            # 【修正】API呼叫失敗時，將原訂單更新為拒絕狀態 
            order.status, order.reference = Status.REJECTED, f"API Error: {e}"
            super().on_order(order)
            return order.vt_orderid

    def cancel_order(self, req: CancelRequest):
        if not self._check_connection(check_ca=True):
            return

        parts = req.orderid.split('.')
        if not (len(parts) >= 2 and req.orderid.startswith(self.gateway_name)):
            self.write_log(f"撤單失敗: OrderID {req.orderid} 格式不符。")
            return
        
        shioaji_seqno = parts[-1]

        with self.order_map_lock:
            sj_trade_to_cancel = self.shioaji_trades.get(shioaji_seqno)
            vnpy_order = self.orders.get(req.orderid)
        
        if not vnpy_order or not sj_trade_to_cancel:
            log_msg = f"撤單失敗: 找不到訂單 {req.orderid} 的內部記錄。"
            self.write_log(_format_log_msg(log_msg))
            return
            
        if not vnpy_order.is_active():
            self.write_log(f"訂單 {req.orderid} 非活躍狀態 ({vnpy_order.status.value})，無需撤銷。")
            return
            
        # 【修正】為 cancel_order 呼叫加上 try/except 
        try:
            self.api.cancel_order(sj_trade_to_cancel)
            self.write_log(f"已發送撤單請求: {req.orderid}")
        except Exception as e:
            self.write_log(_format_log_msg(f"撤單 API 呼叫失敗 ({req.orderid}): {e}"))

    def query_account(self):
            """[已簡化] 查詢預設帳戶的資金情況。"""
            if not self._check_connection():
                return
            try:
                # Shioaji 會根據 set_default_account 的帳戶類型決定 account_balance() 的查詢目標
                if self.api.stock_account or self.api.futopt_account:
                    # 查詢證券帳戶餘額
                    if balance_info := self.api.account_balance():
                        acc_data = AccountData(
                            accountid=self.vnpy_account_id,
                            balance=float(balance_info.acc_balance),
                            frozen=0.0, gateway_name=self.gateway_name
                        )
                        super().on_account(acc_data)
                    
                    # 查詢期貨帳戶保證金
                    if margin_info := self.api.margin():
                        acc_data = AccountData(
                            accountid=self.vnpy_account_id,
                            balance=float(margin_info.equity_amount),
                            frozen=float(margin_info.initial_margin + margin_info.order_margin_premium),
                            gateway_name=self.gateway_name
                        )
                        super().on_account(acc_data)

            except Exception as e:
                self.write_log(_format_log_msg(f"查詢帳戶餘額失敗: {e}"))


    def query_position(self):
        """[已簡化] 查詢預設帳戶的持倉情況。"""
        if not self._check_connection():
            return
            
        received_position_keys: Set[Tuple[str, Direction]] = set()
        with self.position_lock:
            previous_position_keys = set(self.positions.keys())
        try:
            # 使用 Shioaji API 在設定預設帳戶後提供的屬性
            if acc := (self.api.stock_account or self.api.futopt_account):
                positions = self.api.list_positions(acc)
                self._process_api_positions(positions, received_position_keys)

            # 清理已平倉的部位
            with self.position_lock:
                keys_to_zero_out = previous_position_keys - received_position_keys
                for vt_symbol_key, direction_key in keys_to_zero_out:
                    if old_pos_data := self.positions.pop((vt_symbol_key, direction_key), None):
                        zeroed_pos = PositionData(
                            gateway_name=self.gateway_name, symbol=old_pos_data.symbol,
                            exchange=old_pos_data.exchange, direction=direction_key,
                            volume=0, yd_volume=0, frozen=0, price=old_pos_data.price, pnl=0.0,
                        )
                        super().on_position(zeroed_pos)
        except Exception as e:
            self.write_log(_format_log_msg(f"查詢持倉失敗: {e}"))
                
    def query_history(self, req: HistoryRequest) -> List[BarData]:
        if not self._check_connection() or not (sj_contract := self.find_sj_contract(req.symbol, req.exchange.value)):
            return []
        if req.interval != Interval.MINUTE:
            self.write_log(f"不支援的K棒間隔: {req.interval.value}")
            return []
        bars: List[BarData] = []
        try:
            kbars = self.api.kbars(contract=sj_contract, start=req.start.strftime("%Y-%m-%d"), end=req.end.strftime("%Y-%m-%d"))
            df = pd.DataFrame({**kbars})
            if df.empty: return []
            df['datetime'] = pd.to_datetime(df['ts'], unit='ns').dt.tz_localize('Asia/Taipei')
            df.dropna(inplace=True)
            for _, row in df.iterrows():
                if not (req.start <= (bar_time := row['datetime'].to_pydatetime()) < req.end):
                    continue
                bars.append(BarData(
                    gateway_name=self.gateway_name, symbol=req.symbol, exchange=req.exchange,
                    datetime=bar_time + timedelta(minutes=1), interval=req.interval, volume=float(row["Volume"]),
                    open_price=float(row["Open"]), high_price=float(row["High"]),
                    low_price=float(row["Low"]), close_price=float(row["Close"]),
                    turnover=float(row.get("Amount", 0.0)), open_interest=0,
                ))
            return bars
        except Exception as e:
            self.write_log(_format_log_msg(f"查詢歷史K棒失敗: {e}"))
            return []
            
    def close(self):
        if not self.connected: return
        if self._reconnect_timer: self._reconnect_timer.cancel()
        self.event_engine.unregister(EVENT_TIMER, self.process_timer_event)
        
        # 【修正】增加資源清理 
        self.connected, self.logged_in = False, False # 設置旗標停止輪詢
        if self._polling_thread and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=2.0)
            
        if self.api: self.api.logout()

        if self.loop.is_running(): self.loop.call_soon_threadsafe(self.loop.stop)
        if self._async_thread and self._async_thread.is_alive(): self._async_thread.join(timeout=5.0)
        self.write_log("Gateway 已關閉。")

    def _check_connection(self, check_ca: bool = False) -> bool:
        if not self.connected or not self.logged_in or not self.api:
            self.write_log("API 未連線或未登入。")
            return False
        if check_ca and not self.session_setting.get("simulation"):
            if not getattr(self.api.ca, "is_activated", False):
                self.write_log("CA 憑證未啟用。")
                return False
        return True

    # --- 以下為內部輔助/回調方法 ---
    def _contracts_cb(self, security_type: SjSecurityType, contracts: List[Dict] = None):
        """【修正】處理合約下載回調，並觸發處理事件。"""
        self.write_log(f"已下載 {security_type.value} 合約資料。")
        self.fetched_security_types.add(security_type)
        if self.fetched_security_types.issuperset(self.expected_security_types):
            self._all_contracts_fetched_event.set()

    def _process_contracts(self):
        """【修正】保留合約處理邏輯，確保前端能獲取合約資訊 """
        self.write_log("開始處理合約...")
        parsed_contracts_tmp: Dict[str, ContractData] = {}
        if futures_root := getattr(self.api.Contracts, 'Futures', None):
            if hasattr(futures_root, '_code2contract'):
                for code, sjc in futures_root._code2contract.items():
                    if not (vn_exchange := self.sj2vnpy.get(sjc.exchange)): continue
                    size, tick = self._size_pricetick_generic(sjc)
                    parsed_contracts_tmp[f"{code}.{vn_exchange.value}"] = ContractData(
                        gateway_name=self.gateway_name, symbol=code, exchange=vn_exchange, name=sjc.name,
                        product=Product.FUTURES, size=size, pricetick=tick,
                        min_volume=1, net_position=True, history_data=True)
        if options_root := getattr(self.api.Contracts, 'Options', None):
            if hasattr(options_root, '_code2contract'):
                for code, sjc in options_root._code2contract.items():
                    if isinstance(sjc, sjOption) and (cd := self._parse_option(sjc)):
                        parsed_contracts_tmp[cd.vt_symbol] = cd
        if stocks_root := getattr(self.api.Contracts, 'Stocks', None):
            for sj_ex_enum, vn_ex_enum in self.sj2vnpy.items():
                if sj_ex_enum not in [SjExchange.TSE, SjExchange.OTC, SjExchange.OES]: continue
                if exchange_cat := getattr(stocks_root, sj_ex_enum.value, None):
                    if hasattr(exchange_cat, '_code2contract'):
                        for code, sjc in exchange_cat._code2contract.items():
                            product_type = Product.ETF if "ETF" in sjc.name.upper() else Product.EQUITY
                            size, tick = self._size_pricetick_generic(sjc)
                            parsed_contracts_tmp[f"{code}.{vn_ex_enum.value}"] = ContractData(
                                gateway_name=self.gateway_name, symbol=code, exchange=vn_ex_enum, name=sjc.name,
                                product=product_type, size=size, pricetick=tick,
                                min_volume=1, net_position=False, history_data=True)
        if mxf_underlying := self._find_mxf_contract()[0]:
            for cd in parsed_contracts_tmp.values():
                if cd.product == Product.OPTION and not cd.option_underlying and "W" in cd.option_portfolio:
                    cd.option_underlying = mxf_underlying
        with self.contract_lock:
            self.contracts.clear()
            self.contracts.update(parsed_contracts_tmp)
        for contract in self.contracts.values():
            super().on_contract(contract)
        self.write_log(f"合約處理完成，共載入 {len(self.contracts)} 筆合約。")

    def _on_tick_stk(self, exchange: SjExchange, tick: TickSTKv1):
        # 【修正】為回調函數加上最外層的 try/except 
        try:
            if not (vn_exchange_val := getattr(self.sj2vnpy.get(exchange), 'value', None)): return
            vt_symbol = f"{tick.code}.{vn_exchange_val}"
            with self.raw_data_cache_lock: self.latest_raw_ticks[vt_symbol] = tick
            with self.pending_conflation_lock: self.pending_conflation_processing.add(vt_symbol)
            if self.loop.is_running(): self.loop.call_soon_threadsafe(self.conflation_trigger.set)
        except Exception as e:
            self.write_log(_format_log_msg(f"處理股票 Tick 時發生錯誤: {e}, Data: {tick}"))

    def _on_bidask_stk(self, exchange: SjExchange, bidask: BidAskSTKv1):
        # 【修正】為回調函數加上最外層的 try/except 
        try:
            if not (vn_exchange_val := getattr(self.sj2vnpy.get(exchange), 'value', None)): return
            vt_symbol = f"{bidask.code}.{vn_exchange_val}"
            with self.raw_data_cache_lock: self.latest_raw_bidasks[vt_symbol] = bidask
            with self.pending_conflation_lock: self.pending_conflation_processing.add(vt_symbol)
            if self.loop.is_running(): self.loop.call_soon_threadsafe(self.conflation_trigger.set)
        except Exception as e:
            self.write_log(_format_log_msg(f"處理股票 BidAsk 時發生錯誤: {e}, Data: {bidask}"))
        
    def _on_tick_fop(self, exchange: SjExchange, tick: TickFOPv1):
        # 【修正】為回調函數加上最外層的 try/except 
        try:
            if not (vn_exchange_val := getattr(self.sj2vnpy.get(exchange), 'value', None)): return
            vt_symbol = f"{tick.code}.{vn_exchange_val}"
            with self.raw_data_cache_lock: self.latest_raw_fop_ticks[vt_symbol] = tick
            with self.pending_conflation_lock: self.pending_conflation_processing.add(vt_symbol)
            if self.loop.is_running(): self.loop.call_soon_threadsafe(self.conflation_trigger.set)
        except Exception as e:
            self.write_log(_format_log_msg(f"處理期權 Tick 時發生錯誤: {e}, Data: {tick}"))

    def _on_bidask_fop(self, exchange: SjExchange, bidask: BidAskFOPv1):
        # 【修正】為回調函數加上最外層的 try/except 
        try:
            if not (vn_exchange_val := getattr(self.sj2vnpy.get(exchange), 'value', None)): return
            vt_symbol = f"{bidask.code}.{vn_exchange_val}"
            with self.raw_data_cache_lock: self.latest_raw_fop_bidasks[vt_symbol] = bidask
            with self.pending_conflation_lock: self.pending_conflation_processing.add(vt_symbol)
            if self.loop.is_running(): self.loop.call_soon_threadsafe(self.conflation_trigger.set)
        except Exception as e:
            self.write_log(_format_log_msg(f"處理期權 BidAsk 時發生錯誤: {e}, Data: {bidask}"))
        

    async def _conflation_processor(self):
        conflation_interval = float(self.session_setting.get("conflation_interval_sec", 0.050))
        while True:
            try:
                await asyncio.wait_for(self.conflation_trigger.wait(), timeout=conflation_interval)
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                self.write_log(_format_log_msg(f"行情聚合器錯誤: {e}"))
                await asyncio.sleep(1)
                continue
            if self.conflation_trigger.is_set():
                self.conflation_trigger.clear()
            with self.pending_conflation_lock:
                if not self.pending_conflation_processing: continue
                symbols_to_process = self.pending_conflation_processing.copy()
                self.pending_conflation_processing.clear()
            if processing_tasks := [self._process_conflated_data_async(vt) for vt in symbols_to_process]:
                await asyncio.gather(*processing_tasks, return_exceptions=True)

    async def _process_conflated_data_async(self, vt_symbol: str):
        try:
            with self.raw_data_cache_lock:
                raw_tick = self.latest_raw_ticks.pop(vt_symbol, None)
                raw_bidask = self.latest_raw_bidasks.pop(vt_symbol, None)
                raw_fop_tick = self.latest_raw_fop_ticks.pop(vt_symbol, None)
                raw_fop_bidask = self.latest_raw_fop_bidasks.pop(vt_symbol, None)

            if not any([raw_tick, raw_bidask, raw_fop_tick, raw_fop_bidask]): return
            with self.tick_cache_lock:
                vnpy_tick = self.tick_cache.get(vt_symbol)
                if not vnpy_tick:
                    symbol, exchange_str = vt_symbol.split('.')
                    vnpy_tick = TickData(gateway_name=self.gateway_name, symbol=symbol, exchange=Exchange(exchange_str), datetime=datetime.now(TAIPEI_TZ))
            final_dt = vnpy_tick.datetime
            if raw_tick:
                final_dt = max(final_dt, raw_tick.datetime.replace(tzinfo=TAIPEI_TZ))
                vnpy_tick.last_price, vnpy_tick.last_volume, vnpy_tick.volume, vnpy_tick.turnover = float(raw_tick.close), float(raw_tick.volume), float(raw_tick.total_volume), float(getattr(raw_tick, 'total_amount', 0.0))
                vnpy_tick.open_price, vnpy_tick.high_price, vnpy_tick.low_price = float(raw_tick.open), float(raw_tick.high), float(raw_tick.low)
                if hasattr(raw_tick, 'price_chg') and raw_tick.price_chg is not None:
                    vnpy_tick.pre_close = float(raw_tick.close - raw_tick.price_chg)
            if raw_bidask:
                final_dt = max(final_dt, raw_bidask.datetime.replace(tzinfo=TAIPEI_TZ))
                for i in range(5):
                    setattr(vnpy_tick, f"bid_price_{i+1}", float(raw_bidask.bid_price[i]) if i < len(raw_bidask.bid_price) else 0.0)
                    setattr(vnpy_tick, f"bid_volume_{i+1}", float(raw_bidask.bid_volume[i]) if i < len(raw_bidask.bid_volume) else 0.0)
                    setattr(vnpy_tick, f"ask_price_{i+1}", float(raw_bidask.ask_price[i]) if i < len(raw_bidask.ask_price) else 0.0)
                    setattr(vnpy_tick, f"ask_volume_{i+1}", float(raw_bidask.ask_volume[i]) if i < len(raw_bidask.ask_volume) else 0.0)
            if raw_fop_tick:
                final_dt = max(final_dt, raw_fop_tick.datetime.replace(tzinfo=TAIPEI_TZ))
                vnpy_tick.last_price, vnpy_tick.last_volume, vnpy_tick.volume = float(raw_fop_tick.close), float(raw_fop_tick.volume), float(raw_fop_tick.total_volume)
                vnpy_tick.open_price, vnpy_tick.high_price, vnpy_tick.low_price, vnpy_tick.open_interest = float(raw_fop_tick.open), float(raw_fop_tick.high), float(raw_fop_tick.low), float(getattr(raw_fop_tick, 'open_interest', 0.0))
                if hasattr(raw_fop_tick, 'price_chg') and raw_fop_tick.price_chg is not None:
                    vnpy_tick.pre_close = float(raw_fop_tick.close - raw_fop_tick.price_chg)
            if raw_fop_bidask:
                final_dt = max(final_dt, raw_fop_bidask.datetime.replace(tzinfo=TAIPEI_TZ))
                for i in range(5):
                    setattr(vnpy_tick, f"bid_price_{i+1}", float(raw_fop_bidask.bid_price[i]) if i < len(raw_fop_bidask.bid_price) else 0.0)
                    setattr(vnpy_tick, f"bid_volume_{i+1}", float(raw_fop_bidask.bid_volume[i]) if i < len(raw_fop_bidask.bid_volume) else 0.0)
                    setattr(vnpy_tick, f"ask_price_{i+1}", float(raw_fop_bidask.ask_price[i]) if i < len(raw_fop_bidask.ask_price) else 0.0)
                    setattr(vnpy_tick, f"ask_volume_{i+1}", float(raw_fop_bidask.ask_volume[i]) if i < len(raw_fop_bidask.ask_volume) else 0.0)
            vnpy_tick.datetime, vnpy_tick.localtime = final_dt, datetime.now()
            with self.tick_cache_lock: self.tick_cache[vt_symbol] = vnpy_tick
            super().on_tick(copy.copy(vnpy_tick))
        except Exception as e:
            self.write_log(_format_log_msg(f"處理聚合行情錯誤 ({vt_symbol}): {e}"))

    def _start_polling_thread(self):
        """【已修正】啟動訂單狀態輪詢線程。"""
        # 【修正】更改判斷條件，先檢查是否為 None，再檢查 is_alive()
        if self._polling_thread is None or not self._polling_thread.is_alive():
            self._polling_thread = Thread(target=self._run_polling_loop, daemon=True)
            self._polling_thread.start()

    def _run_polling_loop(self):
        """
        [已修正] 移除不支援的 active_trades 參數，改以帳戶為單位進行輪詢。
        """
        self.write_log("訂單狀態輪詢校對線程已啟動。")
        reconciliation_interval = 30
        while self._polling_active: # 使用旗標控制迴圈
            try:
                if self.stock_account:
                    self.api.update_status(account=self.stock_account)
                if self.futopt_account:
                    self.api.update_status(account=self.futopt_account)
                self.write_log("已完成帳戶層級 status 輪詢。")
            except Exception as e:
                self.write_log(_format_log_msg(f"訂單輪詢校對時發生錯誤: {e}"))
            time.sleep(reconciliation_interval)
        self.write_log("訂單狀態輪詢校對線程已停止。")

    def _process_api_positions(self, api_positions, received_keys):
        """【修正】處理持倉回報，並增加例外處理 """
        if not api_positions: return
        for pos in api_positions:
            try:
                if not (sj_contract := self.find_sj_contract(pos.code)): continue
                if not (vn_exchange := self.sj2vnpy.get(sj_contract.exchange)): continue
                if not (vn_direction := DIRECTION_MAP_REVERSE.get(pos.direction)): continue
                position = PositionData(
                    gateway_name=self.gateway_name, symbol=pos.code, exchange=vn_exchange, direction=vn_direction,
                    volume=float(pos.quantity), yd_volume=float(getattr(pos, 'yd_quantity', 0)),
                    frozen=max(0.0, float(pos.quantity) - float(getattr(pos, 'yd_quantity', pos.quantity))),
                    price=float(pos.price), pnl=float(getattr(pos, 'pnl', 0.0))
                )
                pos_key = (position.vt_symbol, position.direction)
                received_keys.add(pos_key)
                with self.position_lock: self.positions[pos_key] = position
                super().on_position(position)
            except Exception as e:
                self.write_log(_format_log_msg(f"處理 API 持倉資料錯誤: {e}, Data: {pos}"))

    def find_sj_contract(self, symbol: str, vn_exchange_str: Optional[str] = None) -> Optional[SjContract]:
        if not self.api or not hasattr(self.api, 'Contracts') or self.api.Contracts.status != SjFetchStatus.Fetched:
            return None
        target_contract: Optional[SjContract] = None
        if vn_exchange_str:
            if sj_exchange := self.vn2sj.get(vn_exchange_str):
                if sj_exchange in [SjExchange.TSE, SjExchange.OTC, SjExchange.OES]:
                    if cat := getattr(self.api.Contracts.Stocks, sj_exchange.value, None):
                        target_contract = cat._code2contract.get(symbol)
                elif sj_exchange == SjExchange.TAIFEX:
                    target_contract = self.api.Contracts.Futures._code2contract.get(symbol)
                    if not target_contract: target_contract = self.api.Contracts.Options._code2contract.get(symbol)
        else:
            target_contract = self.api.Contracts.Futures._code2contract.get(symbol)
            if not target_contract: target_contract = self.api.Contracts.Options._code2contract.get(symbol)
            if not target_contract:
                for ex_val in [SjExchange.TSE.value, SjExchange.OTC.value, SjExchange.OES.value]:
                    if (cat := getattr(self.api.Contracts.Stocks, ex_val, None)) and (target_contract := cat._code2contract.get(symbol)):
                        break
        return target_contract
        
    def _prepare_stock_order_args(self, req: OrderRequest, **kwargs) -> Optional[Dict[str, Any]]:
        if not (order_type_tuple := ORDER_TYPE_STOCK_VT2SJ.get(req.type)): return None
        sj_price_type, sj_order_type = order_type_tuple
        sj_order_cond = _helper_map_stock_order_cond(req.offset, req.direction, kwargs)
        sj_order_lot = _helper_map_stock_order_lot(kwargs.get("order_lot"))
        sj_action = DIRECTION_MAP[req.direction]
        args: Dict[str, Any] = {
            "price": req.price if sj_price_type == SjStockPriceType.LMT else 0.0, "quantity": int(req.volume),
            "action": sj_action, "price_type": sj_price_type, "order_type": sj_order_type,
            "order_cond": sj_order_cond, "order_lot": sj_order_lot,
        }
        if req.offset == Offset.CLOSETODAY and req.direction == Direction.SHORT:
            args["daytrade_short"] = True
        return args

    def _prepare_futures_order_args(self, req: OrderRequest, **kwargs) -> Optional[Dict[str, Any]]:
        if not (order_type_tuple := ORDER_TYPE_FUTURES_VT2SJ.get(req.type)): return None
        sj_price_type, sj_order_type = order_type_tuple
        sj_octype = FUTURES_OFFSET_MAP.get(req.offset, SjFuturesOCType.Auto)
        sj_action = DIRECTION_MAP[req.direction]
        return {
            "price": req.price if sj_price_type == SjFuturesPriceType.LMT else 0.0, "quantity": int(req.volume),
            "action": sj_action, "price_type": sj_price_type, "order_type": sj_order_type, "octype": sj_octype
        }
        
    def _find_mxf_contract(self) -> Tuple[Optional[str], Optional[date]]:
        mxf_cat = getattr(getattr(getattr(self.api, 'Contracts', None), 'Futures', None), 'MXF', None)
        if not mxf_cat: return None, None
        if cont := getattr(mxf_cat, "MXFR1", None):
            return cont.symbol, datetime.strptime(cont.delivery_date, "%Y/%m/%d").date()
        today, best = date.today(), None
        for fut in mxf_cat:
            if fut.symbol.startswith("MXF"):
                exp = datetime.strptime(fut.delivery_date, "%Y/%m/%d").date()
                if exp >= today and (best is None or exp < best[1]):
                    best = (fut.symbol, exp)
        return best if best else (None, None)
        
    def _size_pricetick_generic(self, sjc: SjContract) -> Tuple[float, float]:
        """
        根據商品類型，回傳正確的合約規模(size)和最小跳動點(pricetick)。
        """
        size = float(getattr(sjc, "multiplier", 1.0) or 1.0)
        tick = float(getattr(sjc, "tick_size", 0.01) or 0.01)

        if sjc.security_type == SjSecurityType.Stock:
            size = 1000.0
        elif sjc.security_type == SjSecurityType.Future:
            if sjc.code.startswith("TXF"):
                size = 200.0
            elif sjc.code.startswith("MXF"):
                size = 50.0
        elif sjc.security_type == SjSecurityType.Option:
            if sjc.code.startswith("TXO"):
                size = 50.0

        return size, tick
        
    def _parse_option(self, sjc: sjOption) -> Optional[ContractData]:
        if not (vn_exchange := self.sj_exchange_map_vnpy_enum.get(sjc.exchange)):
            return None
        try:
            strike_price = float(sjc.strike_price)
        except (ValueError, TypeError):
            self.write_log(_format_log_msg(f"選擇權合約 {sjc.code} 履約價無效: {sjc.strike_price}，已跳過。"))
            return None
        size, tick = self._size_pricetick_generic(sjc)
        cd = ContractData(
            gateway_name=self.gateway_name, symbol=sjc.code, exchange=vn_exchange,
            name=sjc.name, product=Product.OPTION, size=size, pricetick=tick,
            min_volume=1, net_position=True, history_data=True
        )
        cd.option_strike = strike_price
        cd.option_type = OptionType.CALL if sjc.option_right == SjOptionRight.Call else OptionType.PUT
        cd.option_expiry = datetime.strptime(sjc.delivery_date, "%Y/%m/%d")
        cd.option_index = str(strike_price)
        category_code = getattr(sjc, 'category', '') or "TXO"
        expiry_date_only = cd.option_expiry.date()
        if is_third_wednesday(expiry_date_only):
            ym_str = expiry_date_only.strftime("%Y%m")
            cd.option_portfolio = f"{category_code}{ym_str}"
            cd.option_underlying = f"TXF{ym_str}" if category_code == "TXO" else ""
        else:
            ymd_str = expiry_date_only.strftime("%Y%m%d")
            cd.option_portfolio = f"{category_code}W{ymd_str}"
            cd.option_underlying = ""
        return cd