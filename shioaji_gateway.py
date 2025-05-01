#shioaji_gateway.py

# --- Python 標準庫導入 ---
import copy
import traceback
from datetime import datetime, date,timedelta# 確保導入 date 和 time
from threading import Lock, Thread
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from zoneinfo import ZoneInfo # 使用更精確的類型提示
import calendar
import time
import threading
import asyncio
import janus
from aenum import extend_enum

# --- VnPy 相關導入 ---
from vnpy.event import EventEngine,Event,EVENT_TIMER
from vnpy.trader.utility import load_json, save_json,extract_vt_symbol  # Import load_json and save_json from vnpy.trader.utility
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    TickData, OrderData, TradeData, PositionData, AccountData,ContractData,OrderRequest, CancelRequest, SubscribeRequest, HistoryRequest, BarData
)
from vnpy.trader.constant import (Exchange, Product, Direction, OrderType, Offset, Status, Interval, OptionType,
)
from vnpy.trader.utility import BarGenerator

# --- Shioaji 相關導入 ---
import shioaji as sj
# 導入需要用到的 Shioaji 常量、數據類型和錯誤類型
from shioaji.constant import (
    Action as SjAction,
    Exchange as SjExchange,
    OrderType as SjOrderType, # ROD, IOC, FOK
    StockPriceType as SjStockPriceType, # LMT, MKT (Stock)
    FuturesPriceType as SjFuturesPriceType, # LMT, MKT, MKP (Futures)
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
from shioaji.account import Account as SjAccount, AccountType as SjAccountType, StockAccount as SjStockAccount, FutureAccount as SjFutureAccount
from shioaji.contracts import Contract as SjContract, FetchStatus as SjFetchStatus
from shioaji.order import Trade as SjTrade
from shioaji.position import StockPosition as SjStockPosition, FuturePosition as SjFuturePosition, Margin as SjMargin, AccountBalance as SjAccountBalance
from shioaji.stream_data_type import TickSTKv1, TickFOPv1, BidAskSTKv1, BidAskFOPv1 # 導入 V1 行情數據類型
from shioaji.data import Kbars as SjKbars 
from shioaji.error import TokenError as SjTokenError, AccountNotSignError as SjAccountNotSignError # 導入特定錯誤
# --- 常量定義與映射 ---

# 時區設定 (Shioaji API 通常返回台灣時間)
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# Gateway 設定檔案名稱
GATEWAY_SETTING_FILENAME = "shioaji_connect.json"




# 產品類型映射 (VnPy <-> Shioaji)
PRODUCT_MAP: Dict[Product, SjSecurityType] = {
    Product.EQUITY: SjSecurityType.Stock,
    Product.FUTURES: SjSecurityType.Future,
    Product.OPTION: SjSecurityType.Option,
    Product.INDEX: SjSecurityType.Index,
    Product.ETF: SjSecurityType.Stock,      # 假設 ETF 按股票處理
    Product.WARRANT: SjSecurityType.Stock, # 假設權證按股票處理
}

# 產品類型反向映射 (Shioaji -> VnPy)
PRODUCT_MAP_REVERSE: Dict[SjSecurityType, Product] = {
    SjSecurityType.Stock: Product.EQUITY,
    SjSecurityType.Future: Product.FUTURES,
    SjSecurityType.Option: Product.OPTION,
    SjSecurityType.Index: Product.INDEX,
}

# 方向映射 (VnPy <-> Shioaji Action)
DIRECTION_MAP: Dict[Direction, SjAction] = {
    Direction.LONG: SjAction.Buy,
    Direction.SHORT: SjAction.Sell,
}
DIRECTION_MAP_REVERSE: Dict[SjAction, Direction] = {v: k for k, v in DIRECTION_MAP.items()}

# 訂單狀態映射 (Shioaji -> VnPy)
# 注意：這是一個初步映射，可能需要根據實際回報微調
STATUS_MAP: Dict[SjStatus, Status] = {
    SjStatus.Cancelled: Status.CANCELLED,
    SjStatus.Filled: Status.ALLTRADED,
    SjStatus.PartFilled: Status.PARTTRADED,
    SjStatus.PendingSubmit: Status.SUBMITTING,
    SjStatus.PreSubmitted: Status.SUBMITTING,
    SjStatus.Submitted: Status.NOTTRADED, # Submitted 但未成交 -> NotTraded
    SjStatus.Failed: Status.REJECTED,
    SjStatus.Inactive: Status.REJECTED # Inactive 狀態需要確認對應 VnPy 哪個狀態，可能是 REJECTED 或 CANCELLED
}

# --- 新增：期貨訂單類型映射 (採納參考實作優點) ---
# 將 VnPy OrderType 映射到 Shioaji (PriceType, OrderType) 元組
# 這個字典將在 send_order 中用於期貨/選擇權訂單
ORDER_TYPE_FUTURES_VT2SJ: Dict[OrderType, Tuple[SjFuturesPriceType, SjOrderType]] = {
    OrderType.LIMIT: (SjFuturesPriceType.LMT, SjOrderType.ROD),     # 限價 + ROD
    OrderType.MARKET: (SjFuturesPriceType.MKP, SjOrderType.IOC),    # 市價(MKP) + IOC (建議)
    OrderType.FAK: (SjFuturesPriceType.LMT, SjOrderType.IOC),       # FAK = 限價 + IOC
    OrderType.FOK: (SjFuturesPriceType.LMT, SjOrderType.FOK),       # FOK = 限價 + FOK
    # OrderType.STOP: 需要在 send_order 中單獨處理邏輯
    # OrderType.RFQ: 需要在 send_order 中單獨處理邏輯
}
# --- 注意：股票訂單的類型邏輯（LMT/MKT + ROD/IOC/FOK）將在 send_order 中根據 req.type 判斷 ---


# 開平倉映射 (VnPy Offset -> Shioaji FuturesOCType) (僅適用於期貨/選擇權)
FUTURES_OFFSET_MAP: Dict[Offset, SjFuturesOCType] = {
    Offset.OPEN: SjFuturesOCType.New,
    Offset.CLOSE: SjFuturesOCType.Cover,
    Offset.CLOSETODAY: SjFuturesOCType.DayTrade,
    Offset.CLOSEYESTERDAY: SjFuturesOCType.Cover,
}
FUTURES_OFFSET_MAP_REVERSE: Dict[SjFuturesOCType, Offset] = {
    SjFuturesOCType.New: Offset.OPEN,
    SjFuturesOCType.Cover: Offset.CLOSE,
    SjFuturesOCType.DayTrade: Offset.CLOSETODAY,
}

# 股票條件反向映射 (供參考)
STOCK_ORDER_COND_MAP_REVERSE: Dict[SjStockOrderCond, Tuple[Optional[Offset], Optional[Direction]]] = {
    SjStockOrderCond.Cash: (None, None),
    SjStockOrderCond.MarginTrading: (Offset.OPEN, Direction.LONG),
    SjStockOrderCond.ShortSelling: (Offset.OPEN, Direction.SHORT),
}


# 期權類型映射 (VnPy OptionType -> Shioaji OptionRight)
OPTION_TYPE_MAP: Dict[OptionType, SjOptionRight] = {
    OptionType.CALL: SjOptionRight.Call,
    OptionType.PUT: SjOptionRight.Put,
}
OPTION_TYPE_MAP_REVERSE: Dict[SjOptionRight, OptionType] = {v: k for k, v in OPTION_TYPE_MAP.items()}


# --- Custom VN Events for ShioajiGateway ---
EVENT_SUBSCRIBE_SUCCESS = "eSubscribeSuccess"
EVENT_SUBSCRIBE_FAILED  = "eSubscribeFailed"
EVENT_RECONNECT_FAILED = "eReconnectFailed"

# 取得某年月的第三個星期三
def third_wednesday(year: int, month: int) -> date:
    cal = calendar.monthcalendar(year, month)
    # 第一週可能無三，取 cal[0][2] 或 cal[1][2]
    day = cal[0][calendar.WEDNESDAY] or cal[1][calendar.WEDNESDAY]
    # 若這是第一週，則再加兩週
    if day <= 7:
        day += 14
    return date(year, month, day)

# 判斷是否為營業日 (需接入行情資料或預先標記假日)
def is_business_day(d: date) -> bool:
    # 這裡僅示意，請接入交易所日曆 API
    return d.weekday() < 5 

# 求取次一營業日
def next_business_day(d: date) -> date:
    nd = d + timedelta(days=1)
    while not is_business_day(nd):
        nd += timedelta(days=1)
    return nd

# --- Helper function to check for 3rd Wednesday ---
# (Place this inside your class or make it a standalone function)
def is_third_wednesday(dt: Optional[date]) -> bool:
    """Checks if a given date is the 3rd Wednesday of its month."""
    if not dt:
        return False
    # Wednesday is weekday() == 2
    # 3rd Wednesday falls between day 15 and 21 inclusive
    return dt.weekday() == 2 and 15 <= dt.day <= 21

# --- Helper function to safely parse date ---
# (Add this inside your class or adapt if you have similar logic)
def _parse_date(self, date_str: str, fmt: str = '%Y/%m/%d') -> Optional[date]:
    """Safely parse date string from Shioaji format (YYYY/MM/DD)."""
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        # Shioaji might return "YYYY/MM/DD"
        return datetime.strptime(date_str, fmt).date()
    except ValueError:
        # Try YYYYMMDD format as a fallback if needed
        try:
            return datetime.strptime(date_str, '%Y%m%d').date()
        except ValueError:
            self.write_log(f"Warning: Failed to parse date '{date_str}' with formats '{fmt}' and '%Y%m%d'")
            return None

def calculate_listing_date(delivery_year: int,
                           delivery_month: int,
                           is_weekly: bool = False,
                           weekly_expiry: date = None) -> date:
    """
    :param delivery_year, delivery_month: 月度合約交割年月
    :param is_weekly: 是否為週別合約
    :param weekly_expiry: 若 is_weekly=True，需提供該週別合約的到期日
    """
    if not is_weekly:
        # 月度合約：取上月底度合約的最後交易日，+1 營業日
        # 上月年月
        ym = (delivery_year, delivery_month - 1) if delivery_month > 1 else (delivery_year - 1, 12)
        last_trade = third_wednesday(ym[0], ym[1])
        return next_business_day(last_trade)
    else:
        # 週別合約：掛牌日 = 到期日 - 14 天
        listing = weekly_expiry - timedelta(days=14)
        # 必須為星期三，若非需調整到當週星期三
        while listing.weekday() != calendar.WEDNESDAY:
            listing += timedelta(days=1)
        # 排除每月第一個星期三
        if listing.day <= 7:
            listing += timedelta(weeks=1)
        return listing

for name in ["TWSE", "TOTC", "TAIFEX", "TOES"]:
            extend_enum(Exchange, name, name)
# --- Gateway 類別定義開始 ---

class ShioajiGateway(BaseGateway):
    """
    適用於 VnPy 4.0 的 Shioaji 接口 Gateway。
    """
    # (default_setting 和 exchanges 保持不變)
    default_setting = {
        "APIKey": "",
        "SecretKey": "",
        "CA路徑": "",
        "CA密碼": "",
        "身分證字號": "", # Shioaji 0.3.6+ activate_ca 需要 person_id
        "simulation": False,
        "下載合約": False,
        "重連次數": 3,
        "重連間隔(秒)": 5
    }
    exchanges: List[Exchange] = [
        Exchange.TWSE,    # 代表台灣證交所 (值為 "TSE")
        Exchange.TOTC,    # 代表台灣櫃買中心 (值為 "OTC")
        Exchange.TAIFEX,  # 代表台灣期交所 (值為 "TAIFEX")
        Exchange.TOES      # 代表 OES (值為 "OES")
    ]


    def __init__(self, event_engine: EventEngine, gateway_name: str = "SHIOAJI"):
        """構造函數"""
        # 1. 調用父類初始化
        super().__init__(event_engine, gateway_name)


        self.sj_exchange_map_vnpy_enum: Dict[SjExchange, Exchange] = {
            SjExchange.TSE: Exchange.TWSE,
            SjExchange.OTC: Exchange.TOTC,
            SjExchange.TAIFEX: Exchange.TAIFEX,
            SjExchange.OES: Exchange.TOES
        }

        # --- 2. 本地映射： SjExchange -> vn.py 的 Exchange ---
        self.sj2vnpy = self.sj_exchange_map_vnpy_enum
        self.vn2sj: Dict[str, SjExchange] = {
        vn_enum.value: sj_enum for sj_enum, vn_enum in self.sj2vnpy.items()
    }
        self.loop = asyncio.new_event_loop()
        self.janus_queue = janus.Queue(maxsize=3000) 
        threading.Thread(target=self._start_loop, daemon=True).start()
       # 啟動事件循環執行緒
        # 2. 初始化 Shioaji API 和連接狀態相關屬性
        self.api: Optional[sj.Shioaji] = None
        self.connected: bool = False
        self.logged_in: bool = False
        self.connect_thread: Optional[Thread] = None
        self.connection_start_time: float = 0

        # 3. 初始化重連相關屬性
        self.reconnect_attempts: int = 0
        self.reconnect_limit: int = 3
        self.reconnect_interval: int = 5

        # 4. 初始化配置相關屬性 (會在 connect 方法中從 setting 讀取)
        self.connect_setting: dict = {}
        self.api_key: str = ""
        self.secret_key: str = ""
        self.ca_path: str = ""
        self.ca_passwd: str = ""
        self.person_id_setting: str = ""
        self.simulation: bool = False
        self.force_download: bool = False
        # 5. 初始化用於儲存訂單、成交、持倉等數據的容器
        self.orders: Dict[str, OrderData] = {}
        self.shioaji_trades: Dict[str, SjTrade] = {}
        self.shioaji_deals: Set[Tuple[str, str]] = set()
        self.positions: Dict[Tuple[str, Direction], PositionData] = {}
        self.accounts: Dict[str, AccountData] = {}
        self.contracts: Dict[str, ContractData] = {}
        self.subscribed: Set[str] = set() #<-- 確保有 self.subscribed 初始化
        self.tick_cache: Dict[str, TickData] = {}
        self._reconnect_timer = None # 用於定時器的引用
        
        

        # 6. 初始化用於線程安全的鎖
        self.order_map_lock = Lock()
        self.position_lock = Lock()
        self.account_lock = Lock()
        self.contract_lock = Lock()
        self.subscribed_lock = Lock()
        self.tick_cache_lock = Lock() # 用於保護快取訪問
        self._reconnect_lock = Lock()


        # 7. 載入上次的設定檔 (如果存在)
        loaded_setting = load_json(GATEWAY_SETTING_FILENAME)
        if loaded_setting:
            self.connect_setting = loaded_setting
            # 將載入的設定更新到 default_setting，這樣 UI 會顯示上次的值
            self.default_setting.update(loaded_setting)
        else:
            self.write_log(f"__init__: No previous settings file found ({GATEWAY_SETTING_FILENAME}). Using defaults.")

    def _start_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._queue_consumer())
        self.loop.run_forever()

    
    async def _process_batch(self, batch: list):
        """
        將批次中每筆任務依類型分派到對應的處理函式，並行執行。
        """
        loop = asyncio.get_running_loop()  # 確保使用相同事件迴圈 :contentReference[oaicite:7]{index=7}
        tasks = []
        for task_type, exchange, raw in batch:
            # 根據任務類型建構執行緒池任務
            if task_type == 'stk_tick':
                tasks.append(loop.run_in_executor(
                    None,  # 使用預設執行緒池 :contentReference[oaicite:8]{index=8}
                    self._process_tick_stk,
                    exchange, raw
                ))
            elif task_type == 'stk_bidask':
                tasks.append(loop.run_in_executor(
                    None,
                    self._process_bidask_stk,
                    exchange, raw
                ))
            elif task_type == 'fop_tick':
                tasks.append(loop.run_in_executor(
                    None,
                    self._process_tick_fop,
                    exchange, raw
                ))
            elif task_type == 'fop_bidask':
                tasks.append(loop.run_in_executor(
                    None,
                    self._process_bidask_fop,
                    exchange, raw
                ))
            else:
                self.write_log(f"[ _process_batch ] 未知任務類型: {task_type}")

        if tasks:
            # 並行等待所有子任務完成 :contentReference[oaicite:9]{index=9}
            await asyncio.gather(*tasks)


    def _on_tick_stk(self, exchange: SjExchange, tick: TickSTKv1):
        """處理股票 Tick 回調：僅做最小封裝並快速入隊"""
                # 快速推送至 Janus 同步佇列
        try:
            self.loop.call_soon_threadsafe(
                self.janus_queue.sync_q.put_nowait,
                ('stk_tick', exchange, tick)
            )
        except Exception:
            self.write_log("Warning: janus_queue 已滿, 丟棄 tick")
    
    def _on_bidask_stk(self, exchange: SjExchange, bidask: BidAskSTKv1):
        """處理股票 BidAsk 回調：僅非阻塞入隊"""
        try:
            self.loop.call_soon_threadsafe(
                self.janus_queue.sync_q.put_nowait,
                ('stk_bidask', exchange, bidask)
            )
        except Exception:
            self.write_log("Warning: janus_queue 已滿, 丟棄 bidask")

    def _on_tick_fop(self, exchange: SjExchange, tick: TickFOPv1):
        """期貨/選擇權 Tick 回調：僅快速入隊"""
        try:
            self.loop.call_soon_threadsafe(
                self.janus_queue.sync_q.put_nowait,
                ('fop_tick', exchange, tick)
            )
        except Exception:
            self.write_log("Warning: janus_queue 已滿, 丟棄 tick")

    def _on_bidask_fop(self, exchange: SjExchange, bidask: BidAskFOPv1):
        """期貨/選擇權 BidAsk 回調：僅快速入隊"""
        try:
            self.loop.call_soon_threadsafe(
                self.janus_queue.sync_q.put_nowait,
                ('fop_bidask', exchange, bidask)
            )
        except Exception:
            self.write_log("Warning: janus_queue 已滿, 丟棄 bidask")
    
    def _process_bidask_stk(self, exchange: SjExchange, bidask: BidAskSTKv1):
        """建構或更新股票 TickData 的 BidAsk 欄位，並推送 on_tick"""
        thread_name = threading.current_thread().name
        try:
            # 1. 轉換交易所
            vn_ex_str = self.sj2vnpy[exchange]
            if not vn_ex_str:
                return
            vn_ex = Exchange(vn_ex_str)

            # 2. 時間戳處理
            if isinstance(bidask.datetime, datetime):
                dt = bidask.datetime.replace(tzinfo=TAIPEI_TZ)
            else:
                dt = datetime.fromtimestamp(bidask.datetime / 1e9, tz=TAIPEI_TZ)

            vt = f"{bidask.code}.{vn_ex.value}"

            # 3. 取回或新建 TickData
            with self.tick_cache_lock:                    # 鎖定快取存取 :contentReference[oaicite:6]{index=6}
                if vt in self.tick_cache:
                    td = self.tick_cache[vt]
                else:
                    td = TickData(
                        gateway_name=self.gateway_name,
                        symbol=bidask.code,
                        exchange=vn_ex,
                        datetime=dt,
                        name=getattr(bidask, 'name', bidask.code),
                        last_price=0.0, last_volume=0.0,
                        volume=0.0, turnover=0.0, open_interest=0.0,
                        open_price=0.0, high_price=0.0,
                        low_price=0.0, pre_close=0.0,
                        limit_up=0.0, limit_down=0.0,
                        localtime=datetime.now()
                    )

                # 4. 更新 BidAsk 前5檔
                bids = getattr(bidask, 'bid_price', [])
                bvol = getattr(bidask, 'bid_volume', [])
                asks = getattr(bidask, 'ask_price', [])
                avol = getattr(bidask, 'ask_volume', [])
                for i in range(5):
                    setattr(td, f"bid_price_{i+1}",  float(bids[i]) if i < len(bids) else 0.0)
                    setattr(td, f"bid_volume_{i+1}", float(bvol[i]) if i < len(bvol) else 0.0)
                    setattr(td, f"ask_price_{i+1}",  float(asks[i]) if i < len(asks) else 0.0)
                    setattr(td, f"ask_volume_{i+1}", float(avol[i]) if i < len(avol) else 0.0)

                td.datetime  = dt
                td.localtime = datetime.now()
                self.tick_cache[vt] = td                  # 寫回快取 :contentReference[oaicite:7]{index=7}
            self.on_tick(td)

        except Exception as e:
            self.write_log(f"[{thread_name}] _process_bidask_stk 錯誤: {e}\n{traceback.format_exc()}")

    def _process_tick_stk(self, exchange, tick):
        """處理股票 Tick：僅更新 tick 欄位，保留其餘欄位並推 on_tick"""
        thread_name = threading.current_thread().name
        try:
            vn_ex = self.sj_exchange_map_vnpy_enum.get(exchange)
            if not vn_ex:
                return
            # 時間戳轉換（含時區）
            if isinstance(tick.datetime, datetime):
                tick_dt = tick.datetime.replace(tzinfo=TAIPEI_TZ)
            else:
                tick_dt = datetime.fromtimestamp(tick.datetime / 1e9, tz=TAIPEI_TZ)

            vt = f"{tick.code}.{vn_ex.value}"

            # 全域鎖保護快取與局部更新
            with self.tick_cache_lock:
                if vt in self.tick_cache:
                    td = self.tick_cache[vt]
                    # 丟棄過舊事件
                    if td.datetime and tick_dt < td.datetime:
                        return
                else:
                    td = TickData(
                        gateway_name=self.gateway_name,
                        symbol=tick.code,
                        exchange=vn_ex,
                        name=getattr(tick, 'name', tick.code),
                        datetime=tick_dt,
                        localtime=datetime.now()
                    )

                # 僅更新 tick 相關欄位
                td.datetime     = tick_dt
                td.localtime    = datetime.now()
                td.last_price   = float(tick.close)
                td.last_volume  = float(tick.volume)
                td.volume       = float(tick.total_volume)
                td.turnover     = float(getattr(tick, 'total_amount', 0.0))
                td.open_price   = float(tick.open)
                td.high_price   = float(tick.high)
                td.low_price    = float(tick.low)
                td.pre_close    = (float(tick.close - tick.price_chg)
                                if getattr(tick, 'price_chg', None)
                                else td.pre_close)

                # 寫回快取
                self.tick_cache[vt] = td
            
            # 推送事件
            self.on_tick(td)

        except Exception as e:
            code = getattr(tick, 'code', 'N/A')
            self.write_log(
                f"[{thread_name}] _process_tick_stk 失敗({code}): {e}\n"
                f"{traceback.format_exc()}"
            )




    def _process_bidask_fop(self, exchange: SjExchange, bidask: BidAskFOPv1):
        """處理期貨/選擇權 BidAsk 回調：僅更新 bid/ask 欄位，保留其餘欄位並推送 on_tick"""
        thread_name = threading.current_thread().name
        try:
            # 1. 交易所映射
            vn_ex_str = self.sj2vnpy[exchange]
            if not vn_ex_str:
                return
            vn_ex = Exchange(vn_ex_str)

            # 2. 時間戳轉換（含時區）
            if isinstance(bidask.datetime, datetime):
                dt = bidask.datetime.replace(tzinfo=TAIPEI_TZ)  # :contentReference[oaicite:0]{index=0}
            else:
                dt = datetime.fromtimestamp(bidask.datetime / 1e9, tz=TAIPEI_TZ)  # :contentReference[oaicite:1]{index=1}

            vt = f"{bidask.code}.{vn_ex.value}"

            # 3. 全域鎖保護，取出或新建 TickData
            with self.tick_cache_lock:  # :contentReference[oaicite:2]{index=2}
                if vt in self.tick_cache:
                    td = self.tick_cache[vt]
                    # 過舊事件丟棄
                    if td.datetime and dt < td.datetime:  # :contentReference[oaicite:3]{index=3}
                        return
                else:
                    td = TickData(
                        gateway_name=self.gateway_name,
                        symbol=bidask.code,
                        exchange=vn_ex,
                        name=getattr(bidask, 'name', bidask.code),
                        datetime=dt,
                        localtime=datetime.now()
                    )

                # 4. 僅更新 BidAsk 前五檔欄位
                prices = getattr(bidask, 'bid_price', [])
                vols   = getattr(bidask, 'bid_volume', [])
                asks   = getattr(bidask, 'ask_price', [])
                avols  = getattr(bidask, 'ask_volume', [])
                for i in range(5):
                    setattr(td, f"bid_price_{i+1}",
                            float(prices[i]) if i < len(prices)
                            else td.__dict__.get(f"bid_price_{i+1}", 0.0))
                    setattr(td, f"bid_volume_{i+1}",
                            float(vols[i])    if i < len(vols)
                            else td.__dict__.get(f"bid_volume_{i+1}", 0.0))
                    setattr(td, f"ask_price_{i+1}",
                            float(asks[i])    if i < len(asks)
                            else td.__dict__.get(f"ask_price_{i+1}", 0.0))
                    setattr(td, f"ask_volume_{i+1}",
                            float(avols[i])   if i < len(avols)
                            else td.__dict__.get(f"ask_volume_{i+1}", 0.0))

                td.datetime  = dt
                td.localtime = datetime.now()
                # 5. 寫回快取

                self.tick_cache[vt] = td
            # 6. 推送更新後的 TickData
            self.on_tick(td)

        except Exception as e:
            self.write_log(
                f"[{thread_name}] _process_bidask_fop 錯誤: {e}\n"
                f"{traceback.format_exc()}"
            )


    def _process_tick_fop(self, exchange, tick):
        """處理期貨/選擇權 Tick：僅更新 tick 欄位，保留其餘欄位並推 on_tick"""
        thread_name = threading.current_thread().name
        try:
            vn_ex_str = self.sj2vnpy[exchange]
            if not vn_ex_str:
                return
            vn_ex = Exchange(vn_ex_str)

            if isinstance(tick.datetime, datetime):
                tick_dt = tick.datetime.replace(tzinfo=TAIPEI_TZ)
            else:
                tick_dt = datetime.fromtimestamp(tick.datetime / 1e9, tz=TAIPEI_TZ)

            vt = f"{tick.code}.{vn_ex.value}"

            with self.tick_cache_lock:
                if vt in self.tick_cache:
                    td = self.tick_cache[vt]
                    if td.datetime and tick_dt < td.datetime:
                        return
                else:
                    td = TickData(
                        gateway_name=self.gateway_name,
                        symbol=tick.code,
                        exchange=vn_ex,
                        name=getattr(tick, 'name', tick.code),
                        datetime=tick_dt,
                        localtime=datetime.now()
                    )

                td.datetime     = tick_dt
                td.localtime    = datetime.now()
                td.last_price   = float(tick.close)
                td.last_volume  = float(tick.volume)
                td.volume       = float(tick.total_volume)
                td.turnover     = 0.0
                td.open_price   = float(tick.open)
                td.high_price   = float(tick.high)
                td.low_price    = float(tick.low)
                td.pre_close    = (float(tick.close - tick.price_chg)
                                if getattr(tick, 'price_chg', None)
                                else td.pre_close)
                td.open_interest = float(getattr(tick, 'open_interest', td.open_interest))
                td.limit_up      = float(getattr(tick, 'limit_up', td.limit_up))
                td.limit_down    = float(getattr(tick, 'limit_down', td.limit_down))

                self.tick_cache[vt] = td 
            self.on_tick(td)

        except Exception as e:
            self.write_log(
                f"[{thread_name}] _process_tick_fop 失敗: {e}\n"
                f"{traceback.format_exc()}"
            )
    async def _queue_consumer(self):
        """
        從 janus.Queue.async_q 異步取出資料，聚集成批後呼叫 _process_batch。
        """
        while True:
            batch = []
            # 1. 取出第一筆任務，無限等待
            task = await self.janus_queue.async_q.get()  # 非同步取值 :contentReference[oaicite:5]{index=5}
            batch.append(task)
            # 2. 短暫限時再取 (0.1 秒)，以聚集微批次
            while True:
                try:
                    item = await asyncio.wait_for(
                        self.janus_queue.async_q.get(),
                        timeout=0.1  # 超時即退出批次收集 :contentReference[oaicite:6]{index=6}
                    )
                    batch.append(item)
                except asyncio.TimeoutError:
                    break
            # 3. 處理批次
            try:
                await self._process_batch(batch)
            except Exception as e:
                self.write_log(f"[_queue_consumer] 批次處理失敗: {e}")
            finally:
                # 標記已完成所有同步佇列項目
                for _ in batch:
                    self.janus_queue.sync_q.task_done()

    def connect(self, setting: dict):
        """
        連接 Shioaji API (非阻塞)。
        啟動一個工作線程來執行實際的連接、登入、初始化流程。
        """
        # 防止重複連接
        if self.connected or self.logged_in:
            # ===> 增強偵錯：記錄返回原因 <===
            self.write_log("connect: Returning early because already connected or logged in.")
            self.write_log("<=== connect: Method finished (already connected).")
            return

        # ===> 增強偵錯：記錄執行緒狀態 <===
        thread_alive = self.connect_thread and self.connect_thread.is_alive()
        
        # 檢查是否有線程正在運行
        if thread_alive:
            # ===> 增強偵錯：記錄返回原因 <===
            self.write_log("connect: Returning early because connect_thread is still alive.")
            self.write_log("<=== connect: Method finished (thread alive).")
            return

        # 記錄開始連接的時間（用於判斷超時等，可選）
        self.connection_start_time = time.time()
        # 更新設定
        self.connect_setting.update(setting)
        # 啟動連接工作線程
        self.connect_thread = Thread(target=self._connect_worker, args=(self.connect_setting,))
        self.connect_thread.daemon = True # 設置為守護線程，主程序退出時線程也退出
        self.connect_thread.start()

    def _start_reconnect(self) -> None:
        thread = threading.current_thread().name
        self.write_log(f"[{thread}] _start_reconnect ENTER")

        # 原子遞增
        with self._reconnect_lock:
            self.reconnect_attempts += 1
            attempt = self.reconnect_attempts

        if attempt > self.reconnect_limit:
            self.write_log(f"[{thread}] reached max retry, give up")
            self.on_event(EVENT_RECONNECT_FAILED,
                        {"attempts": attempt, "limit": self.reconnect_limit})
            self.close()
            return

        wait = self.reconnect_interval * (2 ** (attempt - 1))
        self.write_log(f"[{thread}] schedule reconnect in {wait}s")

        if self._reconnect_timer and self._reconnect_timer.is_alive():
            self._reconnect_timer.cancel()

        self._reconnect_timer = threading.Timer(wait, self._do_reconnect)
        self._reconnect_timer.daemon = True
        self._reconnect_timer.start()

    def _do_reconnect(self) -> None:
        thread = threading.current_thread().name
        if self.connected or self.logged_in:
            self.write_log(f"[{thread}] already connected, skip")
            return

        try:
            self.connect(self.connect_setting)
        except Exception as ex:
            self.write_log(f"[{thread}] reconnect failed: {ex}")
            self._start_reconnect()   # 若要持續重試

    def _connect_worker(self, setting: dict):
        """
        實際執行連接的工作線程函數。
        包括初始化、登入、激活CA、獲取合約、設置回調。
        """
        self.reconnect_attempts = 0 # 重置重連計數器

        # === 初始化 login 返回的變數，避免 UnboundLocalError ===
        raw_accounts_data = None
        contract_download_flag = False # 預設為 False
        logged_in_person_id = None     # 預設為 None
        needs_download = False         # 預設為 False
        # ======================================================

        try:
            self.api_key = setting.get("APIKey", "")
            self.secret_key = setting.get("SecretKey", "")
            self.ca_path = setting.get("CA路徑", "").replace("\\", "/") # 處理路徑分隔符
            self.ca_passwd = setting.get("CA密碼", "")
            self.person_id_setting = setting.get("身分證字號", "") # 用戶在設定中提供的 ID
            self.simulation = setting.get("simulation", False)
            self.force_download = setting.get("下載合約", False)
            self.reconnect_limit = setting.get("重連次數", 3)
            self.reconnect_interval = setting.get("重連間隔(秒)", 5)


            if not self.api_key or not self.secret_key:
                self.write_log("錯誤：缺少 APIKey 或 SecretKey")
                self._handle_disconnect() # 觸發清理
                self.write_log("<=== _connect_worker: 因缺少 Key 退出 ===")
                return

            self.api = sj.Shioaji(simulation=self.simulation)
            try:
                login_result = self.api.login(
                    api_key=self.api_key,
                    secret_key=self.secret_key,
                    fetch_contract=False, # 登入時通常不下載，後面手動控制
                    subscribe_trade=True,
                    contracts_timeout=0
                )

                try:
                    # *** 假設 login 返回 3 個值 ***
                    raw_accounts_data, contract_download_flag, logged_in_person_id = login_result
                except (TypeError, ValueError):
                    # *** 如果解包失敗，提供預設值 ***
                    raw_accounts_data = login_result
                    contract_download_flag = False # <<<--- 提供預設值
                    logged_in_person_id = None     # <<<--- 提供預設值
                    self.write_log(f"_connect_worker: 使用預設值 contract_download_flag = {contract_download_flag}")

            except SjTokenError as e: # Catch login specific error here
                self.write_log(f"錯誤：Shioaji API 登入失敗 (TokenError): {e}")
                self._handle_disconnect()
                self.write_log("<=== _connect_worker: 因 TokenError 退出 ===")
                return # Stop processing if login fails
            except Exception as e_login: # Catch other potential login errors
                self.write_log(f"錯誤：Shioaji API 登入時發生意外錯誤: {e_login}\n{traceback.format_exc()}")
                self._handle_disconnect()
                self.write_log("<=== _connect_worker: 因登入異常退出 ===")
                return # Stop processing

            save_json(GATEWAY_SETTING_FILENAME, setting)

            accounts_list: List[Union[SjAccount, SjStockAccount, SjFutureAccount]] = []
            if isinstance(raw_accounts_data, list):
                accounts_list = raw_accounts_data
                self.write_log(f"_connect_worker: 標準化完成 - 列表包含 {len(accounts_list)} 個帳戶.")
            elif isinstance(raw_accounts_data, (SjAccount, SjStockAccount, SjFutureAccount)):
                accounts_list = [raw_accounts_data]
                self.write_log("_connect_worker: 標準化完成 - 單個帳戶已放入列表.")
            elif raw_accounts_data is None:
                self.write_log("警告：Shioaji login 返回了 None 作為帳戶數據。")
            else:
                self.write_log(f"錯誤：Shioaji login 返回了非預期的帳戶數據類型: {type(raw_accounts_data)}。無法處理帳戶。")
            self.write_log(f"_connect_worker: 標準化後帳戶列表長度: {len(accounts_list)}")

            # --- 設定預設帳號 ---
            self.write_log("_connect_worker: 正在設定預設帳戶...")
            if not accounts_list:
                self.write_log("警告：登入後未獲得有效的帳戶列表，無法設定預設帳戶。")
            else:
                # --- Debugging: Inspect the normalized list ---
                for i, item in enumerate(accounts_list):
                    self.write_log(f"_connect_worker: 帳戶列表項 {i}: type={type(item)}, value={repr(item)}")
                # --- Debugging END ---
                stock_default_set = False
                future_default_set = False
                for acc in accounts_list:
                    try:
                        if acc.account_type == SjAccountType.Stock and not self.api.stock_account:
                            self.api.set_default_account(acc)
                            stock_default_set = True
                            #self.write_log(f"_connect_worker: 設定預設證券帳號: {acc.account_id}")
                        elif acc.account_type == SjAccountType.Future and not self.api.futopt_account:
                            self.api.set_default_account(acc)
                            future_default_set = True
                            #self.write_log(f"_connect_worker: 設定預設期權帳號: {acc.account_id}")
                        if stock_default_set and future_default_set: # 如果都設好了就不用繼續了
                            break
                    except AttributeError as e_set_acc:
                        self.write_log(f"錯誤：設置預設帳戶時訪問帳戶屬性出錯 (type={type(acc)}): {e_set_acc}. Account data: {repr(acc)}")
                        continue # 跳過此帳戶
                self.write_log("_connect_worker: 預設帳戶設定完成.")


            # --- 3. 下載合約 ---
            self.write_log("_connect_worker: 準備下載合約...")
            # *** === FIX: 在此處定義 needs_download === ***
            # 確保 contract_download_flag 和 self.force_download 都有值
            needs_download = contract_download_flag or self.force_download
            # *** === FIX END === ***

            self.write_log(f"_connect_worker: 合約下載需求: needs_download={needs_download} (來自 login_flag:{contract_download_flag} 或 force_download:{self.force_download})")

            contracts_fetched_successfully = False # 添加標誌位
            try:
                if needs_download:
                    self.write_log("_connect_worker: 調用 fetch_contracts(contract_download=True)...")
                    self.api.fetch_contracts(contract_download=True,contracts_timeout=0, contracts_cb=self._contracts_cb)
                    time.sleep(10) # 等待合約下載完成
                else:
                    self.write_log("_connect_worker: 根據登入結果和設定，跳過主動合約下載。")

                status_after_fetch = self.api.Contracts.status
                self.write_log(f"_connect_worker: fetch_contracts 調用完成或跳過. 合約狀態: {status_after_fetch}")

                if status_after_fetch == SjFetchStatus.Fetched:
                    self.write_log("_connect_worker: 合約狀態為 Fetched，準備處理...")
                    self._process_contracts()
                    contracts_fetched_successfully = True # 標記成功
                elif status_after_fetch == SjFetchStatus.Fetching:
                    self.write_log("警告：合約信息獲取仍在進行中 (Fetching)，可能超時。")
                elif status_after_fetch == SjFetchStatus.NotFetched:
                    self.write_log("警告：合約信息獲取失敗 (NotFetched)，API 未能成功獲取。")
                else:
                    self.write_log(f"警告：合約信息獲取狀態未知或失敗 ({status_after_fetch})，無法處理合約詳情")

                # 如果不需要下載，但之前已下載過，也要處理
                if not needs_download and status_after_fetch == SjFetchStatus.Fetched:
                    if not contracts_fetched_successfully: # 避免重複處理
                        self.write_log("_connect_worker: 檢測到之前已下載的合約，開始處理...")
                        self._process_contracts()
                        contracts_fetched_successfully = True

            except Exception as e_fetch:
                self.write_log(f"錯誤：獲取或處理合約信息時發生異常: {e_fetch}\n詳細錯誤:\n{traceback.format_exc()}")
                # 合約失敗不應直接終止連接，但後續操作會受影響

            # --- 4. 激活 CA 憑證 ---
            self.write_log("_connect_worker: 準備激活 CA...")
            # 確保 logged_in_person_id 已從登入結果中獲取或設為 None
            if not self.simulation and self.ca_path and self.ca_passwd:
                self.write_log("_connect_worker: 檢測到需要激活 CA (非模擬模式且提供了路徑和密碼)")
                try:
                    person_id_to_use = logged_in_person_id if logged_in_person_id else self.person_id_setting
                    if not person_id_to_use:
                        self.write_log("警告：無法確定用於 CA 激活的 Person ID，跳過 CA 激活")
                    else:
                        self.write_log(f"_connect_worker: 調用 activate_ca, PersonID: {person_id_to_use}...")
                        self.api.activate_ca(
                            ca_path=self.ca_path,
                            ca_passwd=self.ca_passwd,
                            person_id=person_id_to_use
                        )
                        self.write_log("Shioaji CA 憑證激活成功")
                        # 重新檢查帳戶簽署狀態
                        self.write_log("激活 CA 後，重新檢查帳戶簽署狀態:")
                        try:
                            # 調用 list_accounts 獲取最新狀態
                            latest_accounts = self.api.list_accounts()
                            if latest_accounts:
                                for acc_latest in latest_accounts:
                                    self.write_log(f"帳號 {acc_latest.account_id} ({acc_latest.account_type.value}) 最新簽署狀態: {acc_latest.signed}")
                            else:
                                self.write_log("激活 CA 後未能獲取到帳戶列表。")
                        except Exception as e_list_ca:
                            self.write_log(f"錯誤：激活 CA 後調用 list_accounts 檢查狀態失敗: {e_list_ca}")

                except Exception as e_ca:
                    self.write_log(f"警告：Shioaji CA 憑證激活失敗: {e_ca}\n{traceback.format_exc()}")
            elif not self.simulation:
                self.write_log("提示：未提供 CA 路徑或密碼 (或處於模擬模式)，跳過 CA 激活")
            else: # Simulation mode
                self.write_log("提示：處於模擬模式，跳過 CA 激活")


            # --- 5. 設置回調函數 ---
            self.write_log("_connect_worker: 正在設置回調函數...")
            self._set_callbacks() # 假設 _set_callbacks 內部有自己的日誌或錯誤處理
            self.write_log("_connect_worker: 回調函數設置完畢.")

            # --- 成功路徑最終檢查點 ---
            self.write_log(f"_connect_worker: 成功路徑檢查點。目前狀態: connected={self.connected}, logged_in={self.logged_in}")

            # 再次確認 API 狀態是否正常 (如果 Shioaji 提供此類方法)
            # if not self.api or not self.api.is_active(): # 替換為實際的狀態檢查方法
            #     self.write_log(f"錯誤：準備完成連接時發現 API 狀態異常。")
            #     raise Exception("API 狀態異常，無法完成連接")

            # --- 6. 更新最終連接狀態 ---
            #self.write_log("_connect_worker: 準備更新最終狀態為 True...")
            self.connected = True
            self.logged_in = True  # <<< --- *** 確保在這裡設置 ***
            #self.write_log("*** 狀態更新: connected=True, logged_in=True ***")
            #self.write_log(f"{self.gateway_name} 接口連接成功")
            self.reconnect_attempts = 0 # 成功後重置重連次數
            self.query_all()

        # --- 最外層異常捕獲 ---
        except Exception as e_outer:
            self.write_log(f"錯誤：Shioaji 連接過程中(最外層捕獲)出錯: {e_outer}\n詳細錯誤:\n{traceback.format_exc()}")
            # 在這裡記錄一下當時的狀態可能也有幫助
            self.write_log(f"錯誤發生時狀態: connected={self.connected}, logged_in={self.logged_in}")
            self._handle_disconnect()
            self.write_log("<=== _connect_worker: 因最外層異常退出 ===")
            # 確保線程退出
            return
    def close(self):
        """關閉連接"""
        if not self.logged_in and not self.connected:
            self.write_log("接口未連接，無需斷開")
            return

        self.write_log("正在斷開 Shioaji API 連接...")

        # 停止可能的重連嘗試 (如果正在進行中)
        # 可以通過設置一個標誌位或取消定時器來實現，這裡暫不添加複雜邏輯

        # 登出 API
        if self.logged_in and self.api:
            try:
                self.api.logout()
                self.write_log("Shioaji API 已登出")
            except Exception as e:
                self.write_log(f"Shioaji API 登出時發生錯誤: {e}")

        # 清理狀態和資源
        self.api = None
        self.connected = False
        self.logged_in = False
        self.reconnect_attempts = 0 # 重置計數器

        # 清理內部數據緩存 (可選，看是否需要在斷開時清除)
        with self.order_map_lock:
            self.orders.clear()
            self.shioaji_trades.clear()
            self.shioaji_deals.clear()
        with self.position_lock:
            self.positions.clear()
        with self.account_lock:
            self.accounts.clear()
        with self.contract_lock:
            self.contracts.clear()

        self.event_engine.unregister(EVENT_TIMER, self.process_timer_event)
        self.write_log(f"{self.gateway_name} 接口連接已斷開")

    def _set_callbacks(self):
        """設置 Shioaji 的所有回調函數"""


        if not self.api:
            self.write_log("警告：_set_callbacks: self.api 未初始化，無法設置回調。")
            self.write_log("<=== _set_callbacks: 因 API 未初始化退出 ===")
            return

        try:
            # 行情回調 (Tick 和 BidAsk)
            self.write_log("_set_callbacks: 正在設置行情回調...")
            self.api.quote.set_on_tick_stk_v1_callback(self._on_tick_stk)
            self.api.quote.set_on_tick_fop_v1_callback(self._on_tick_fop)
            self.api.quote.set_on_bidask_stk_v1_callback(self._on_bidask_stk)
            self.api.quote.set_on_bidask_fop_v1_callback(self._on_bidask_fop)
            self.write_log("_set_callbacks: 行情回調設置完成.")

            # 訂單和成交回調
            self.write_log("_set_callbacks: 正在設置訂單/成交回調...")
            self.api.set_order_callback(self._on_order_deal_shioaji)
            self.write_log("_set_callbacks: 訂單/成交回調設置完成.")


            # 連接狀態回調 (如果 Shioaji 版本支持)
            self.write_log("_set_callbacks: 檢查 Session Down 回調支持...")
            if hasattr(self.api, "set_session_down_callback"):
                self.write_log("_set_callbacks: 支持 Session Down 回調，正在設置...")
                self.api.set_session_down_callback(self._on_session_down)
                self.write_log("_set_callbacks: 已設置 Session Down 回調.") # 原有日誌保留
            else:
                self.write_log("_set_callbacks: 當前 Shioaji 版本不支持 Session Down 回調。")


            # 其他事件回調 (可選)
            # self.api.set_event_callback(self._on_event_shioaji)

            self.write_log("Shioaji 回調函數設置完畢") # 保留原有總結日誌
            self.write_log("<=== _set_callbacks: 回調設置成功結束 ===") # 函數成功退出

        except Exception as e:
            # ===> 增強偵錯：記錄詳細錯誤 <===
            self.write_log(f"錯誤：設置 Shioaji 回調函數時出錯: {e}\n詳細錯誤:\n{traceback.format_exc()}")
            self.write_log("<=== _set_callbacks: 因異常退出 ===") # 函數異常退出

    def _on_session_down(self):
        """處理 Shioaji 連接斷開事件的回調"""
        thread_name = threading.current_thread().name # 獲取執行緒名稱，瞭解是否在不同執行緒觸發
        # ===> 增強偵錯：記錄函數進入 <===
        self.write_log(f"[{thread_name}] ===> _on_session_down: 回調觸發 ===")
        self.write_log(f"[{thread_name}] 警告：檢測到 Shioaji Session Down (來自回調)") # 保留原有警告

        # 觸發統一的斷線處理邏輯
        self.write_log(f"[{thread_name}] _on_session_down: 準備調用 _handle_disconnect...")
        self._handle_disconnect()
        # ===> 增強偵錯：記錄函數退出 <===
        self.write_log(f"[{thread_name}] <=== _on_session_down: 處理完畢 ===")

        
    def _handle_disconnect(self):
        """統一處理斷開連接（主動或被動）"""
        thread_name = threading.current_thread().name
        # ===> 增強偵錯：記錄函數進入和調用來源 <===
        self.write_log(f"[{thread_name}] ===> _handle_disconnect: 進入處理 ***")
        # 記錄調用堆疊，幫助判斷是哪個流程觸發了斷線處理
        # traceback.format_stack() 返回列表，用 ''.join() 合併成字串
        self.write_log(f"[{thread_name}] _handle_disconnect: 調用來源:\n{''.join(traceback.format_stack(limit=5))}") # 限制堆疊深度

        # ===> 增強偵錯：記錄檢查時的狀態 <===
        current_connected = self.connected
        current_logged_in = self.logged_in
        self.write_log(f"[{thread_name}] _handle_disconnect: 檢查點 - connected={current_connected}, logged_in={current_logged_in}")

        # 檢查是否真的需要處理斷線（防止重複觸發）
        if not current_connected and not current_logged_in:
            self.write_log(f"[{thread_name}] _handle_disconnect: 狀態已是未連接，無需處理，提前返回。")
            self.write_log(f"[{thread_name}] <=== _handle_disconnect: 結束 (無需處理) ===")
            return

        was_connected = current_connected # 使用檢查時的狀態
        # ===> 增強偵錯：記錄狀態變更前後 <===
        self.write_log(f"[{thread_name}] _handle_disconnect: 準備設置狀態為 False。原狀態: connected={current_connected}, logged_in={current_logged_in}")
        self.connected = False
        self.logged_in = False
        #self.write_log(f"[{thread_name}] *** 狀態更新: connected=False, logged_in=False ***")
        #self.write_log(f"[{thread_name}] _handle_disconnect: 接口連接狀態已設為斷開")

        # 如果之前是成功連接狀態，則啟動重連機制
        self.write_log(f"[{thread_name}] _handle_disconnect: 檢查是否需要重連 (was_connected={was_connected})")
        if was_connected:
            self.write_log(f"[{thread_name}] _handle_disconnect: 需要重連，調用 _start_reconnect...")
            self._start_reconnect()
        else:
            self.write_log(f"[{thread_name}] _handle_disconnect: 不需要重連。")

        # ===> 增強偵錯：記錄函數退出 <===
        self.write_log(f"[{thread_name}] <=== _handle_disconnect: 處理完畢 ===")

    def _check_connection(self, check_ca: bool = False) -> bool:
        """檢查 API 是否已連接並登入，可選檢查 CA 簽署狀態"""
        self.write_log(f"_check_connection called: connected={self.connected}, logged_in={self.logged_in}, api_exists={self.api is not None}")
        if not self.connected or not self.logged_in or not self.api:
            self.write_log("錯誤：Shioaji API 未連接或未登入")
            return False
        

        # 如果需要檢查 CA (如下單、撤單、查詢帳務持倉等操作)
        if check_ca and not self.simulation:
            # 獲取需要操作的帳戶 (這裡需要根據操作的目標來判斷)
            # 簡化：檢查預設帳戶的簽署狀態
            stock_signed = self.api.stock_account and self.api.stock_account.signed
            futopt_signed = self.api.futopt_account and self.api.futopt_account.signed

            # 如果涉及股票操作但股票帳戶未簽署，或涉及期權操作但期權帳戶未簽署
            # (這裡的判斷邏輯需要根據實際操作細化)
            if not (stock_signed or futopt_signed): # 簡化：至少有一個簽署
                 self.write_log("警告：需要 CA 簽署的操作，但相關帳戶未檢測到有效簽署狀態")
                 # return False # 可以選擇返回 False 阻止操作
                 # 或者只是警告，讓 API 調用自己失敗
        return True



    def query_all(self):
        """連線成功→啟動一次性查詢並掛定時輪詢"""
        self.query_account()
        self.query_position()
        self.init_query()     

    def init_query(self):
        self._query_functions = [self.query_account, self.query_position]
        self._query_trigger = 0
        self.query_interval_account = 2      # 秒
        self.query_interval_position = 5
        self.event_engine.register(EVENT_TIMER, self.process_timer_event)

    def process_timer_event(self, event: Event):
        self._query_trigger += 1
        if self._query_trigger % self.query_interval_account == 0:
            self.query_account()
        if self._query_trigger % self.query_interval_position == 0:
            self.query_position()

    def find_sj_contract(self, symbol: str, vn_exchange_str: Optional[str] = None) -> Optional[SjContract]:
        """
        根據 VnPy 的 symbol 和 exchange 字串查找對應的 Shioaji Contract 物件。
        """
        # ===> 增強偵錯：記錄函數進入和參數 <===
        self.write_log(f"===> find_sj_contract: Finding contract for symbol='{symbol}', vn_exchange_str='{vn_exchange_str}'")

        # ===> 增強偵錯：詳細檢查 API 和合約狀態 <===
        if not self.api:
            self.write_log("錯誤：find_sj_contract: self.api is None.")
            self.write_log("<=== find_sj_contract: Returning None (API None)")
            return None

        current_contract_status = self.api.Contracts.status
        self.write_log(f"find_sj_contract: Current self.api.Contracts.status = {current_contract_status} ({repr(current_contract_status)})")

        if current_contract_status != SjFetchStatus.Fetched:
            # 保留原有日誌，但現在知道狀態不是 Fetched
            self.write_log("錯誤：無法查找合約，合約尚未下載完成 (狀態非 Fetched)")
            self.write_log(f"<=== find_sj_contract: Returning None (Status not Fetched: {current_contract_status})")
            return None
        # ===> 增強偵錯結束 <===


        # 如果沒有提供交易所，則嘗試在所有合約中搜索代碼 (效率較低)
        if not vn_exchange_str:
            self.write_log(f"警告：find_sj_contract: 未指定交易所查找合約 {symbol}，將在所有合約中搜索...")
            # ===> 增強偵錯：記錄查找過程 <===
            target_contract = None
            try:
                target_contract = self.api.Contracts.Stocks._code2contract.get(symbol)
                if not target_contract:
                    target_contract = self.api.Contracts.Futures._code2contract.get(symbol)
                    if not target_contract:
                        target_contract = self.api.Contracts.Options._code2contract.get(symbol)
                        if not target_contract:
                            self.write_log(f"  在 Options 中未找到 {symbol}，合約不存在或尚未下載。")
            except AttributeError as e_find_all:
                self.write_log(f"錯誤：find_sj_contract: 在所有合約中搜索時發生 AttributeError: {e_find_all}")
            except Exception as e_find_all_other:
                self.write_log(f"錯誤：find_sj_contract: 在所有合約中搜索時發生未知錯誤: {e_find_all_other}")

            if target_contract:
                self.write_log(f"find_sj_contract: 在所有合約中找到 {symbol}")
                self.write_log("<=== find_sj_contract: Returning contract (found in all)")
                return target_contract
            else:
                self.write_log(f"錯誤：find_sj_contract: 在所有合約中都找不到代碼為 {symbol} 的合約")
                self.write_log("<=== find_sj_contract: Returning None (not found in all)")
                return None

        # 如果提供了交易所，則進行更精確的查找
        sj_exchange: Optional[SjExchange] = self.vn2sj[vn_exchange_str]
        if not sj_exchange:
            self.write_log(f"錯誤：find_sj_contract: 不支持的交易所字串 {vn_exchange_str}")
            self.write_log("<=== find_sj_contract: Returning None (invalid exchange string)")
            return None

        target_contract: Optional[SjContract] = None
        self.write_log(f"find_sj_contract: Searching for '{symbol}' in specific exchange '{sj_exchange.value}'")
        try:
            # ===> 增強偵錯：記錄具體查找步驟 <===
            if sj_exchange in [SjExchange.TSE, SjExchange.OTC, SjExchange.OES]:
                self.write_log(f"  Looking in Stocks ({sj_exchange.value})...")
                exchange_contracts = getattr(self.api.Contracts.Stocks, sj_exchange.value, None)
                if exchange_contracts and hasattr(exchange_contracts, '_code2contract'):
                    target_contract = exchange_contracts._code2contract.get(symbol)
                    self.write_log(f"  Stocks lookup result for '{symbol}': {'Found' if target_contract else 'Not Found'}")
                else:
                    self.write_log(f"  Stocks lookup failed: exchange_contracts (type: {type(exchange_contracts)}) or _code2contract missing.")
            elif sj_exchange == SjExchange.TAIFEX:
                self.write_log("  Looking in Futures...")
                if hasattr(self.api.Contracts, 'Futures') and hasattr(self.api.Contracts.Futures, '_code2contract'):
                    target_contract = self.api.Contracts.Futures._code2contract.get(symbol) # Shioaji v0.3.x 結構
                    self.write_log(f"  Futures lookup result for '{symbol}': {'Found' if target_contract else 'Not Found'}")
                else:
                    self.write_log("  Futures lookup failed: api.Contracts.Futures or _code2contract missing.")

                if not target_contract:
                    self.write_log(f"  '{symbol}' not in Futures, looking in Options...")
                    if hasattr(self.api.Contracts, 'Options') and hasattr(self.api.Contracts.Options, '_code2contract'):
                        target_contract = self.api.Contracts.Options._code2contract.get(symbol) # Shioaji v0.3.x 結構
                        self.write_log(f"  Options lookup result for '{symbol}': {'Found' if target_contract else 'Not Found'}")
                    else:
                        self.write_log("  Options lookup failed: api.Contracts.Options or _code2contract missing.")
            # ===> 增強偵錯結束 <===

        except AttributeError as e:
            self.write_log(f"錯誤：find_sj_contract: 查找特定交易所合約時發生 AttributeError: {e}")
        except Exception as e_find_specific:
            self.write_log(f"錯誤：find_sj_contract: 查找特定交易所合約時發生未知錯誤: {e_find_specific}\n{traceback.format_exc()}")

        if not target_contract:
            self.write_log(f"警告：find_sj_contract: 在 {vn_exchange_str} ({sj_exchange.value}) 中找不到合約 {symbol}")
            self.write_log("<=== find_sj_contract: Returning None (not found in specific exchange)")
            return None
        else:
            self.write_log(f"find_sj_contract: 成功找到合約 {symbol} @ {vn_exchange_str}")
            self.write_log("<=== find_sj_contract: Returning contract object")
            return target_contract

    def get_product_type(self, symbol: str, vn_exchange_str: str) -> Optional[Product]:
        """根據 symbol 和 exchange 字串判斷 VnPy Product 類型"""
        sj_contract = self.find_sj_contract(symbol, vn_exchange_str)
        if sj_contract:
            return PRODUCT_MAP_REVERSE.get(sj_contract.security_type)
        else:
            # 如果找不到合約，嘗試根據交易所猜測 (不推薦，但作為後備)
            if vn_exchange_str == "TAIFEX":
                 # 無法區分期貨還是選擇權
                 return Product.FUTURES # 或者返回 None
            elif vn_exchange_str in ["TSE", "OTC", "OES"]:
                 return Product.EQUITY
            else:
                 return None
            
    def subscribe(self, req: SubscribeRequest) -> None:
        vt = f"{req.symbol}.{req.exchange.value}"
        if not self._check_connection():
            self.write_log(f"訂閱失敗：連線檢查未通過 {vt}")
            self.on_event(EVENT_SUBSCRIBE_FAILED, vt)
            return

        contract = self.find_sj_contract(req.symbol, req.exchange.value)
        if not contract:
            self.write_log(f"訂閱失敗：找不到合約 {vt}")
            self.on_event(EVENT_SUBSCRIBE_FAILED, vt)
            return

        MAX_CALLS = 190                       # 留 10 個安全緩衝
        with self.subscribed_lock:
            if len(self.subscribed) >= MAX_CALLS:
                self.write_log(f"Skip subscribe {vt}, quota exhausted")
                self.on_event(EVENT_SUBSCRIBE_FAILED, vt)
                return

        with self.subscribed_lock:
            if vt in self.subscribed:
                self.write_log(f"已重複訂閱 {vt}")
                self.on_event(EVENT_SUBSCRIBE_SUCCESS, vt)
                return

        try:
            self.api.quote.subscribe(contract, quote_type=SjQuoteType.Tick, version=SjQuoteVersion.v1)
            self.api.quote.subscribe(contract, quote_type=SjQuoteType.BidAsk, version=SjQuoteVersion.v1)
            with self.subscribed_lock:
                self.subscribed.add(vt)
            self.on_event(EVENT_SUBSCRIBE_SUCCESS, vt)
        except Exception as ex:
            self.write_log(f"訂閱異常 {vt}: {ex}")
            self.on_event(EVENT_SUBSCRIBE_FAILED, vt)

    def cancel_order(self, req: CancelRequest) -> None:
        vt = req.orderid
        if not self._check_connection(check_ca=True):
            self.write_log(f"撤單失敗，連線或 CA 未簽署：{vt}")
            order = req.create_order_data(f"{self.gateway_name}.CANCEL_REJECT")
            order.status    = Status.REJECTED
            order.reference = "連線或 CA 未簽署"
            self.on_order(order)
            return

        parts = vt.split('.')
        if len(parts) != 2 or parts[0] != self.gateway_name:
            self.write_log(f"撤單失敗，OrderID 格式錯誤：{vt}")
            order = req.create_order_data(f"{self.gateway_name}.CANCEL_REJECT")
            order.status    = Status.REJECTED
            order.reference = "OrderID 格式錯誤"
            self.on_order(order)
            return

        seqno = parts[1]
        with self.order_map_lock:
            trade = self.shioaji_trades.get(seqno)
            order = self.orders.get(vt)

        if not trade:
            self.write_log(f"撤單失敗，找不到 Trade 記錄：{vt}")
            if order:
                order.status    = Status.REJECTED
                order.reference = "找不到 Trade 記錄"
                self.on_order(order)
            return

        if order and not order.is_active():
            self.write_log(f"訂單非活躍狀態，跳過撤單：{vt} (狀態 {order.status})")
            return

        try:
            self.api.cancel_order(trade)
            self.write_log(f"撤單請求已發送：{vt}")
            self.on_order(order)
        except Exception as e:
            self.write_log(f"撤單異常：{vt}，原因：{e}")
            if order:
                order.status    = Status.REJECTED
                order.reference = f"撤單失敗: {e}"
                self.on_order(order)

    def _on_cancel_submitted(self, event: Event) -> None:
        vt = event.data
        self.write_log(f"[Event] 撤單請求送出：{vt}")

    def send_order(self, req: OrderRequest, **kwargs) -> str:
        """
        發送下單請求。
        通過調用輔助函數準備特定產品的參數。
        允許通過關鍵字參數 `order_lot`, `order_cond`, `custom_field` 傳遞額外信息。
        """
        # 0. 檢查連接和 CA 狀態
        if not self._check_connection(check_ca=True):
            # ... 返回 REJECTED OrderData ... (同之前的實現)
            order = req.create_order_data(f"{self.gateway_name}.CONN_REJECT_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
            order.status = Status.REJECTED
            order.reference = "接口未連接或 CA 未簽署"
            self.on_order(order)
            return order.vt_orderid

        # 1. 查找 Shioaji 合約對象
        vn_exchange_str = req.exchange.value
        symbol = req.symbol
        vt_symbol = f"{symbol}.{vn_exchange_str}"
        sj_contract = self.find_sj_contract(symbol, vn_exchange_str)

        if not sj_contract:
            # ... 返回 REJECTED OrderData ... (同之前的實現)
            order = req.create_order_data(f"{self.gateway_name}.NO_CONTRACT_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
            order.status = Status.REJECTED
            order.reference = f"找不到合約 {vt_symbol}"
            self.on_order(order)
            return order.vt_orderid

        # 2. 確定產品類型
        product = PRODUCT_MAP_REVERSE.get(sj_contract.security_type)
        if not product:
             # ... 返回 REJECTED OrderData ... (同之前的實現)
             order = req.create_order_data(f"{self.gateway_name}.NO_PRODUCT_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
             order.status = Status.REJECTED
             order.reference = f"無法確定產品類型 {sj_contract.security_type}"
             self.on_order(order)
             return order.vt_orderid

        # 3. 調用輔助函數準備 Shioaji Order 參數
        order_args: Optional[Dict[str, Any]] = None
        if product == Product.EQUITY:
            order_args = self._prepare_stock_order_args(req, **kwargs)
        elif product in [Product.FUTURES, Product.OPTION]:
            order_args = self._prepare_futures_order_args(req, **kwargs)
        else:
             self.write_log(f"錯誤：不支持的產品類型 {product.value} 無法下單")
             # ... 返回 REJECTED OrderData ...
             order = req.create_order_data(f"{self.gateway_name}.PROD_REJECT_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
             order.status = Status.REJECTED
             order.reference = f"不支持的產品類型 {product.value}"
             self.on_order(order)
             return order.vt_orderid

        # 如果參數準備失敗 (輔助函數返回 None)
        if order_args is None:
             self.write_log(f"錯誤：準備訂單參數失敗 ({vt_symbol}, Type: {req.type.value})")
             # ... 返回 REJECTED OrderData ...
             order = req.create_order_data(f"{self.gateway_name}.PREP_ERR_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
             order.status = Status.REJECTED
             order.reference = "準備訂單參數失敗"
             self.on_order(order)
             return order.vt_orderid

        # 添加通用參數: 帳戶
        order_args["account"] = self.api.stock_account if product == Product.EQUITY else self.api.futopt_account
        if not order_args["account"]:
             self.write_log(f"錯誤：找不到對應產品的預設帳戶 ({product.value})")
             # ... 返回 REJECTED OrderData ...
             order = req.create_order_data(f"{self.gateway_name}.NO_ACCOUNT_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
             order.status = Status.REJECTED
             order.reference = "找不到交易帳戶"
             self.on_order(order)
             return order.vt_orderid


        # 4. 創建 Shioaji Order 物件並發送
        try:
            sj_order = self.api.Order(**order_args)
            self.write_log(f"準備發送 Shioaji Order: {sj_order}")

        except Exception as e_build:
             self.write_log(f"錯誤：構建 Shioaji Order 物件失敗: {e_build}\nArgs: {order_args}\n{traceback.format_exc()}")
             # ... 返回 REJECTED OrderData ...
             order = req.create_order_data(f"{self.gateway_name}.BUILD_ERR_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
             order.status = Status.REJECTED
             order.reference = f"構建 Shioaji Order 失敗: {e_build}"
             self.on_order(order)
             return order.vt_orderid

        try:
            trade_resp: Optional[SjTrade] = self.api.place_order(sj_contract, sj_order)
            self.write_log(f"Shioaji place_order 調用成功返回 Trade 物件: {repr(trade_resp)}") # 先記錄返回的原始物件

            # 5. *** 修改：先處理緩存和推送，再記錄最終成功日誌 ***
            if trade_resp and trade_resp.order and trade_resp.status:
                shioaji_seqno = trade_resp.order.seqno
                # ===> 確保使用正確的 gateway_name 生成 ID <===
                vt_orderid = f"{self.gateway_name}.{shioaji_seqno}"

                with self.order_map_lock:
                    # ===> 關鍵：先創建並緩存 OrderData 和 Trade <===
                    order = OrderData(
                        gateway_name=self.gateway_name,
                        symbol=symbol, # 使用傳入的 symbol
                        exchange=req.exchange, # 使用傳入的 req.exchange
                        orderid=vt_orderid,    # 使用生成的 vt_orderid
                        type=req.type,
                        direction=req.direction,
                        offset=req.offset,
                        price=req.price,
                        volume=req.volume,
                        traded=float(trade_resp.status.deal_quantity),
                        # 使用同步返回的狀態做初始狀態，映射失敗則用 SUBMITTING
                        status=STATUS_MAP.get(trade_resp.status.status, Status.SUBMITTING),
                        datetime=trade_resp.status.order_datetime.replace(tzinfo=TAIPEI_TZ) if trade_resp.status.order_datetime else datetime.now(TAIPEI_TZ),
                        reference=trade_resp.status.msg
                    )
                    self.orders[vt_orderid] = order # 存入 OrderData 緩存
                    self.shioaji_trades[shioaji_seqno] = trade_resp # 存入 Shioaji Trade 緩存
                    self.write_log(f"send_order: Cached OrderData and SjTrade for {vt_orderid}")
                    # ===> 緩存完成 <===

                # ===> 然後再推送和記錄最終成功日誌 <===
                self.write_log(f"準備推送 OrderData (Initial): {repr(order)}") # 推送前記錄
                self.on_order(copy.copy(order)) # 推送副本
                self.write_log(f"訂單發送成功，VnPy OrderID: {vt_orderid}, Shioaji SeqNo: {shioaji_seqno}") # 在所有操作完成後記錄
                return vt_orderid # 返回 ID
            else:
                self.write_log(f"錯誤：Shioaji place_order 未返回有效的 Trade/Order/Status 物件。Response: {repr(trade_resp)}")
                # ... 返回 REJECTED OrderData ... (保持不變)
                order = req.create_order_data(f"{self.gateway_name}.RESP_ERR_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
                order.status = Status.REJECTED
                order.reference = "place_order 未返回有效對象"
                self.on_order(order)
                return order.vt_orderid

        except SjAccountNotSignError as e_sign:
             self.write_log(f"錯誤：下單失敗，帳戶未簽署 CA: {e_sign}")
             # ... 返回 REJECTED OrderData ...
             order = req.create_order_data(f"{self.gateway_name}.SIGN_ERR_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
             order.status = Status.REJECTED
             order.reference = f"帳戶未簽署: {e_sign}"
             self.on_order(order)
             return order.vt_orderid
        except Exception as e_place:
            self.write_log(f"錯誤：調用 Shioaji place_order 失敗: {e_place}\n{traceback.format_exc()}")
            # ... 返回 REJECTED OrderData ...
            order = req.create_order_data(f"{self.gateway_name}.API_ERR_{datetime.now().strftime('%H%M%S_%f')}", self.gateway_name)
            order.status = Status.REJECTED
            order.reference = f"place_order 調用失敗: {e_place}"
            self.on_order(order)
            return order.vt_orderid

    def _prepare_stock_order_args(
        self, req: OrderRequest, **kwargs
    ) -> Optional[Dict[str, Any]]:
        """準備股票訂單所需的 Shioaji Order 參數"""
        sj_price_type: Optional[SjStockPriceType] = None
        sj_order_type: SjOrderType = SjOrderType.ROD  # 預設 ROD
        sj_order_cond: Optional[SjStockOrderCond] = None
        sj_order_lot: SjStockOrderLot = SjStockOrderLot.Common # 預設整股
        daytrade_short_flag: bool = False

        # 決定 PriceType 和 OrderType (ROD/IOC/FOK)
        if req.type == OrderType.LIMIT:
            sj_price_type = SjStockPriceType.LMT
            sj_order_type = SjOrderType.ROD
        elif req.type == OrderType.MARKET:
            sj_price_type = SjStockPriceType.MKT
            sj_order_type = SjOrderType.IOC
        elif req.type == OrderType.FAK:
             if req.price is None or req.price <= 0:
                  self.write_log("錯誤：股票 FAK 訂單必須指定有效的限價")
                  return None
             sj_price_type = SjStockPriceType.LMT
             sj_order_type = SjOrderType.IOC
        elif req.type == OrderType.FOK:
             if req.price is None or req.price <= 0:
                  self.write_log("錯誤：股票 FOK 訂單必須指定有效的限價")
                  return None
             sj_price_type = SjStockPriceType.LMT
             sj_order_type = SjOrderType.FOK
        elif req.type in [OrderType.STOP, OrderType.RFQ]:
             self.write_log(f"錯誤：股票訂單不支持 {req.type.value} 類型")
             return None
        else:
             self.write_log(f"錯誤：不支持的股票訂單類型 {req.type.value}")
             return None

        # 決定 StockOrderCond 和 daytrade_short_flag
        if req.offset == Offset.OPEN:
            if req.direction == Direction.LONG:
                custom_cond = kwargs.get("order_cond")
                if custom_cond == SjStockOrderCond.MarginTrading:
                    sj_order_cond = SjStockOrderCond.MarginTrading
                    self.write_log("開多倉，使用條件：融資 (MarginTrading) - 來自指定")
                else:
                    sj_order_cond = SjStockOrderCond.Cash
                    if custom_cond is not None:
                         self.write_log(f"警告：收到無效的開多倉 order_cond: {custom_cond}，使用預設 Cash")
            else: # SHORT
                sj_order_cond = SjStockOrderCond.ShortSelling
        elif req.offset == Offset.CLOSETODAY:
            sj_order_cond = SjStockOrderCond.Cash
            daytrade_short_flag = True
            self.write_log("檢測到平今倉 (當沖) 操作，設置 daytrade_short=True")
        elif req.offset in [Offset.CLOSE, Offset.CLOSEYESTERDAY]:
             custom_cond = kwargs.get("order_cond")
             if custom_cond == SjStockOrderCond.MarginTrading and req.direction == Direction.SHORT: # 平融資多單
                  sj_order_cond = SjStockOrderCond.MarginTrading
                  self.write_log("平多倉，使用條件：融資 (MarginTrading) - 來自指定")
             elif custom_cond == SjStockOrderCond.ShortSelling and req.direction == Direction.LONG: # 平融券空單 (回補)
                  sj_order_cond = SjStockOrderCond.ShortSelling
                  self.write_log("平空倉，使用條件：融券 (ShortSelling) - 來自指定")
             else:
                  sj_order_cond = SjStockOrderCond.Cash # 預設用現股平倉
                  if custom_cond is not None:
                       self.write_log(f"警告：收到無效的平倉 order_cond: {custom_cond}，使用預設 Cash")
        else: # Offset.NONE 或其他
            sj_order_cond = SjStockOrderCond.Cash

        # 處理 StockOrderLot
        custom_order_lot = kwargs.get("order_lot")
        if isinstance(custom_order_lot, SjStockOrderLot):
            sj_order_lot = custom_order_lot
        elif custom_order_lot is not None:
            self.write_log(f"警告：收到無效的自訂 order_lot 參數: {custom_order_lot}，使用預設 Common")

        # (可選) 驗證數量與 Lot
        if sj_order_lot == SjStockOrderLot.Common and int(req.volume) % 1000 != 0:
            self.write_log(f"警告：整股交易數量 ({req.volume}) 可能不是 1000 的倍數")
        elif sj_order_lot in [SjStockOrderLot.Odd, SjStockOrderLot.IntradayOdd] and int(req.volume) >= 1000:
             self.write_log(f"警告：零股交易數量 ({req.volume}) 可能不應大於等於 1000")


        # 構建參數字典
        args = {
            "price": req.price,
            "quantity": int(req.volume),
            "action": DIRECTION_MAP.get(req.direction),
            "price_type": sj_price_type,
            "order_type": sj_order_type,
            "order_cond": sj_order_cond,
            "order_lot": sj_order_lot,
        }
        if daytrade_short_flag:
            args["daytrade_short"] = True

        # 可以在這裡添加 custom_field 等其他股票特定參數
        custom_field = kwargs.get("custom_field")
        if isinstance(custom_field, str) and len(custom_field) <= 6:
            args["custom_field"] = custom_field


        # 檢查必要參數是否存在
        if args["action"] is None or args["price_type"] is None or args["order_cond"] is None:
            self.write_log(f"錯誤：準備股票訂單參數時缺少必要欄位: {args}")
            return None

        return args

    def _prepare_futures_order_args(
        self, req: OrderRequest, **kwargs
    ) -> Optional[Dict[str, Any]]:
        """準備期貨/選擇權訂單所需的 Shioaji Order 參數"""
        order_type_params = ORDER_TYPE_FUTURES_VT2SJ.get(req.type)

        if not order_type_params:
            if req.type in [OrderType.STOP, OrderType.RFQ]:
                 self.write_log(f"錯誤：期貨/選擇權訂單不支持 {req.type.value} 類型")
            else:
                 self.write_log(f"錯誤：不支持的期貨/選擇權訂單類型 {req.type.value}")
            return None # 準備失敗

        sj_price_type, sj_order_type = order_type_params
        sj_octype = FUTURES_OFFSET_MAP.get(req.offset, SjFuturesOCType.Auto)

        # 構建參數字典  
        args = {
            "price": req.price,
            "quantity": int(req.volume),
            "action": DIRECTION_MAP.get(req.direction),
            "price_type": sj_price_type,
            "order_type": sj_order_type,
            "octype": sj_octype,
        }

         # 可以在這裡添加 custom_field 等其他期貨特定參數
        custom_field = kwargs.get("custom_field")
        if isinstance(custom_field, str) and len(custom_field) <= 6:
            args["custom_field"] = custom_field

        # 檢查必要參數是否存在
        if args["action"] is None or args["price_type"] is None or args["octype"] is None:
            self.write_log(f"錯誤：準備期貨訂單參數時缺少必要欄位: {args}")
            return None

        return args
    

    # --- 訂單/成交回調處理 ---
    def _on_order_deal_shioaji(self, state: SjOrderState, message: dict):
        """
        訂單/成交回調：
        1. 優先調用 api.update_status(account=...) 嘗試同步拉取最新狀態；
        2. 若 update_status 失敗或未返回，則從 message 推斷狀態；
        3. 最後拆成 order_update 與 trade 兩個事件送出。
        """
        thread_name = threading.current_thread().name
        try:
            # 1. 提取 SeqNo
            if state in [SjOrderState.StockDeal, SjOrderState.FuturesDeal]:
                seqno = message.get("seqno") or message.get("trade_id")
            else:
                seqno = (message.get("order", {}) or {}).get("seqno") \
                     or (message.get("status", {}) or {}).get("id")
            if not seqno:
                self.write_log(f"[{thread_name}] 無法解析 SeqNo，跳過回調: {message}")
                return

            vt_orderid = f"{self.gateway_name}.{seqno}"
            self.write_log(f"[{thread_name}] 處理訂單回調: {vt_orderid}, State={state.value}")

            # 2. 從緩存取出
            with self.order_map_lock:
                original_trade = self.shioaji_trades.get(seqno)
                order = self.orders.get(vt_orderid)

            if not order:
                self.write_log(f"[{thread_name}] 找不到 OrderData 緩存: {vt_orderid}")
                return

            # 3. 嘗試 api.update_status
            status_updated = False
            if original_trade and original_trade.order and original_trade.order.account:
                acct = original_trade.order.account
                try:
                    self.api.update_status(account=acct)
                    if original_trade.status and original_trade.status.status:
                        status_updated = True
                except Exception as ex:
                    self.write_log(f"[{thread_name}] update_status 失敗: {ex}")

            # 4. 確定最終狀態與成交量
            final_status = None
            final_qty = order.traded
            final_msg = order.reference

            if status_updated:
                sj_stat = original_trade.status
                final_status = sj_stat.status
                final_qty = float(sj_stat.deal_quantity)
                final_msg = sj_stat.msg
            else:
                # fallback 推斷
                st = message.get("status", {})
                deals = float(st.get("deal_quantity", order.traded))
                op   = message.get("operation", {})
                code = op.get("op_code")
                if state in [SjOrderState.StockDeal, SjOrderState.FuturesDeal]:
                    final_status = SjStatus.Filled if deals >= order.volume else SjStatus.PartFilled
                    final_qty = deals
                elif op.get("op_type") == "Cancel" and code == "00":
                    final_status = SjStatus.Cancelled
                elif op.get("op_type") == "New" and code == "00":
                    final_status = SjStatus.Submitted
                elif code and code != "00":
                    final_status = SjStatus.Failed
                final_msg = op.get("op_msg") or st.get("msg") or final_msg

            # 5. 映射並發 order_update 事件
            if final_status:
                vn_stat = STATUS_MAP.get(final_status, None)
                if vn_stat and (vn_stat != order.status or final_qty > order.traded):
                    with self.order_map_lock:
                        order.status = vn_stat
                        order.traded = final_qty
                        order.reference = final_msg
                    self.on_order(order)
                    self.write_log(f"[{thread_name}] 發送 order_update 事件: {order.vt_orderid} → {order.status}")

            # 6. 若為成交回調，再拆 trade 事件
            if state in [SjOrderState.StockDeal, SjOrderState.FuturesDeal]:
                deals_list = message.get("status", {}).get("deals", [])
                # 部分情況下單筆就放在 message 裡
                if not deals_list and "price" in message and "quantity" in message:
                    deals_list = [message]
                for deal in deals_list:
                    deal_id = deal.get("seq") or deal.get("exchange_seq")
                    price   = deal.get("price")
                    vol     = deal.get("quantity")
                    ts      = deal.get("ts")
                    if deal_id and price is not None and vol is not None and ts:
                        trade = TradeData(
                            gateway_name=self.gateway_name,
                            symbol=order.symbol,
                            exchange=order.exchange,
                            orderid=order.vt_orderid,
                            tradeid=f"{self.gateway_name}_{deal_id}",
                            direction=order.direction,
                            offset=order.offset,
                            price=float(price),
                            volume=float(vol),
                            datetime=datetime.fromtimestamp(ts/1e9, tz=TAIPEI_TZ),
                            localtime=datetime.now()
                        )
                        self.on_trade(trade)
                        self.write_log(f"[{thread_name}] 發送 trade 事件: {trade.tradeid} ({trade.volume}@{trade.price})")
        except Exception as e:
            self.write_log(f"[{thread_name}] _on_order_deal_shioaji 未捕獲錯誤: {e}\n{traceback.format_exc()}")

    def query_account(self, event: Event = None) -> None:
        """查詢帳戶資金"""
        thread_name = threading.current_thread().name

        if not self._check_connection(): # _check_connection 內部已有詳細日誌
            self.write_log(f"[{thread_name}] <=== query_account: 因連接檢查失敗退出 ===")
            return
        if not self.api:
            self.write_log(f"[{thread_name}] 錯誤：query_account: API 未初始化")
            self.write_log(f"[{thread_name}] <=== query_account: 因 API 未初始化退出 ===")
            return

        accounts: List[Union[SjAccount, SjStockAccount, SjFutureAccount]] = [] # 初始化
        try:
            #self.write_log(f"[{thread_name}] query_account: 調用 self.api.list_accounts()...")
            accounts = self.api.list_accounts() or [] # 確保即使返回 None 也是空列表

            # ===> 增強偵錯: 記錄 list_accounts 結果 <===
            #self.write_log(f"[{thread_name}] query_account: list_accounts 返回 {len(accounts)} 個帳戶。")
            if not accounts:
                self.write_log(f"[{thread_name}] <=== query_account: list_accounts 未返回帳戶信息，退出 ===")
                return # Exit if no accounts returned

        except Exception as e_list:
            self.write_log(f"[{thread_name}] 錯誤：query_account: 調用 list_accounts 失敗: {e_list}\n{traceback.format_exc()}")
            self.write_log(f"[{thread_name}] <=== query_account: 因 list_accounts 異常退出 ===")
            return

        #self.write_log(f"[{thread_name}] query_account: 開始遍歷 {len(accounts)} 個帳戶查詢資金...")
        processed_count = 0 # 計數器，用於記錄成功處理的帳戶數
        for acc in accounts:
            account_data: Optional[AccountData] = None # 為每個帳戶重置
            vt_accountid = "UNKNOWN" # 預設 ID

            # --- 在 try 塊外部安全獲取 ID 和類型用於日誌 ---
            acc_id_safe = getattr(acc, 'account_id', 'N/A')
            acc_type_safe = getattr(acc, 'account_type', 'N/A')
            if acc_id_safe == 'N/A' or acc_type_safe == 'N/A':
                    self.write_log(f"[{thread_name}] 警告：帳戶列表中的項目缺少 account_id 或 account_type: {repr(acc)}，跳過此項。")
                    continue # 跳過格式錯誤的帳戶對象

            vt_accountid = f"{self.gateway_name}_{acc_id_safe}_{acc_type_safe.value}"
            #self.write_log(f"[{thread_name}] query_account: --- Processing account {vt_accountid} ---")
            # --- 結束安全獲取 ---

            try:
                # --- 處理股票帳戶 ---
                if acc.account_type == SjAccountType.Stock:
                    self.write_log(f"  帳戶 {vt_accountid} 是股票帳戶，調用 account_balance()...")
                    balance: Optional[SjAccountBalance] = None # 調用前初始化
                    try:
                        # 假設 account_balance 不需要參數，使用預設帳戶
                        balance = self.api.account_balance()
                        # ===> 增強偵錯: 記錄 API 返回值 <===
                        self.write_log(f"  account_balance() 返回: {repr(balance)}")
                    except Exception as e_bal:
                        # 捕獲 API 調用本身的異常
                        self.write_log(f"  錯誤：調用 account_balance() 時發生異常: {e_bal}\n{traceback.format_exc()}")
                        # 這裡可以 continue 跳過這個帳戶，或者讓後面的 if balance 處理

                    # 檢查返回值是否有效
                    if balance:
                        self.write_log("  成功獲取股票餘額，準備創建 AccountData...")
                        account_data = AccountData(
                            accountid=vt_accountid,
                            balance=float(balance.acc_balance),
                            frozen=0.0,
                            gateway_name=self.gateway_name,
                        )
                    else:
                        self.write_log(f"  警告：未能獲取帳戶 {vt_accountid} 的股票餘額 (API 返回 None 或異常)。")

                # --- 處理期貨/選擇權帳戶 ---
                elif acc.account_type == SjAccountType.Future:
                    self.write_log(f"  帳戶 {vt_accountid} 是期貨帳戶，調用 margin(account=acc)...")
                    margin: Optional[SjMargin] = None # 調用前初始化
                    try:
                        # 假設 margin 需要 account=acc 參數
                        margin = self.api.margin(account=acc)
                        # ===> 增強偵錯: 記錄 API 返回值 <===
                        self.write_log(f"  margin(account=...) 返回: {repr(margin)}")
                    except Exception as e_mar:
                        # 捕獲 API 調用本身的異常
                        self.write_log(f"  錯誤：調用 margin(account=...) 時發生異常: {e_mar}\n{traceback.format_exc()}")
                        # 這裡可以 continue 跳過這個帳戶，或者讓後面的 if margin 處理

                    # 檢查返回值是否有效
                    if margin:
                        self.write_log("  成功獲取期貨保證金，準備創建 AccountData...")
                        frozen_approx = float(margin.initial_margin + margin.order_margin_premium)
                        account_data = AccountData(
                            accountid=vt_accountid,
                            balance=float(margin.equity_amount),
                            frozen=frozen_approx,
                            gateway_name=self.gateway_name,
                        )
                    else:
                        self.write_log(f"  警告：未能獲取帳戶 {vt_accountid} 的期貨保證金 (API 返回 None 或異常)。")

                # --- 推送更新 ---
                # ===> 增強偵錯: 記錄是否創建了 AccountData <===
                if account_data:
                    #self.write_log(f"  準備推送帳戶 {vt_accountid} 的更新 (AccountData: {account_data})...")
                    # 更新內部緩存 (線程安全)
                    with self.account_lock:
                        self.accounts[vt_accountid] = account_data
                    # 推送到 VnPy 事件引擎
                    self.on_account(account_data) # on_account 會觸發 acc_output.csv 記錄
                    #self.write_log(f"  帳戶 {vt_accountid} 更新推送完成。")
                    processed_count += 1 # 增加成功計數

            # --- 捕獲處理單個帳戶時可能發生的其他錯誤 ---
            except AttributeError as e_attr:
                # 捕捉訪問 acc.account_id 或 acc.account_type 等屬性時的錯誤
                self.write_log(f"[{thread_name}] 錯誤：處理帳戶列表項目時缺少屬性: {e_attr} - Item: {repr(acc)}\n{traceback.format_exc()}")
            except Exception as e_acc_loop:
                # 捕捉處理單個帳戶時的其他意外錯誤
                self.write_log(f"[{thread_name}] 錯誤：處理帳戶 {vt_accountid} 資金時發生意外錯誤: {e_acc_loop}\n{traceback.format_exc()}")

    

    def query_position(self, event: Event = None) -> None:
        """查詢持倉"""
        thread_name = threading.current_thread().name
        #self.write_log(f"[{thread_name}] ===> query_position: 開始執行{event_info} ===")

        if not self._check_connection():
            self.write_log(f"[{thread_name}] <=== query_position: 因連接檢查失敗退出 ===")
            return
        if not self.api:
            self.write_log(f"[{thread_name}] 錯誤：query_position: API 未初始化")
            self.write_log(f"[{thread_name}] <=== query_position: 因 API 未初始化退出 ===")
            return

        #self.write_log(f"[{thread_name}] query_position: 開始查詢持倉信息...")

        with self.position_lock:
            previous_position_keys: set = set(self.positions.keys())
        received_position_keys: set = set()

        try:
            accounts = self.api.list_accounts()
            if not accounts:
                self.write_log(f"[{thread_name}] query_position: 未獲取到任何帳戶信息，無法查詢持倉。")
                self.write_log(f"[{thread_name}] <=== query_position: 無帳戶信息，退出 ===")
                return
        except Exception as e_list_acc:
            self.write_log(f"[{thread_name}] 錯誤：query_position: 調用 list_accounts 失敗: {e_list_acc}\n{traceback.format_exc()}")
            self.write_log(f"[{thread_name}] <=== query_position: 因 list_accounts 異常退出 ===")
            return

        #self.write_log(f"[{thread_name}] query_position: 準備遍歷 {len(accounts)} 個帳戶查詢持倉...")
        for acc in accounts:
            try:
                positions_list: Optional[List[Union[SjStockPosition, SjFuturePosition]]] = \
                    self.api.list_positions(account=acc, unit=SjUnit.Share)

                if positions_list is None:
                    #self.write_log(f"[{thread_name}] query_position: 帳戶 {vt_accountid} 未返回持倉列表。")
                    continue # 處理下一個帳戶

                #self.write_log(f"[{thread_name}] query_position: 帳戶 {vt_accountid} 返回 {len(positions_list)} 筆持倉，開始處理...")
                # 5. 處理該帳戶下的每個持倉
                for pos in positions_list:
                    try:
                        code = pos.code
                        #self.write_log(f"  處理持倉回報: code={code}, direction={pos.direction}, quantity={pos.quantity}, price={pos.price}") # 精簡日誌

                        # ====> 修改核心邏輯在這裡 <====
                        sj_contract = self.find_sj_contract(code) # 查找合約

                        if not sj_contract:
                            # 如果找不到合約信息 (很可能是已過期)
                            #self.write_log(f"  警告：持倉代碼 {code} 找不到對應的合約詳細信息 (可能已過期)，將跳過此持倉記錄。")
                            continue # 跳過這個持倉，處理下一個
                        # ====> 修改結束 <====

                        # --- 如果找到了合約，繼續正常處理 ---
                        vn_exchange_str = self.sj2vnpy[sj_contract.exchange]
                        if not vn_exchange_str:
                            #self.write_log(f"  警告：持倉 {code}: 無法映射交易所 Shioaji Exchange '{sj_contract.exchange}'，跳過此持倉。")
                            continue

                        vn_exchange = Exchange(vn_exchange_str)
                        vt_symbol = f"{code}.{vn_exchange.value}" # vt_symbol 在這裡定義

                        vn_direction = DIRECTION_MAP_REVERSE.get(pos.direction)
                        if not vn_direction:
                            #self.write_log(f"  警告：持倉 {vt_symbol} 方向未知 ({pos.direction})，跳過")
                            continue

                        # 從緩存獲取更詳細的合約信息 (size, pricetick)
                        cached_contract = self.contracts.get(vt_symbol)
                        if not cached_contract:
                            #self.write_log(f"  警告：持倉 {vt_symbol} 找不到對應的合約詳細信息，跳過")
                            continue
                        volume = float(pos.quantity)
                        yd_volume = 0.0
                        if isinstance(pos, SjStockPosition) and hasattr(pos, 'yd_quantity'):
                            yd_volume = float(pos.yd_quantity)
                        frozen = max(0.0, volume - yd_volume)

                        # 創建 VnPy PositionData
                        position = PositionData(
                            gateway_name=self.gateway_name,
                            symbol=code,
                            exchange=vn_exchange,
                            direction=vn_direction,
                            volume=volume,
                            yd_volume=yd_volume,
                            frozen=frozen,
                            price=float(pos.price),
                            pnl=float(pos.pnl),
                            #accountid=vt_accountid,
                            # 可選擴展信息
                            # vt_symbol=vt_symbol
                        )

                        # 記錄收到的持倉鍵
                        position_key = (position.vt_symbol, position.direction)
                        received_position_keys.add(position_key)

                        # 更新內部緩存並推送 (加鎖)
                        with self.position_lock:
                            self.positions[position_key] = position

                        # ===> 增強偵錯：記錄準備推送的 PositionData <===
                        #self.write_log(f"  準備推送 PositionData: {repr(position)}")
                        # ===> 結束增強偵錯 <===

                        #self.write_log(f"  推送持倉更新: {position.vt_symbol}, Dir: {position.direction.value}, Vol: {position.volume}, Price: {position.price}")
                        #self.write_log(repr(position))
                        self.on_position(position) # 推送 PositionData
                    except Exception as e_pos_item:
                        self.write_log(f"錯誤：處理單個持倉 ({getattr(pos, 'code', 'N/A')}) 時出錯: {e_pos_item}\n{traceback.format_exc()}")

            except Exception as e_acc_pos:
                self.write_log(f"[{thread_name}] 錯誤：查詢帳戶 {acc.account_id} ({acc.account_type.value}) 持倉列表失敗: {e_acc_pos}\n{traceback.format_exc()}")


        # 6. 清理 Gateway 內部緩存中已不存在的持倉
        #self.write_log(f"[{thread_name}] query_position: 準備清理過時持倉...")
        keys_to_clear = previous_position_keys - received_position_keys
        if keys_to_clear:
            #self.write_log(f"[{thread_name}] query_position: 發現 {len(keys_to_clear)} 個需要清理的持倉鍵: {keys_to_clear}")
            with self.position_lock: # --- Lock Start ---
                for key in keys_to_clear:
                    vt_symbol, direction = key
                    #self.write_log(f"  正在清理持倉: {vt_symbol} ({direction.value})")
                    # 從緩存中移除
                    self.positions.pop(key, None)

                    # 創建一個零數量的 PositionData 推送給 VnPy
                    try:
                        # 嘗試解析 symbol 和 exchange
                        symbol, exchange_str = extract_vt_symbol(vt_symbol)
                        vn_exchange = Exchange(exchange_str)
                        # 獲取帳戶 ID - 應從被刪除的 position 對象獲取，但它已被 pop
                        # 如果需要 accountid，需要在 pop 之前獲取或存儲映射關係
                        # 簡化：accountid 留空或使用通用標識
                        zero_position = PositionData(
                            gateway_name=self.gateway_name,
                            symbol=symbol, # 使用解析出的 symbol
                            exchange=vn_exchange,
                            direction=direction,
                            volume=0,
                            yd_volume=0,
                            frozen=0,
                            price=0.0,
                            pnl=0.0,
                            #accountid="", # 暫時留空
                            # vt_symbol=vt_symbol
                        )
                        # ===> 增強偵錯：記錄準備推送的 Zero PositionData <===
                        #self.write_log(f"  準備推送 Zero PositionData: {repr(zero_position)}")
                        # ===> 結束增強偵錯 <===

                        self.on_position(zero_position) # 推送零持倉
                        #self.write_log(f"  推送零持倉完成: {vt_symbol} ({direction.value})")
                    except ValueError as e_extract:
                        self.write_log(f"  警告：清理持倉時無法解析 vt_symbol: {vt_symbol} - {e_extract}")
                    except Exception as e_zero:
                        self.write_log(f"  錯誤：清理持倉 {vt_symbol} ({direction.value}) 並推送零持倉時出錯: {e_zero}\n{traceback.format_exc()}")
            # --- Lock End ---


    def query_history(self, req: HistoryRequest) -> Optional[List[BarData]]:
        """
        查詢歷史 K 線數據。
        注意：根據用戶反饋，Shioaji api.kbars 始終返回 1 分鐘 K 線。
        此方法將直接返回分鐘線，並嘗試從分鐘線合成日線。
        其他週期暫不支持。
        """
        # 1. 檢查連接狀態
        if not self._check_connection():
            self.write_log("查詢歷史 K 線失敗：接口未連接")
            return None
        if not self.api:
            self.write_log("查詢歷史 K 線失敗：API 未初始化")
            return None

        # 2. 提取請求參數
        symbol = req.symbol
        vn_exchange_str = req.exchange.value
        interval = req.interval
        start_dt = req.start # datetime object with timezone
        end_dt = req.end   # datetime object with timezone
        vt_symbol = f"{symbol}.{vn_exchange_str}"

        # 3. 查找 Shioaji 合約對象
        sj_contract = self.find_sj_contract(symbol, vn_exchange_str)
        if not sj_contract:
            self.write_log(f"錯誤：查詢歷史 K 線失敗，找不到合約 {vt_symbol}")
            return None

        history: List[BarData] = []

        # --- 檢查支持的週期 (僅日線和分鐘線) ---
        if interval not in [Interval.DAILY, Interval.MINUTE]:
            self.write_log(f"錯誤：此 Gateway 的 query_history 僅支持查詢日線或分鐘線，不支持 {interval.value}")
            return [] # 返回空列表

        # --- 無論請求日線還是分鐘線，都先獲取分鐘線數據 ---
        # 格式化日期 YYYY-MM-DD
        start_date_str = start_dt.strftime('%Y-%m-%d')
        end_date_str = end_dt.strftime('%Y-%m-%d')

        try:
            self.write_log(f"開始查詢 1 分鐘 K 線 (用於 {interval.value} 請求): {vt_symbol} from {start_date_str} to {end_date_str}")
            # 調用 api.kbars 獲取分鐘線數據
            kbars_data: Optional[SjKbars] = self.api.kbars(
                contract=sj_contract,
                start=start_date_str,
                end=end_date_str
            )

            if not kbars_data or not kbars_data.ts:
                self.write_log(f"Shioaji API 未返回任何 K 線數據: {vt_symbol}")
                return history # 返回空列表

            self.write_log(f"API 返回 {len(kbars_data.ts)} 筆 1 分鐘 K 線原始數據")

            # --- 處理返回的 1 分鐘 K 線數據 ---
            minute_bars: List[BarData] = [] # 臨時存儲轉換後的 1 分鐘 BarData
            for i in range(len(kbars_data.ts)):
                try:
                    ts_value = kbars_data.ts[i]
                    dt_naive = datetime.fromtimestamp(ts_value / 1e9)
                    bar_dt = dt_naive.replace(tzinfo=TAIPEI_TZ)

                    # 過濾掉請求範圍之外的數據
                    if bar_dt < start_dt or bar_dt >= end_dt:
                        continue

                    turnover = float(kbars_data.Amount[i]) if hasattr(kbars_data, 'Amount') and kbars_data.Amount and i < len(kbars_data.Amount) else 0.0

                    bar = BarData(
                        gateway_name=self.gateway_name, symbol=symbol, exchange=req.exchange,
                        datetime=bar_dt, interval=Interval.MINUTE, # 注意：這裡創建的是分鐘 Bar
                        volume=float(kbars_data.Volume[i]), turnover=turnover,
                        open_price=float(kbars_data.Open[i]), high_price=float(kbars_data.High[i]),
                        low_price=float(kbars_data.Low[i]), close_price=float(kbars_data.Close[i]),
                    )
                    minute_bars.append(bar)
                except Exception as e_conv:
                    self.write_log(f"警告：轉換第 {i} 筆 K 線數據時出錯: {e_conv}")
                    continue

            # --- 根據請求的 interval 決定如何處理 minute_bars ---
            if interval == Interval.MINUTE:
                # 如果請求的就是分鐘線，直接賦值
                history = minute_bars
                self.write_log(f"成功獲取並轉換 {len(history)} 筆分鐘 K 線數據")
            elif interval == Interval.DAILY:
                # 如果請求的是日線，需要使用 BarGenerator 從分鐘線合成
                self.write_log("開始從分鐘 K 線合成日 K 線...")
                # 創建日線合成器
                daily_bg = BarGenerator(None, window=1, on_bar=lambda bar: history.append(bar), interval=Interval.DAILY)
                for minute_bar in minute_bars:
                    daily_bg.update_bar(minute_bar) # 將分鐘線餵給日線合成器
                # daily_bg.generate() # 確保最後一根日線生成 (如果需要)
                self.write_log(f"成功從 {len(minute_bars)} 筆分鐘線合成 {len(history)} 筆日 K 線數據")

        except Exception as e:
            self.write_log(f"錯誤：查詢或處理歷史 K 線失敗 ({vt_symbol}, Interval: {interval.value}): {e}\n{traceback.format_exc()}")
            return None # 查詢失敗返回 None

        # 5. 返回結果
        return history

    def _contracts_cb(self, security_type: SjSecurityType):
        """合約下載進度回調 (可選)"""
        self.write_log(f"合約下載進度: {security_type.value} 完成")
        # 可以在所有類型下載完成後觸發處理，但更可靠的是在 _connect_worker 中檢查狀態後調用
        # if security_type == SjSecurityType.Option: # 假設 Option 是最後一個
        #self._process_contracts()

    # ----------------------------------------------------------------------
    # 合約處理：一次掃描、分流建檔，再批次補齊週期權標的
    # ----------------------------------------------------------------------
    def _process_contracts(self) -> None:
        """
        全面重寫版本。

        1. **期權**：集中呼叫 `_parse_option()`，並採 *naive datetime* 保存 `option_expiry` ➜ 避免與外部
           OptionMaster (naive) 計算衝突。
        2. **期貨 / 股票 / 指數**：以 Shioaji 契約原生欄位 `multiplier`、`tick_size` 為基礎，再依
           TAIFEX 官規覆寫台指大小台乘數與跳動。
        3. **週期選擇權標的**：先抓 `MXFR1`；若不存在則找最早未到期 `MXFyyyyMM`；最後批次補齊
           `option_underlying` 為找到的期貨代號。
        """
        self.write_log("===> _process_contracts (FULL) 開始 …")

        # 0️⃣ 前置檢查 --------------------------------------------------
        if not self.api:
            self.write_log("_process_contracts: self.api is None，退出")
            return
        if self.api.Contracts.status != SjFetchStatus.Fetched:
            self.write_log(f"_process_contracts: Contracts.status = {self.api.Contracts.status} (非 Fetched)，退出")
            return

        # 1️⃣ 內部工具 --------------------------------------------------
        def _find_mxf_contract() -> Tuple[Optional[str], Optional[date]]:
            """優先回傳 MXFR1；否則回傳最近未到期 MXFyyyyMM"""
            mxf_cat = self.api.Contracts.Futures.MXF
            cont = getattr(mxf_cat, "MXFR1", None)
            if cont:
                return cont.symbol, datetime.strptime(cont.delivery_date, "%Y/%m/%d").date()

            today = date.today()
            best: Optional[Tuple[str, date]] = None
            for fut in mxf_cat:
                if fut.symbol.startswith("MXF"):
                    exp = datetime.strptime(fut.delivery_date, "%Y/%m/%d").date()
                    if exp >= today and (best is None or exp < best[1]):
                        best = (fut.symbol, exp)
            return best if best else (None, None)

        def _size_pricetick_generic(sjc) -> Tuple[float, float]:
            """安全讀取 multiplier / tick_size；若缺失則給預設"""
            try:
                size = float(getattr(sjc, "multiplier", 1) or 1)
            except Exception:
                size = 1.0
            try:
                tick = float(getattr(sjc, "tick_size", 0.01) or 0.01)
            except Exception:
                tick = 0.01
            return size, tick

        def _parse_option(sjc: sj.contracts.Option) -> Optional[ContractData]:
            try:
                cd = ContractData(
                    gateway_name=self.gateway_name,
                    symbol=sjc.code,
                    exchange=Exchange.TAIFEX,
                    name=sjc.name,
                    product=Product.OPTION,
                    size=50.0,            # 依 TAIFEX 臺指選擇權乘數
                    pricetick=0.1,
                    min_volume=1,
                    net_position=True,
                    history_data=True,
                )

                # --- 欄位對應 ------------------------------------
                cd.option_strike = float(sjc.strike_price)
                cd.option_type   = OptionType.CALL if sjc.option_right.value == "C" else OptionType.PUT
                expiry_dt        = datetime.strptime(sjc.delivery_date, "%Y/%m/%d")  # *naive*
                cd.option_expiry = expiry_dt

                cat = sjc.category or "TXO"
                if is_third_wednesday(expiry_dt.date()):  # 月選
                    ym = expiry_dt.strftime("%Y%m")
                    cd.option_portfolio = f"{cat}{ym}"
                    cd.option_index     = str(int(cd.option_strike))
                    cd.option_underlying = f"TXF{ym}" if cat == "TXO" else ""
                else:                                    # 週選
                    ymd = expiry_dt.strftime("%Y%m%d")
                    cd.option_portfolio = f"TX5{ymd}" if cat.startswith("TX") else f"{cat}W{ymd}"
                    cd.option_index     = cd.option_portfolio
                    cd.option_underlying = ""  # 待批次補齊
                return cd
            except Exception as e:
                self.write_log(f"_parse_option() 失敗: {e}")
                return None

        # 2️⃣ 建立臨時 dict -------------------------------------------
        tmp: Dict[str, ContractData] = {}

        # 2‑A 期權 -------------------------------------------------
        for code, sjc in self.api.Contracts.Options._code2contract.items():
            cd = _parse_option(sjc)
            if cd:
                tmp[cd.vt_symbol] = cd

        # 2‑B 期貨 -------------------------------------------------
        fut_map = getattr(self.api.Contracts, "Futures", None)
        if fut_map and hasattr(fut_map, "_code2contract"):
            for code, sjc in fut_map._code2contract.items():
                size, tick = _size_pricetick_generic(sjc)
                # 覆寫台指商品官規
                if code.startswith("TXF"):
                    size, tick = 200.0, 1.0
                elif code.startswith("MXF"):
                    size, tick = 50.0, 1.0
                elif code.startswith("TMF"):
                    size, tick = 10.0, 1.0

                cd = ContractData(
                    gateway_name=self.gateway_name,
                    symbol=code,
                    exchange=Exchange.TAIFEX,
                    name=sjc.name,
                    product=Product.FUTURES,
                    size=size,
                    pricetick=tick,
                    min_volume=1,
                    net_position=True,
                    history_data=True,
                )
                tmp[cd.vt_symbol] = cd

        # 2‑C 現股 / ETF -------------------------------------------
        for ex_name, ex_enum in (("TSE", Exchange.TWSE), ("OTC", Exchange.TOTC)):
            ex_cat = getattr(self.api.Contracts.Stocks, ex_name, None)
            if not ex_cat or not hasattr(ex_cat, "_code2contract"):
                continue
            for code, sjc in ex_cat._code2contract.items():
                size, tick = _size_pricetick_generic(sjc)
                cd = ContractData(
                    gateway_name=self.gateway_name,
                    symbol=code,
                    exchange=ex_enum,
                    name=sjc.name,
                    product=Product.EQUITY,
                    size=size,
                    pricetick=tick,
                    min_volume=1,
                    net_position=False,
                    history_data=True,
                )
                tmp[cd.vt_symbol] = cd

        # 3️⃣ 補週選標的 -------------------------------------------
        mxf_sym, _ = _find_mxf_contract()
        if mxf_sym:
            patched = 0
            for cd in tmp.values():
                if cd.product == Product.OPTION and not cd.option_underlying and cd.option_portfolio.startswith("TX5"):
                    cd.option_underlying = mxf_sym
                    patched += 1
            self.write_log(f"週選標的補齊 {patched} 檔 ➜ {mxf_sym}")
        else:
            self.write_log("⚠️  找不到 MXF 連續/近月，週選標的保持空白")

        # 4️⃣ 推送 ---------------------------------------------------
        with self.contract_lock:
            self.contracts.clear()
            for cd in tmp.values():
                self.contracts[cd.vt_symbol] = cd
                self.on_contract(cd)

        self.write_log(f"_process_contracts: 完成，推送 {len(tmp)} 檔合約")
