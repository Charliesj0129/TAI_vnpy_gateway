# shioaji_gateway.py

# --- Python 標準庫導入 ---
import copy
import traceback
from datetime import datetime, date, timedelta
from threading import Lock, Thread
from typing import Any, Dict, List, Optional, Set, Tuple, Callable
from zoneinfo import ZoneInfo
import calendar
import time
import threading
import asyncio
import janus
import pandas as pd

# --- VnPy 相關導入 ---
from vnpy.event import Event, EventEngine
from vnpy.trader.event import EVENT_LOG, EVENT_TIMER
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
    SjStatus.PreSubmitted: Status.SUBMITTING,
    SjStatus.Submitted: Status.NOTTRADED,
    SjStatus.Failed: Status.REJECTED,
    SjStatus.Inactive: Status.REJECTED
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
    kwargs: Dict[str, Any],
    write_log_func: Callable[[str, str], None]
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

def _helper_map_stock_order_lot(
    input_lot_str: Optional[str],
    write_log_func: Callable[[str, str], None]
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
        "APIKey": "",
        "SecretKey": "",
        "CA路徑": "",
        "CA密碼": "",
        "身分證字號": "",
        "vnpy_account_id": "YOUR_ACCOUNT_ID",
        "simulation": True,
        "重連次數": 3,
        "重連間隔(秒)": 5,
        "下載合約": True,
        "contracts_cb_timeout_sec": 60.0,
        "conflation_interval_sec": 0.050,
        "janus_batch_timeout_sec": 0.1,
        "定時查詢(秒)": 120
    }

    def __init__(self, event_engine: EventEngine, gateway_name: str = "SHIOAJI"):
        super().__init__(event_engine, gateway_name)

        self.setting_filename = "shioaji_connect.json"

        self.sj_exchange_map_vnpy_enum: Dict[SjExchange, Exchange] = {
            SjExchange.TSE: Exchange.TWSE,
            SjExchange.OTC: Exchange.TOTC,
            SjExchange.TAIFEX: Exchange.TAIFEX,
            SjExchange.OES: Exchange.TOES
        }
        self.sj2vnpy = self.sj_exchange_map_vnpy_enum
        self.vn2sj: Dict[str, SjExchange] = {vn_enum.value: sj_enum for sj_enum, vn_enum in self.sj2vnpy.items()}

        self.loop = asyncio.new_event_loop()
        self._async_thread: Optional[threading.Thread] = None
        self.janus_queue = janus.Queue(maxsize=8000)

        self.api: Optional[sj.Shioaji] = None
        self.connected: bool = False
        self.logged_in: bool = False
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
        self._janus_consumer_task: Optional[asyncio.Task] = None

        self.fetched_security_types: Set[SjSecurityType] = set()
        self.expected_security_types: Set[SjSecurityType] = {SjSecurityType.Index, SjSecurityType.Stock, SjSecurityType.Future, SjSecurityType.Option}
        self._contracts_processed_flag: bool = False
        self._processing_contracts_lock = threading.Lock()
        self._all_contracts_fetched_event = threading.Event()
        
    def connect(self, setting: dict):
        if self.connected:
            return

        self.session_setting = load_json(self.setting_filename)
        if not self.session_setting:
            self.write_log(f"無法從 {self.setting_filename} 載入設定檔。", "error")
            return

        self.vnpy_account_id = self.session_setting.get("vnpy_account_id", "ShioajiAccount")
        self.query_interval = int(self.session_setting.get("定時查詢(秒)", 120))
        
        self.write_log(f"開始連接 Shioaji API... (帳戶ID: {self.vnpy_account_id})")

        self._start_async_loop_thread()

        self.connect_thread = Thread(target=self._connect_worker)
        self.connect_thread.daemon = True
        self.connect_thread.start()

        self.event_engine.register(EVENT_TIMER, self.process_timer_event)

    def _connect_worker(self):
        try:
            self.api = sj.Shioaji(simulation=self.session_setting.get("simulation", True))
            self.api.login(
                api_key=self.session_setting["APIKey"],
                secret_key=self.session_setting["SecretKey"],
                fetch_contract=False,
                subscribe_trade=True,
                contracts_timeout=0
            )

            accounts_list: List[SjAccount] = self.api.list_accounts()
            for acc in accounts_list:
                self.api.set_default_account(acc)
            
            self.write_log("Shioaji API 登入成功。")

            # --- 下載合約 ---
            self.write_log("開始下載合約資料...")
            self._all_contracts_fetched_event.clear()
            self.fetched_security_types.clear()
            self._contracts_processed_flag = False
            
            contracts_cb_timeout = float(self.session_setting.get("contracts_cb_timeout_sec", 60.0))
            self.api.fetch_contracts(
                contract_download=self.session_setting.get("下載合約", True),
                contracts_timeout=0,
                contracts_cb=self._contracts_cb
            )

            if not self._all_contracts_fetched_event.wait(timeout=contracts_cb_timeout):
                self.write_log("等待合約下載回調超時。", "warning")

            if not self._contracts_processed_flag:
                self.write_log("合約回調完成，開始處理合約資料。")
                self._process_contracts()
                self._contracts_processed_flag = True

            # --- 啟用 CA ---
            if not self.session_setting.get("simulation"):
                self.write_log("啟用憑證 (CA)...")
                try:
                    self.api.activate_ca(
                        ca_path=self.session_setting["CA路徑"],
                        ca_passwd=self.session_setting["CA密碼"],
                        person_id=self.session_setting["身分證字號"],
                    )
                    self.write_log("憑證 (CA) 啟用成功。")
                except Exception as e_ca:
                    self.write_log(f"憑證 (CA) 啟用失敗: {e_ca}", "error")

            self._set_callbacks()
            self.connected = True
            self.logged_in = True
            self.write_log("Gateway 連線成功。")

            self.query_all()
            self._resubscribe_all()

        except Exception as e:
            self.write_log(f"Gateway 連線失敗: {e}\n{traceback.format_exc()}", "error")
            self._handle_disconnect()
            
    def _start_async_loop_thread(self):
        if not self._async_thread or not self._async_thread.is_alive():
            self._async_thread = threading.Thread(target=self._run_async_loop, daemon=True)
            self._async_thread.start()

    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self._conflation_task = self.loop.create_task(self._conflation_processor())
            self._janus_consumer_task = self.loop.create_task(self._queue_consumer())
            self.loop.run_forever()
        except Exception as e:
            self.write_log(f"Asyncio 迴圈錯誤: {e}", "error")
        finally:
            tasks = [t for t in [self._conflation_task, self._janus_consumer_task] if t and not t.done()]
            for task in tasks:
                task.cancel()
            
            async def gather_tasks():
                await asyncio.gather(*tasks, return_exceptions=True)

            if self.loop.is_running():
                self.loop.run_until_complete(gather_tasks())
                self.loop.stop()
            self.loop.close()
            self.write_log("Asyncio 迴圈已關閉。")

    def _set_callbacks(self):
        self.api.quote.set_on_tick_stk_v1_callback(self._on_tick_stk)
        self.api.quote.set_on_tick_fop_v1_callback(self._on_tick_fop)
        self.api.quote.set_on_bidask_stk_v1_callback(self._on_bidask_stk)
        self.api.quote.set_on_bidask_fop_v1_callback(self._on_bidask_fop)
        self.api.set_order_callback(self._on_order_deal_shioaji)
        if hasattr(self.api, "set_session_down_callback"):
            self.api.set_session_down_callback(self._on_session_down)
        self.write_log("Shioaji 回調函數設定完成。")

    def _handle_disconnect(self):
        if not self.connected and not self.logged_in:
            return
        
        self.connected = False
        self.logged_in = False
        self.write_log("Gateway 已斷線。", "warning")

        reconnect_limit = int(self.session_setting.get("重連次數", 3))
        if reconnect_limit > 0:
            self._start_reconnect()
        else:
            self.write_log("未設定重連，Gateway 將保持斷線狀態。", "warning")
            
    def _start_reconnect(self):
        with self._reconnect_lock:
            if self.reconnect_attempts < int(self.session_setting.get("重連次數", 3)):
                self.reconnect_attempts += 1
                self.write_log(f"準備進行第 {self.reconnect_attempts} 次重連...")
                reconnect_interval = int(self.session_setting.get("重連間隔(秒)", 5))
                self._reconnect_timer = threading.Timer(reconnect_interval, self.connect, args=[self.session_setting])
                self._reconnect_timer.start()
            else:
                self.write_log("已達最大重連次數，停止重連。", "error")
                
    def _on_session_down(self, *args):
        self.write_log("偵測到 Session 中斷。", "warning")
        self._handle_disconnect()
        
    def process_timer_event(self, event: Event):
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
                self.write_log(f"重新訂閱 {vt_symbol} 失敗: {e}", "error")

    def subscribe(self, req: SubscribeRequest):
        vt_symbol = req.vt_symbol
        if not self._check_connection():
            self.write_log(f"訂閱失敗 (未連線): {vt_symbol}", "warning")
            return
            
        contract = self.find_sj_contract(req.symbol, req.exchange.value)
        if not contract:
            self.write_log(f"訂閱失敗 (找不到合約): {vt_symbol}", "warning")
            return

        with self.subscribed_lock:
            if len(self.subscribed) >= 190: # Shioaji 上限
                self.write_log(f"訂閱失敗 (已達上限): {vt_symbol}", "warning")
                return

        try:
            self.api.quote.subscribe(contract, quote_type=SjQuoteType.Tick, version=SjQuoteVersion.v1)
            self.api.quote.subscribe(contract, quote_type=SjQuoteType.BidAsk, version=SjQuoteVersion.v1)
            with self.subscribed_lock:
                self.subscribed.add(vt_symbol)
            self.write_log(f"成功發送訂閱請求: {vt_symbol}")
        except Exception as e:
            self.write_log(f"訂閱 API 錯誤: {vt_symbol}, {e}", "error")

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
            self.write_log(f"取消訂閱 API 錯誤: {vt_symbol}, {e}", "error")

    def send_order(self, req: OrderRequest) -> str:
        """發送下單請求。"""
        if not self._check_connection(check_ca=True):
            order = req.create_order_data(f"REJ_CONN_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
            order.status = Status.REJECTED
            order.reference = "Gateway 未連線或 CA 未簽署"
            super().on_order(order)
            return order.vt_orderid

        sj_contract = self.find_sj_contract(req.symbol, req.exchange.value)
        if not sj_contract:
            order = req.create_order_data(f"REJ_NO_CONTRACT_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
            order.status = Status.REJECTED
            order.reference = f"找不到合約: {req.vt_symbol}"
            super().on_order(order)
            return order.vt_orderid

        product = PRODUCT_MAP_REVERSE.get(sj_contract.security_type)
        order_args: Optional[Dict[str, Any]] = None
        if product == Product.EQUITY:
            order_args = self._prepare_stock_order_args(req, **req.extra)
        elif product in [Product.FUTURES, Product.OPTION]:
            order_args = self._prepare_futures_order_args(req, **req.extra)

        if not order_args:
            order = req.create_order_data(f"REJ_PREP_ERR_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
            order.status = Status.REJECTED
            order.reference = "訂單參數準備失敗"
            super().on_order(order)
            return order.vt_orderid

        if product == Product.EQUITY:
            order_args["account"] = self.api.stock_account
        else:
            order_args["account"] = self.api.futopt_account
            
        if not order_args["account"]:
            order = req.create_order_data(f"REJ_NO_ACC_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
            order.status = Status.REJECTED
            order.reference = f"找不到對應的 Shioaji {product.value} 帳戶"
            super().on_order(order)
            return order.vt_orderid
            
        try:
            sj_order = self.api.Order(**order_args)
            trade_resp: Optional[SjTrade] = self.api.place_order(sj_contract, sj_order)

            if trade_resp and trade_resp.order and trade_resp.status:
                shioaji_seqno = trade_resp.order.seqno
                vt_orderid = f"{self.gateway_name}.{shioaji_seqno}"

                order = OrderData(
                    gateway_name=self.gateway_name,
                    #accountid=self.vnpy_account_id,
                    symbol=req.symbol,
                    exchange=req.exchange,
                    orderid=vt_orderid,
                    type=req.type,
                    direction=req.direction,
                    offset=req.offset,
                    price=req.price,
                    volume=req.volume,
                    traded=float(trade_resp.status.deal_quantity),
                    status=STATUS_MAP.get(trade_resp.status.status, Status.SUBMITTING),
                    datetime=trade_resp.status.order_datetime.replace(tzinfo=TAIPEI_TZ) if trade_resp.status.order_datetime else datetime.now(TAIPEI_TZ),
                    reference=trade_resp.status.msg
                )
                
                with self.order_map_lock:
                    self.orders[vt_orderid] = order
                    self.shioaji_trades[shioaji_seqno] = trade_resp
                
                super().on_order(copy.copy(order))
                return vt_orderid
            else:
                raise ValueError(f"Shioaji place_order 未返回有效回應: {repr(trade_resp)}")

        except Exception as e:
            self.write_log(f"下單失敗 ({req.vt_symbol}): {e}", "error")
            order = req.create_order_data(f"REJ_API_ERR_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
            order.status = Status.REJECTED
            order.reference = f"Shioaji API 錯誤: {e}"
            super().on_order(order)
            return order.vt_orderid

    def cancel_order(self, req: CancelRequest):
        if not self._check_connection(check_ca=True):
            return

        parts = req.orderid.split('.')
        if not (len(parts) >= 2 and req.orderid.startswith(self.gateway_name)):
            self.write_log(f"撤單失敗: OrderID {req.orderid} 格式不符。", "error")
            return
        
        shioaji_seqno = parts[-1]

        with self.order_map_lock:
            sj_trade_to_cancel = self.shioaji_trades.get(shioaji_seqno)
            vnpy_order = self.orders.get(req.orderid)
        
        if not vnpy_order or not sj_trade_to_cancel:
            self.write_log(f"撤單失敗: 找不到訂單 {req.orderid} 的內部記錄。", "warning")
            return
            
        if not vnpy_order.is_active():
            self.write_log(f"訂單 {req.orderid} 非活躍狀態，無需撤銷。", "info")
            return
            
        try:
            self.api.cancel_order(sj_trade_to_cancel)
            self.write_log(f"已發送撤單請求: {req.orderid}")
        except Exception as e:
            self.write_log(f"撤單 API 呼叫失敗 ({req.orderid}): {e}", "error")

    def query_account(self):
        if not self._check_connection():
            return
        try:
            # 查詢股票帳戶
            if self.api.stock_account and self.api.stock_account.signed:
                balance_info = self.api.account_balance()
                if balance_info:
                    acc_data = AccountData(
                        accountid=self.vnpy_account_id,
                        balance=float(balance_info.acc_balance),
                        frozen=0.0,
                        gateway_name=self.gateway_name
                    )
                    super().on_account(acc_data)
            # 查詢期貨帳戶
            if self.api.futopt_account and self.api.futopt_account.signed:
                margin_info = self.api.margin(account=self.api.futopt_account)
                if margin_info:
                    acc_data = AccountData(
                        accountid=self.vnpy_account_id,
                        balance=float(margin_info.equity_amount),
                        frozen=float(margin_info.initial_margin + margin_info.order_margin_premium),
                        gateway_name=self.gateway_name
                    )
                    super().on_account(acc_data)
        except Exception as e:
            self.write_log(f"查詢帳戶餘額失敗: {e}", "error")

    def query_position(self):
        if not self._check_connection():
            return
        
        received_position_keys: Set[Tuple[str, Direction]] = set()

        with self.position_lock:
            previous_position_keys = set(self.positions.keys())
        
        try:
            # 查詢股票持倉
            if self.api.stock_account:
                stock_positions = self.api.list_positions(self.api.stock_account, SjUnit.Share)
                self._process_api_positions(stock_positions, received_position_keys)
            
            # 查詢期貨/選擇權持倉
            if self.api.futopt_account:
                futopt_positions = self.api.list_positions(self.api.futopt_account, SjUnit.Common)
                self._process_api_positions(futopt_positions, received_position_keys)
            
            # 清理已消失的持倉
            with self.position_lock:
                keys_to_zero_out = previous_position_keys - received_position_keys
                for vt_symbol_key, direction_key in keys_to_zero_out:
                    old_pos_data = self.positions.pop((vt_symbol_key, direction_key), None)
                    if old_pos_data:
                        zeroed_pos = PositionData(
                            gateway_name=self.gateway_name,
                            symbol=old_pos_data.symbol,
                            exchange=old_pos_data.exchange,
                            direction=direction_key,
                            volume=0, yd_volume=0, frozen=0,
                            price=old_pos_data.price, pnl=0.0,
                            #accountid=self.vnpy_account_id
                        )
                        super().on_position(zeroed_pos)
        except Exception as e:
            self.write_log(f"查詢持倉失敗: {e}", "error")
            
    def query_history(self, req: HistoryRequest) -> List[BarData]:
        if not self._check_connection():
            return []

        sj_contract = self.find_sj_contract(req.symbol, req.exchange.value)
        if not sj_contract:
            return []

        if req.interval != Interval.MINUTE:
            self.write_log(f"不支援的K棒間隔: {req.interval.value}", "error")
            return []

        bars: List[BarData] = []
        try:
            shioaji_kbars_obj = self.api.kbars(
                contract=sj_contract,
                start=req.start.strftime("%Y-%m-%d"),
                end=req.end.strftime("%Y-%m-%d")
            )
            df = pd.DataFrame({**shioaji_kbars_obj})
            if df.empty:
                return []
            
            df['datetime'] = pd.to_datetime(df['ts'], unit='s').dt.tz_localize('Asia/Taipei')
            df.dropna(inplace=True)

            for _, row in df.iterrows():
                bar_time = row['datetime'].to_pydatetime()
                if not (req.start <= bar_time < req.end):
                    continue

                bar = BarData(
                    gateway_name=self.gateway_name,
                    symbol=req.symbol,
                    exchange=req.exchange,
                    datetime=bar_time + timedelta(minutes=1),
                    interval=req.interval,
                    volume=float(row["Volume"]),
                    open_price=float(row["Open"]),
                    high_price=float(row["High"]),
                    low_price=float(row["Low"]),
                    close_price=float(row["Close"]),
                    turnover=float(row.get("Amount", 0.0)),
                    open_interest=0,
                )
                bars.append(bar)
            return bars
        except Exception as e:
            self.write_log(f"查詢歷史K棒失敗: {e}", "error")
            return []
            
    def close(self):
        if not self.connected:
            return
        
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
            
        self.event_engine.unregister(EVENT_TIMER, self.process_timer_event)

        if self.logged_in and self.api:
            self.api.logout()
        
        self.logged_in = False
        self.connected = False
        
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
            
        if self._async_thread and self._async_thread.is_alive():
            self._async_thread.join(timeout=5.0)

        self.write_log("Gateway 已關閉。")

    def write_log(self, msg: str, level: str = "info"):
        log = LogData(msg=msg, gateway_name=self.gateway_name)
        log_event = Event(type=EVENT_LOG, data=log)
        self.event_engine.put(log_event)

    def _check_connection(self, check_ca: bool = False) -> bool:
        if not self.connected or not self.logged_in or not self.api:
            self.write_log("API 未連線或未登入。", "warning")
            return False
        if check_ca and not self.session_setting.get("simulation"):
            # 簡易檢查，實際下單時 Shioaji 會報錯
            pass
        return True

    # --- 以下為內部輔助/回調方法 ---
    def _contracts_cb(self, security_type: SjSecurityType):
        self.fetched_security_types.add(security_type)
        if self.fetched_security_types.issuperset(self.expected_security_types):
            self._all_contracts_fetched_event.set()

    def _process_contracts(self):
        """
        處理從 Shioaji API 獲取的所有合約資料。
        這個方法會解析不同類型的商品（股票、期貨、選擇權），
        將它們轉換為 VnPy 的 ContractData 格式，
        然後逐一推送到事件引擎。
        """
        self.write_log("開始處理合約...")
        # 建立一個臨時字典來存放解析後的合約資料
        parsed_contracts_tmp: Dict[str, ContractData] = {}

        # 1. 處理期貨合約
        futures_root = getattr(self.api.Contracts, 'Futures', None)
        if futures_root and hasattr(futures_root, '_code2contract'):
            for code, sjc in futures_root._code2contract.items():
                vn_exchange = self.sj2vnpy.get(sjc.exchange)
                if not vn_exchange:
                    continue
                size, tick = self._size_pricetick_generic(sjc)
                # 為台灣常見期貨設定合約規格
                if code.startswith("TXF"): size = 200.0
                elif code.startswith("MXF"): size = 50.0
                
                cd = ContractData(
                    gateway_name=self.gateway_name,
                    symbol=code,
                    exchange=vn_exchange,
                    name=sjc.name,
                    product=Product.FUTURES,
                    size=size,
                    pricetick=tick,
                    min_volume=1,
                    net_position=True,
                    history_data=True
                )
                parsed_contracts_tmp[cd.vt_symbol] = cd

        # 2. 處理選擇權合約
        options_root = getattr(self.api.Contracts, 'Options', None)
        if options_root and hasattr(options_root, '_code2contract'):
            for code, sjc in options_root._code2contract.items():
                if isinstance(sjc, sjOption):
                    cd = self._parse_option(sjc)
                    if cd:
                        parsed_contracts_tmp[cd.vt_symbol] = cd

        # 3. 處理股票/ETF合約
        stocks_root = getattr(self.api.Contracts, 'Stocks', None)
        if stocks_root:
            for sj_ex_enum, vn_ex_enum in self.sj2vnpy.items():
                if sj_ex_enum not in [SjExchange.TSE, SjExchange.OTC, SjExchange.OES]:
                    continue
                exchange_cat = getattr(stocks_root, sj_ex_enum.value, None)
                if exchange_cat and hasattr(exchange_cat, '_code2contract'):
                    for code, sjc in exchange_cat._code2contract.items():
                        product_type = Product.ETF if "ETF" in sjc.name.upper() else Product.EQUITY
                        size, tick = self._size_pricetick_generic(sjc)
                        cd = ContractData(
                            gateway_name=self.gateway_name,
                            symbol=code,
                            exchange=vn_ex_enum,
                            name=sjc.name,
                            product=product_type,
                            size=size,
                            pricetick=tick,
                            min_volume=1,
                            net_position=False,
                            history_data=True
                        )
                        parsed_contracts_tmp[cd.vt_symbol] = cd

        # 4. 補齊週選擇權的標的合約
        mxf_underlying, _ = self._find_mxf_contract()
        if mxf_underlying:
            for cd in parsed_contracts_tmp.values():
                # 判斷邏輯：是選擇權、尚無標的、且商品組合名中可能包含'W'代表為週選
                if cd.product == Product.OPTION and not cd.option_underlying and "W" in cd.option_portfolio:
                    cd.option_underlying = mxf_underlying

        # 5. 更新內部快取並逐一發送合約事件
        with self.contract_lock:
            self.contracts.clear()
            self.contracts.update(parsed_contracts_tmp)

        # 逐一推送 on_contract 事件，這會觸發 MainEngine 的處理邏輯
        for contract in self.contracts.values():
            super().on_contract(contract)

        self.write_log(f"合約處理完成，共載入 {len(self.contracts)} 筆合約。")

    def _on_tick_stk(self, exchange: SjExchange, tick: TickSTKv1):
        vn_exchange_val = getattr(self.sj2vnpy.get(exchange), 'value', None)
        if not vn_exchange_val: return
        vt_symbol = f"{tick.code}.{vn_exchange_val}"
        with self.raw_data_cache_lock: self.latest_raw_ticks[vt_symbol] = tick
        with self.pending_conflation_lock: self.pending_conflation_processing.add(vt_symbol)
        if self.loop.is_running(): self.loop.call_soon_threadsafe(self.conflation_trigger.set)

    def _on_bidask_stk(self, exchange: SjExchange, bidask: BidAskSTKv1):
        vn_exchange_val = getattr(self.sj2vnpy.get(exchange), 'value', None)
        if not vn_exchange_val: return
        vt_symbol = f"{bidask.code}.{vn_exchange_val}"
        with self.raw_data_cache_lock: self.latest_raw_bidasks[vt_symbol] = bidask
        with self.pending_conflation_lock: self.pending_conflation_processing.add(vt_symbol)
        if self.loop.is_running(): self.loop.call_soon_threadsafe(self.conflation_trigger.set)
        
    def _on_tick_fop(self, exchange: SjExchange, tick: TickFOPv1):
        vn_exchange_val = getattr(self.sj2vnpy.get(exchange), 'value', None)
        if not vn_exchange_val: return
        vt_symbol = f"{tick.code}.{vn_exchange_val}"
        with self.raw_data_cache_lock: self.latest_raw_fop_ticks[vt_symbol] = tick
        with self.pending_conflation_lock: self.pending_conflation_processing.add(vt_symbol)
        if self.loop.is_running(): self.loop.call_soon_threadsafe(self.conflation_trigger.set)

    def _on_bidask_fop(self, exchange: SjExchange, bidask: BidAskFOPv1):
        vn_exchange_val = getattr(self.sj2vnpy.get(exchange), 'value', None)
        if not vn_exchange_val: return
        vt_symbol = f"{bidask.code}.{vn_exchange_val}"
        with self.raw_data_cache_lock: self.latest_raw_fop_bidasks[vt_symbol] = bidask
        with self.pending_conflation_lock: self.pending_conflation_processing.add(vt_symbol)
        if self.loop.is_running(): self.loop.call_soon_threadsafe(self.conflation_trigger.set)
        
    def _on_order_deal_shioaji(self, state: SjOrderState, message: dict):
        try:
            event_tuple = ('process_order_deal', state, message)
            if self.loop.is_running():
                self.loop.call_soon_threadsafe(self.janus_queue.sync_q.put_nowait, event_tuple)
        except Exception as e:
            self.write_log(f"推送訂單/成交至佇列時出錯: {e}", "error")

    async def _conflation_processor(self):
        """行情聚合處理器"""
        conflation_interval = float(self.session_setting.get("conflation_interval_sec", 0.050))
        while True:
            try:
                if conflation_interval > 0:
                    await asyncio.wait_for(self.conflation_trigger.wait(), timeout=conflation_interval)
                else:
                    await self.conflation_trigger.wait()
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception as e:
                self.write_log(f"行情聚合器錯誤: {e}", "error")
                await asyncio.sleep(1)
                continue
            
            if self.conflation_trigger.is_set():
                self.conflation_trigger.clear()

            with self.pending_conflation_lock:
                if not self.pending_conflation_processing: continue
                symbols_to_process = self.pending_conflation_processing.copy()
                self.pending_conflation_processing.clear()
            
            processing_tasks = [self._process_conflated_data_async(vt_symbol) for vt_symbol in symbols_to_process]
            if processing_tasks:
                await asyncio.gather(*processing_tasks, return_exceptions=True)

    async def _process_conflated_data_async(self, vt_symbol: str):
        """處理單一商品的聚合行情"""
        try:
            with self.raw_data_cache_lock:
                raw_tick = self.latest_raw_ticks.get(vt_symbol)
                raw_bidask = self.latest_raw_bidasks.get(vt_symbol)
                raw_fop_tick = self.latest_raw_fop_ticks.get(vt_symbol)
                raw_fop_bidask = self.latest_raw_fop_bidasks.get(vt_symbol)

            if not any([raw_tick, raw_bidask, raw_fop_tick, raw_fop_bidask]): return

            with self.tick_cache_lock:
                vnpy_tick = self.tick_cache.get(vt_symbol)
                if not vnpy_tick:
                    symbol, exchange_str = vt_symbol.split('.')
                    vnpy_tick = TickData(gateway_name=self.gateway_name, symbol=symbol, exchange=Exchange(exchange_str), datetime=datetime.now(TAIPEI_TZ))
            
            final_dt = vnpy_tick.datetime

            if raw_tick:
                tick_dt = raw_tick.datetime.replace(tzinfo=TAIPEI_TZ)
                final_dt = max(final_dt, tick_dt)
                vnpy_tick.last_price = float(raw_tick.close)
                vnpy_tick.last_volume = float(raw_tick.volume)
                vnpy_tick.volume = float(raw_tick.total_volume)
                vnpy_tick.turnover = float(getattr(raw_tick, 'total_amount', 0.0))
                vnpy_tick.open_price = float(raw_tick.open)
                vnpy_tick.high_price = float(raw_tick.high)
                vnpy_tick.low_price = float(raw_tick.low)
                if hasattr(raw_tick, 'price_chg'):
                    vnpy_tick.pre_close = float(raw_tick.close - raw_tick.price_chg)
            
            if raw_bidask:
                bidask_dt = raw_bidask.datetime.replace(tzinfo=TAIPEI_TZ)
                final_dt = max(final_dt, bidask_dt)
                for i in range(5):
                    setattr(vnpy_tick, f"bid_price_{i+1}", float(raw_bidask.bid_price[i]) if i < len(raw_bidask.bid_price) else 0.0)
                    setattr(vnpy_tick, f"bid_volume_{i+1}", float(raw_bidask.bid_volume[i]) if i < len(raw_bidask.bid_volume) else 0.0)
                    setattr(vnpy_tick, f"ask_price_{i+1}", float(raw_bidask.ask_price[i]) if i < len(raw_bidask.ask_price) else 0.0)
                    setattr(vnpy_tick, f"ask_volume_{i+1}", float(raw_bidask.ask_volume[i]) if i < len(raw_bidask.ask_volume) else 0.0)
                    
            if raw_fop_tick:
                fop_tick_dt = raw_fop_tick.datetime.replace(tzinfo=TAIPEI_TZ)
                final_dt = max(final_dt, fop_tick_dt)
                vnpy_tick.last_price = float(raw_fop_tick.close)
                # ... 其他 FOP Tick 欄位 ...
                vnpy_tick.open_interest = float(getattr(raw_fop_tick, 'open_interest', 0.0))

            if raw_fop_bidask:
                 fop_bidask_dt = raw_fop_bidask.datetime.replace(tzinfo=TAIPEI_TZ)
                 final_dt = max(final_dt, fop_bidask_dt)
                 # ... FOP BidAsk 欄位 ...

            vnpy_tick.datetime = final_dt
            vnpy_tick.localtime = datetime.now()

            with self.tick_cache_lock: self.tick_cache[vt_symbol] = vnpy_tick
            super().on_tick(copy.copy(vnpy_tick))
        except Exception as e:
            self.write_log(f"處理聚合行情錯誤 ({vt_symbol}): {e}", "error")

    async def _queue_consumer(self):
        """從佇列中消費訂單/成交事件"""
        janus_timeout = float(self.session_setting.get("janus_batch_timeout_sec", 0.1))
        while True:
            try:
                first_item = await self.janus_queue.async_q.get()
                batch = [first_item]
                if janus_timeout > 0:
                    try:
                        while True:
                            item = await asyncio.wait_for(self.janus_queue.async_q.get(), timeout=janus_timeout)
                            batch.append(item)
                    except asyncio.TimeoutError:
                        pass
                
                if batch: await self._process_batch(batch)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.write_log(f"佇列消費者錯誤: {e}", "error")
                await asyncio.sleep(0.5)

    async def _process_batch(self, batch: list):
        """異步處理一批事件"""
        tasks = []
        loop = asyncio.get_running_loop()
        for item_tuple in batch:
            task_type, data1, data2 = item_tuple
            if task_type == 'process_order_deal':
                tasks.append(loop.run_in_executor(None, self._process_single_order_deal_event, data1, data2))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
        for _ in batch:
            try:
                self.janus_queue.sync_q.task_done()
            except ValueError:
                pass
            
    def _process_single_order_deal_event(self, state: SjOrderState, message: dict):
        """在執行緒池中處理單個訂單/成交事件"""
        shioaji_seqno: Optional[str] = None
        try:
            if "order" in message and isinstance(message["order"], dict):
                shioaji_seqno = message["order"].get("seqno")
            elif "status" in message and isinstance(message["status"], dict):
                shioaji_seqno = message["status"].get("id") or message["status"].get("seqno")
            elif state in [SjOrderState.StockDeal, SjOrderState.FuturesDeal]:
                shioaji_seqno = message.get("seqno")
            
            if not shioaji_seqno:
                return

            vt_orderid = f"{self.gateway_name}.{shioaji_seqno}"

            with self.order_map_lock:
                cached_order = self.orders.get(vt_orderid)
                if not cached_order: return
                order_to_update = copy.copy(cached_order)

            final_status = order_to_update.status
            final_ref_msg = order_to_update.reference
            final_dt = order_to_update.datetime

            status_block = message.get("status", message.get("order", {}))
            if isinstance(status_block, dict):
                status_str = status_block.get("status")
                if status_str: final_status = STATUS_MAP.get(SjStatus(status_str), final_status)
                ref_msg = status_block.get("msg")
                if ref_msg: final_ref_msg = ref_msg
                dt_obj = status_block.get("order_datetime")
                if dt_obj: final_dt = dt_obj.replace(tzinfo=TAIPEI_TZ)

            op_block = message.get("operation", {})
            if isinstance(op_block, dict) and op_block.get("op_code") != "00":
                final_status = Status.REJECTED
                if op_block.get("op_msg"): final_ref_msg = op_block.get("op_msg")

            if state in [SjOrderState.StockDeal, SjOrderState.FuturesDeal]:
                deals = status_block.get("deals", [message] if state == SjOrderState.StockDeal else [])
                for deal in deals:
                    try:
                        deal_id = deal.get("exchange_seq") or deal.get("id") or f"{shioaji_seqno}_{time.time()}"
                        deal_key = (str(shioaji_seqno), str(deal_id))
                        
                        with self.order_map_lock:
                            if deal_key in self.shioaji_deals: continue
                            self.shioaji_deals.add(deal_key)

                        deal_qty = float(deal["quantity"])
                        if deal_qty <= 0: continue
                        
                        order_to_update.traded += deal_qty
                        
                        trade_dt_ts = deal.get("ts")
                        trade_dt = datetime.fromtimestamp(float(trade_dt_ts), TAIPEI_TZ) if trade_dt_ts else datetime.now(TAIPEI_TZ)
                        final_dt = max(final_dt, trade_dt)

                        trade = TradeData(
                            gateway_name=self.gateway_name,
                            #accountid=self.vnpy_account_id,
                            symbol=order_to_update.symbol,
                            exchange=order_to_update.exchange,
                            orderid=vt_orderid,
                            tradeid=f"{self.gateway_name}.{deal_id}",
                            direction=order_to_update.direction,
                            offset=order_to_update.offset,
                            price=float(deal["price"]),
                            volume=deal_qty,
                            datetime=trade_dt
                        )
                        super().on_trade(trade)
                    except Exception as e_deal:
                        self.write_log(f"處理單筆成交錯誤 ({vt_orderid}): {e_deal}", "error")
            
            if order_to_update.traded >= order_to_update.volume:
                final_status = Status.ALLTRADED
            elif order_to_update.traded > 0:
                final_status = Status.PARTTRADED
            
            if final_status != order_to_update.status or final_ref_msg != order_to_update.reference:
                order_to_update.status = final_status
                order_to_update.reference = final_ref_msg
                order_to_update.datetime = final_dt
                
                with self.order_map_lock: self.orders[vt_orderid] = order_to_update
                super().on_order(copy.copy(order_to_update))

        except Exception as e:
            self.write_log(f"處理訂單/成交事件時發生嚴重錯誤 (SeqNo {shioaji_seqno}): {e}", "critical")

    def _process_api_positions(self, api_positions, received_keys):
        if not api_positions: return
        for pos in api_positions:
            try:
                sj_contract = self.find_sj_contract(pos.code)
                if not sj_contract: continue

                vn_exchange = self.sj2vnpy.get(sj_contract.exchange)
                if not vn_exchange: continue

                vn_direction = DIRECTION_MAP_REVERSE.get(pos.direction)
                if not vn_direction: continue

                position = PositionData(
                    gateway_name=self.gateway_name,
                    #accountid=self.vnpy_account_id,
                    symbol=pos.code,
                    exchange=vn_exchange,
                    direction=vn_direction,
                    volume=float(pos.quantity),
                    yd_volume=float(getattr(pos, 'yd_quantity', 0)),
                    frozen=max(0.0, float(pos.quantity) - float(getattr(pos, 'yd_quantity', pos.quantity))),
                    price=float(pos.price),
                    pnl=float(getattr(pos, 'pnl', 0.0))
                )
                
                pos_key = (position.vt_symbol, position.direction)
                received_keys.add(pos_key)
                with self.position_lock: self.positions[pos_key] = position
                super().on_position(position)
            except Exception as e:
                self.write_log(f"處理 API 持倉資料錯誤: {e}", "error")

    def find_sj_contract(self, symbol: str, vn_exchange_str: Optional[str] = None) -> Optional[SjContract]:
        if not self.api or not hasattr(self.api, 'Contracts') or self.api.Contracts.status != SjFetchStatus.Fetched:
            return None
        
        target_contract: Optional[SjContract] = None
        if vn_exchange_str:
            sj_exchange = self.vn2sj.get(vn_exchange_str)
            if sj_exchange:
                if sj_exchange in [SjExchange.TSE, SjExchange.OTC, SjExchange.OES]:
                    cat = getattr(self.api.Contracts.Stocks, sj_exchange.value, None)
                    if cat: target_contract = cat._code2contract.get(symbol)
                elif sj_exchange == SjExchange.TAIFEX:
                    target_contract = self.api.Contracts.Futures._code2contract.get(symbol)
                    if not target_contract: target_contract = self.api.Contracts.Options._code2contract.get(symbol)
        else: # 遍歷查找
            target_contract = self.api.Contracts.Futures._code2contract.get(symbol)
            if not target_contract: target_contract = self.api.Contracts.Options._code2contract.get(symbol)
            if not target_contract:
                for ex_val in [SjExchange.TSE.value, SjExchange.OTC.value, SjExchange.OES.value]:
                    cat = getattr(self.api.Contracts.Stocks, ex_val, None)
                    if cat: target_contract = cat._code2contract.get(symbol)
                    if target_contract: break
        return target_contract
        
    def _prepare_stock_order_args(self, req: OrderRequest, **kwargs) -> Optional[Dict[str, Any]]:
        order_type_tuple = ORDER_TYPE_STOCK_VT2SJ.get(req.type)
        if not order_type_tuple: return None
        sj_price_type, sj_order_type = order_type_tuple
        sj_order_cond = _helper_map_stock_order_cond(req.offset, req.direction, kwargs, self.write_log)
        sj_order_lot = _helper_map_stock_order_lot(kwargs.get("order_lot"), self.write_log)
        sj_action = DIRECTION_MAP[req.direction]
        
        args: Dict[str, Any] = {
            "price": req.price if sj_price_type == SjStockPriceType.LMT else 0.0,
            "quantity": int(req.volume),
            "action": sj_action, "price_type": sj_price_type, "order_type": sj_order_type,
            "order_cond": sj_order_cond, "order_lot": sj_order_lot,
        }
        if req.offset == Offset.CLOSETODAY and req.direction == Direction.SHORT:
            args["daytrade_short"] = True
        return args

    def _prepare_futures_order_args(self, req: OrderRequest, **kwargs) -> Optional[Dict[str, Any]]:
        order_type_tuple = ORDER_TYPE_FUTURES_VT2SJ.get(req.type)
        if not order_type_tuple: return None
        sj_price_type, sj_order_type = order_type_tuple
        sj_octype = FUTURES_OFFSET_MAP.get(req.offset, SjFuturesOCType.Auto)
        sj_action = DIRECTION_MAP[req.direction]
        
        return {
            "price": req.price if sj_price_type == SjFuturesPriceType.LMT else 0.0,
            "quantity": int(req.volume),
            "action": sj_action, "price_type": sj_price_type, "order_type": sj_order_type,
            "octype": sj_octype,
        }
        
    def _find_mxf_contract(self) -> Tuple[Optional[str], Optional[date]]:
        mxf_cat = getattr(getattr(getattr(self.api, 'Contracts', None), 'Futures', None), 'MXF', None)
        if not mxf_cat: return None, None
        cont = getattr(mxf_cat, "MXFR1", None)
        if cont: return cont.symbol, datetime.strptime(cont.delivery_date, "%Y/%m/%d").date()
        today = date.today()
        best: Optional[Tuple[str, date]] = None
        for fut in mxf_cat:
            if fut.symbol.startswith("MXF"):
                exp = datetime.strptime(fut.delivery_date, "%Y/%m/%d").date()
                if exp >= today and (best is None or exp < best[1]):
                    best = (fut.symbol, exp)
        return best if best else (None, None)
        
    def _size_pricetick_generic(self, sjc: SjContract) -> Tuple[float, float]:
        size = float(getattr(sjc, "multiplier", 1.0) or 1.0)
        tick = float(getattr(sjc, "tick_size", 0.01) or 0.01)
        return size, tick
        
    def _parse_option(self, sjc: sjOption) -> Optional[ContractData]:
        vn_exchange = self.sj2vnpy.get(sjc.exchange)
        if not vn_exchange: return None
        size, tick = self._size_pricetick_generic(sjc)
        if sjc.code.startswith("TXO"): size = 50.0

        cd = ContractData(
            gateway_name=self.gateway_name, symbol=sjc.code, exchange=vn_exchange,
            name=sjc.name, product=Product.OPTION, size=size, pricetick=tick,
            min_volume=1, net_position=True, history_data=True
        )
        cd.option_strike = float(sjc.strike_price)
        cd.option_type = OptionType.CALL if sjc.option_right == SjOptionRight.Call else OptionType.PUT
        cd.option_expiry = datetime.strptime(sjc.delivery_date, "%Y/%m/%d")
        
        category_code = getattr(sjc, 'category', '') or "TXO"
        expiry_date_only = cd.option_expiry.date()

        if is_third_wednesday(expiry_date_only):
            ym_str = expiry_date_only.strftime("%Y%m")
            cd.option_portfolio = f"{category_code}{ym_str}"
            cd.option_index = str(int(cd.option_strike))
            cd.option_underlying = f"TXF{ym_str}" if category_code == "TXO" else ""
        else:
            ymd_str = expiry_date_only.strftime("%Y%m%d")
            cd.option_portfolio = f"{category_code}W{ymd_str}"
            cd.option_index = cd.option_portfolio
            cd.option_underlying = ""
        
        return cd
