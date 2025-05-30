# shioaji_session_handler.py

# --- Python 標準庫導入 ---
import copy
import traceback
from datetime import datetime, date, timedelta # 確保導入 date 和 time
from threading import Lock, Thread # Lock and Thread are used by the class, not necessarily here
from typing import Any, Dict, List, Optional, Set, Tuple, Union, Callable
from zoneinfo import ZoneInfo 
import calendar
import time
import threading
import asyncio
import janus # Janus is used by the class for async queue handling
# import threading # Already imported via `from threading import Lock, Thread`
# import asyncio # Used by the class
# import janus # Used by the class

# --- VnPy 相關導入 ---
# from vnpy.event import EventEngine,Event # EventEngine not directly used by handler, Event used by class methods

from vnpy.trader.event import EVENT_CONTRACT
from vnpy.trader.gateway import BaseGateway # Imported for class inheritance
from vnpy.trader.object import (
    TickData, OrderData, TradeData, PositionData, AccountData, ContractData, 
    OrderRequest, CancelRequest, SubscribeRequest, HistoryRequest, BarData, LogData
)
from vnpy.trader.constant import (
    Exchange, Product, Direction, OrderType, Offset, Status, Interval, OptionType
)
from vnpy.trader.utility import BarGenerator # Used in query_history

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
    Unit as SjUnit, # Used in query_position
    OptionRight as SjOptionRight,
)
from shioaji.account import Account as SjAccount, AccountType as SjAccountType, StockAccount as SjStockAccount, FutureAccount as SjFutureAccount
from shioaji.contracts import Contract as SjContract, FetchStatus as SjFetchStatus ,Option as sjOption, Stock as SjStock, Future as SjFuture
from shioaji.order import Trade as SjTrade # Used in type hints and caches
from shioaji.position import StockPosition as SjStockPosition, FuturePosition as SjFuturePosition, Margin as SjMargin, AccountBalance as SjAccountBalance
from shioaji.stream_data_type import TickSTKv1, TickFOPv1, BidAskSTKv1, BidAskFOPv1 
from shioaji.data import Kbars as SjKbars 
from shioaji.error import TokenError as SjTokenError, AccountNotSignError as SjAccountNotSignError

# --- 時區設定 ---
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# --- 通用 VnPy 與 Shioaji 參數映射表 ---
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

# --- 股票訂單參數映射 (根據您的建議新增/修改) ---
ORDER_TYPE_STOCK_VT2SJ: Dict[OrderType, Tuple[SjStockPriceType, SjOrderType]] = {
    OrderType.LIMIT:  (SjStockPriceType.LMT, SjOrderType.ROD),
    OrderType.MARKET: (SjStockPriceType.MKT, SjOrderType.IOC),  # 股票市價單強制 IOC
    OrderType.FAK:    (SjStockPriceType.LMT, SjOrderType.IOC),
    OrderType.FOK:    (SjStockPriceType.LMT, SjOrderType.FOK),
}

ORDER_COND_MAP: Dict[Tuple[Offset, Direction], SjStockOrderCond] = {
    (Offset.OPEN, Direction.LONG):       SjStockOrderCond.Cash,
    (Offset.OPEN, Direction.SHORT):      SjStockOrderCond.ShortSelling,
    (Offset.CLOSETODAY, Direction.LONG): SjStockOrderCond.ShortSelling, # 當沖買回 (平今日融券)
    (Offset.CLOSETODAY, Direction.SHORT):SjStockOrderCond.Cash,         # 當沖賣出 (出現股)
    (Offset.CLOSE, Direction.LONG):      SjStockOrderCond.ShortSelling, # 平倉買回 (平昨日融券)
    (Offset.CLOSE, Direction.SHORT):     SjStockOrderCond.Cash,         # 平倉賣出 (出現股或平昨日融資)
    (Offset.CLOSEYESTERDAY, Direction.LONG): SjStockOrderCond.ShortSelling,
    (Offset.CLOSEYESTERDAY, Direction.SHORT):SjStockOrderCond.Cash,
}

STOCK_LOT_MAP: Dict[str, SjStockOrderLot] = {
    "Common":       SjStockOrderLot.Common,
    "Odd":          SjStockOrderLot.Odd,
    "IntradayOdd":  SjStockOrderLot.IntradayOdd,
    "Fixing":       SjStockOrderLot.Fixing,
}

# --- 期貨/選擇權訂單參數映射 (這些已在您的原程式碼中) ---
ORDER_TYPE_FUTURES_VT2SJ: Dict[OrderType, Tuple[SjFuturesPriceType, SjOrderType]] = {
    OrderType.LIMIT: (SjFuturesPriceType.LMT, SjOrderType.ROD),
    OrderType.MARKET: (SjFuturesPriceType.MKP, SjOrderType.IOC), # 期貨市價單用 MKP + IOC
    OrderType.FAK:    (SjFuturesPriceType.LMT, SjOrderType.IOC),
    OrderType.FOK:    (SjFuturesPriceType.LMT, SjOrderType.FOK),
}

FUTURES_OFFSET_MAP: Dict[Offset, SjFuturesOCType] = {
    Offset.OPEN: SjFuturesOCType.New,
    Offset.CLOSE: SjFuturesOCType.Cover,
    Offset.CLOSETODAY: SjFuturesOCType.DayTrade, # 當沖 (平今倉)
    Offset.CLOSEYESTERDAY: SjFuturesOCType.Cover, # 平倉 (昨倉)
    # Offset.NONE: SjFuturesOCType.Auto # 可選：如果 NONE 時希望自動判斷
}
# FUTURES_OFFSET_MAP_REVERSE (如果Handler內部需要反向查詢)
# STOCK_ORDER_COND_MAP_REVERSE (如果Handler內部需要反向查詢)
# OPTION_TYPE_MAP, OPTION_TYPE_MAP_REVERSE (如果Handler內部需要)

# --- 自訂事件字串 (供 Handler 和 Manager 之間識別事件類型) ---
EVENT_SUBSCRIBE_SUCCESS = "eSubscribeSuccess"
EVENT_SUBSCRIBE_FAILED  = "eSubscribeFailed"
EVENT_RECONNECT_FAILED = "eReconnectFailed"
EVENT_CONTRACTS_LOADED = "eContractsLoaded"

# --- 模組級別輔助函數 (根據您的建議和原有程式碼) ---

def _helper_map_stock_order_cond(
    offset: Offset, 
    direction: Direction, 
    kwargs: Dict[str, Any], 
    write_log_func: Callable[[str, str], None] # 假設 write_log_func 接收 (msg, level)
) -> SjStockOrderCond:
    """根據 Offset, Direction 和 kwargs 中的 "order_cond" 決定 SjStockOrderCond。"""
    
    kwarg_order_cond_str = kwargs.get("order_cond") # User might pass "MarginTrading" or "ShortSelling"
    
    # 優先處理 kwargs 中明確指定的融資/融券意圖
    if kwarg_order_cond_str:
        if kwarg_order_cond_str == SjStockOrderCond.MarginTrading.value:
            if direction == Direction.LONG and offset == Offset.OPEN: # 融資買入開倉
                return SjStockOrderCond.MarginTrading
            elif direction == Direction.SHORT and offset in [Offset.CLOSE, Offset.CLOSEYESTERDAY, Offset.CLOSETODAY]: # 融資賣出平倉
                return SjStockOrderCond.MarginTrading
            else:
                write_log_func(f"輔助函數：kwargs中order_cond='MarginTrading'與操作({offset.value},{direction.value})不符，將使用預設映射。", "warning")
        elif kwarg_order_cond_str == SjStockOrderCond.ShortSelling.value:
            if direction == Direction.SHORT and offset == Offset.OPEN: # 融券賣出開倉
                return SjStockOrderCond.ShortSelling
            elif direction == Direction.LONG and offset in [Offset.CLOSE, Offset.CLOSEYESTERDAY, Offset.CLOSETODAY]: # 融券買入平倉
                return SjStockOrderCond.ShortSelling
            else:
                write_log_func(f"輔助函數：kwargs中order_cond='ShortSelling'與操作({offset.value},{direction.value})不符，將使用預設映射。", "warning")
        elif kwarg_order_cond_str == SjStockOrderCond.Cash.value: # 明確指定現股
             return SjStockOrderCond.Cash
        else:
            write_log_func(f"輔助函數：kwargs中提供了未知的order_cond字串'{kwarg_order_cond_str}'，將使用預設映射。", "warning")

    # 如果kwargs中沒有有效指定，則使用 ORDER_COND_MAP
    default_cond = ORDER_COND_MAP.get((offset, direction), SjStockOrderCond.Cash)
    # write_log_func(f"輔助函數：依據offset/direction ({offset.value}/{direction.value}) 映射至 {default_cond.value}", "debug")
    return default_cond

def _helper_map_stock_order_lot(
    input_lot_str: Optional[str], 
    write_log_func: Callable[[str, str], None]
) -> SjStockOrderLot:
    """將來自 kwargs 的字串转换为 SjStockOrderLot 枚舉。"""
    if input_lot_str and isinstance(input_lot_str, str):
        sj_lot = STOCK_LOT_MAP.get(input_lot_str) # STOCK_LOT_MAP 的 key 是字串
        if sj_lot:
            return sj_lot
        else:
            write_log_func(f"輔助函數：無效的 order_lot 字串 '{input_lot_str}'，使用預設 Common。", "warning")
    elif isinstance(input_lot_str, SjStockOrderLot): # 如果直接傳入枚舉成員
        return input_lot_str
    
    return SjStockOrderLot.Common # 預設為整股

# --- 日期相關輔助函數 (來自您的原程式碼) ---
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

# _parse_date 是類別方法，因为它使用了 self.write_log, 所以它應定義在 ShioajiSessionHandler 類內部
# calculate_listing_date 也是如此，如果它需要調用 self._parse_date 或 self.write_log
# 如果它們是純函數，不依賴 self，則可以放在這裡。
# 從您提供的程式碼片段來看，_parse_date 確實是類方法，calculate_listing_date 則調用了其他模組級函數。

# calculate_listing_date 可以保持為模組級別，因为它不使用 self
def calculate_listing_date(
    delivery_year: int,
    delivery_month: int,
    is_weekly: bool = False,
    weekly_expiry: Optional[date] = None # Made weekly_expiry Optional for clarity
) -> Optional[date]: # Return Optional[date] if weekly_expiry is None for weekly
    if not is_weekly:
        ym = (delivery_year, delivery_month - 1) if delivery_month > 1 else (delivery_year - 1, 12)
        last_trade = third_wednesday(ym[0], ym[1])
        return next_business_day(last_trade)
    else:
        if not weekly_expiry: # Weekly options must have an expiry date
            # Consider logging a warning here if a logging function is passed or available globally
            return None 
        listing = weekly_expiry - timedelta(days=14)
        while listing.weekday() != calendar.WEDNESDAY:
            listing += timedelta(days=1)
        if listing.day <= 7:
            listing += timedelta(weeks=1)
        return listing
# --- ShioajiSessionHandler ---
class ShioajiSessionHandler(BaseGateway): 
    def __init__(
        self, 
        manager_event_callback: Callable, # Callback to send events to manager
        gateway_name: str,                # e.g., "SHIOAJI_MULTI.ACCOUNT1"
        vnpy_account_id: str              # e.g., "STOCK_ACCOUNT_01"
    ):
        super().__init__(None, gateway_name) # event_engine is None, manager handles events

        self.manager_event_callback = manager_event_callback
        self.vnpy_account_id = vnpy_account_id

        self.sj_exchange_map_vnpy_enum: Dict[SjExchange, Exchange] = {
            SjExchange.TSE: Exchange.TWSE,
            SjExchange.OTC: Exchange.TOTC,
            SjExchange.TAIFEX: Exchange.TAIFEX,
            SjExchange.OES: Exchange.TOES
        }
        self.sj2vnpy = self.sj_exchange_map_vnpy_enum
        self.vn2sj: Dict[str, SjExchange] = {
            vn_enum.value: sj_enum for sj_enum, vn_enum in self.sj2vnpy.items()
        }

        self.loop = asyncio.new_event_loop() # Each handler has its own asyncio loop
        self._async_thread: Optional[threading.Thread] = None # Thread to run self.loop
        
        # --- JanusQueue for non-conflated events (e.g., order/deal updates) ---
        # Or if you decide on a mixed strategy where some data uses janus, some direct conflation.
        self.janus_queue = janus.Queue(maxsize=8000) 
        self.batch_collect_timeout: float = 0.1 # Default for janus_queue consumer, configurable in connect()

        # --- API and Connection State ---
        self.api: Optional[sj.Shioaji] = None
        self.connected: bool = False
        self.logged_in: bool = False
        self.connect_thread: Optional[Thread] = None
        self.connection_start_time: float = 0

        # --- Reconnection Attributes ---
        self.reconnect_attempts: int = 0
        self.reconnect_limit: int = 3    # Default, overridden by session_setting
        self.reconnect_interval: int = 5 # Default, overridden by session_setting
        self._reconnect_timer: Optional[threading.Timer] = None # Corrected from just _reconnect_timer

        # --- Session Configuration (populated in connect) ---
        self.session_setting: dict = {} 
        self.api_key: str = ""
        self.secret_key: str = ""
        self.ca_path: str = ""
        self.ca_passwd: str = ""
        self.person_id_setting: str = "" 
        self.simulation: bool = False    
        self.force_download: bool = True 

        # --- VnPy Data Caches (Handler-specific) ---
        self.orders: Dict[str, OrderData] = {}
        self.shioaji_trades: Dict[str, SjTrade] = {}      # Maps Shioaji seqno to SjTrade object
        self.shioaji_deals: Set[Tuple[str, str]] = set()  # Tracks (deal_id, order_seqno) to avoid duplicates
        self.positions: Dict[Tuple[str, Direction], PositionData] = {} # Key: (vt_symbol, direction)
        self.contracts: Dict[str, ContractData] = {}     # Key: vt_symbol
        self.subscribed: Set[str] = set()                # Key: vt_symbol
        self.tick_cache: Dict[str, TickData] = {}        # Key: vt_symbol, stores merged TickData

        # --- Locks for Thread Safety ---
        self.order_map_lock = Lock()
        self.position_lock = Lock()
        self.contract_lock = Lock()
        self.subscribed_lock = Lock()
        self.tick_cache_lock = Lock() # For self.tick_cache (merged VnPy TickData)
        self._reconnect_lock = Lock()

        # --- Attributes for Tick/BidAsk Conflation (Option A) ---
        self.latest_raw_ticks: Dict[str, TickSTKv1] = {}          # Stores latest raw Shioaji TickSTKv1
        self.latest_raw_bidasks: Dict[str, BidAskSTKv1] = {}      # Stores latest raw Shioaji BidAskSTKv1
        self.latest_raw_fop_ticks: Dict[str, TickFOPv1] = {}      # For futures/options ticks
        self.latest_raw_fop_bidasks: Dict[str, BidAskFOPv1] = {}  # For futures/options bidasks
        
        self.raw_data_cache_lock = threading.Lock() # Protects all latest_raw_xxx dictionaries

        self.conflation_interval_sec: float = 0.050  # Default 50ms, read from session_setting in connect()
                                                     # Set to 0 to process as fast as possible after trigger
        
        self.pending_conflation_processing: Set[str] = set() # vt_symbols with new raw data
        self.pending_conflation_lock = threading.Lock()       # Protects pending_conflation_processing

        self.conflation_trigger = asyncio.Event() # asyncio.Event for the handler's loop
        self._conflation_task: Optional[asyncio.Task] = None # Task for _conflation_processor coroutine
        # --- End Conflation Attributes ---
        self.fetched_security_types: Set[SjSecurityType] = set()
        self.expected_security_types: Set[SjSecurityType] = {
            SjSecurityType.Index, 
            SjSecurityType.Stock, 
            SjSecurityType.Future, 
            SjSecurityType.Option
        }
        self._contracts_processed_flag: bool = False
        self._processing_contracts_lock = threading.Lock() # 保護 _contracts_processed_flag 和 _process_contracts 調用
        self._all_contracts_fetched_event = threading.Event() # 用於 _connect_worker 等待

        self.write_log(f"Handler for {self.vnpy_account_id} (gw: {self.gateway_name}) initialized.")

    def _start_async_loop_thread(self):
        if not self._async_thread or not self._async_thread.is_alive():
            self.write_log(f"Starting asyncio loop thread for {self.gateway_name}...")
            self._async_thread = threading.Thread(target=self._run_async_loop, daemon=True)
            self._async_thread.start()
        else:
            self.write_log(f"Asyncio loop thread for {self.gateway_name} already running.")

    async def _gather_tasks_for_shutdown(self, tasks_to_await: List[asyncio.Task]):
        """Helper to gather tasks during shutdown, ignoring CancelledError."""
        if tasks_to_await:
            await asyncio.gather(*tasks_to_await, return_exceptions=True)

    def _run_async_loop(self) -> None:
        """
        運行此 Handler 獨立的 asyncio 事件迴圈。
        此迴圈負責運行 _conflation_processor 和可能的 _queue_consumer。
        """
        asyncio.set_event_loop(self.loop)
        self.write_log(f"Asyncio loop for {self.gateway_name} is starting.")

        # 用於保存創建的 asyncio tasks，以便後續優雅關閉
        self._janus_consumer_task: Optional[asyncio.Task] = None # Assume _queue_consumer sets this if used

        try:
            # 創建並啟動行情聚合處理器任務
            if hasattr(self, '_conflation_processor'): # 確保方法存在
                self.write_log(f"Creating _conflation_processor task for {self.gateway_name}.")
                self._conflation_task = self.loop.create_task(self._conflation_processor())
            else:
                self.write_log(f"_conflation_processor method not found for {self.gateway_name}.", level="warning")

            # (可選) 如果 janus_queue 仍用於其他事件 (如訂單/成交)，則啟動其消費者
            if hasattr(self, '_queue_consumer'): # 確保方法存在
                self.write_log(f"Creating _queue_consumer task for {self.gateway_name} (if janus_queue is used).")
                # 假設您的 _queue_consumer 也被設計為一個協程任務
                self._janus_consumer_task = self.loop.create_task(self._queue_consumer()) 
            
            # 運行事件迴圈，直到 loop.stop() 被調用
            self.loop.run_forever()

        except Exception as e:
            self.write_log(f"Asyncio loop for {self.gateway_name} encountered an error: {e}\n{traceback.format_exc()}", level="error")
        finally:
            self.write_log(f"Asyncio loop for {self.gateway_name} is stopping...")
            
            # 在關閉迴圈前，嘗試優雅地取消並等待所有運行的主要異步任務
            # 這裡的 tasks_to_await 應該只包含我們明確創建的背景任務
            tasks_to_await_shutdown = []
            if self._conflation_task and not self._conflation_task.done():
                self._conflation_task.cancel()
                tasks_to_await_shutdown.append(self._conflation_task)
            
            if self._janus_consumer_task and not self._janus_consumer_task.done():
                self._janus_consumer_task.cancel()
                tasks_to_await_shutdown.append(self._janus_consumer_task)

            if tasks_to_await_shutdown:
                    # 運行一個臨時的協程來等待這些任務的取消完成
                    # self.loop.run_until_complete(self._gather_tasks_for_shutdown(tasks_to_await_shutdown))
                    # The above line can cause issues if loop is already stopping/closed.
                    # A common pattern is that tasks should handle CancelledError gracefully themselves.
                    self.write_log(f"Waiting for {len(tasks_to_await_shutdown)} tasks to cancel for {self.gateway_name}...")
                    # A simple way to allow cancellations to be processed if loop is not fully closed:
                    # for task in tasks_to_await_shutdown:
                    #     try:
                    #         self.loop.run_until_complete(asyncio.wait_for(task, timeout=1.0))
                    #     except (asyncio.CancelledError, asyncio.TimeoutError):
                    #         pass # Expected
                    #     except Exception as ex_task_wait:
                    #         self.write_log(f"Error waiting for task {task_name} during shutdown: {ex_task_wait}", level="warning")
                    pass # Rely on loop.close() to finalize task cleanup if they don't exit cleanly on cancel.
            # 再次確保迴圈已停止 (如果是由異常導致 run_forever 退出)
            if self.loop.is_running():
                self.loop.stop() 
            
            # 關閉事件迴圈。這會取消所有剩餘的掛起任務。
            self.loop.close()
            self.write_log(f"Asyncio loop for {self.gateway_name} definitively closed.")
  
    def on_event(self, event_type_str_key: str, data: Optional[Any] = None) -> None: # Renamed event_type to avoid clash
        """Generic event emission via manager. event_type_str_key is like 'eSubscribeSuccess' """
        # The manager_event_callback expects: (event_category, data_payload, vnpy_account_id_origin)
        # "event" category here is for VnPy's generic Event objects or simple key-value events
        self.manager_event_callback("event", {"type": event_type_str_key, "data": data}, self.vnpy_account_id)

    def on_tick(self, tick: TickData) -> None:
        tick.gateway_name = self.gateway_name # Handler's specific name
        self.manager_event_callback("tick", tick, self.vnpy_account_id)

    def on_order(self, order: OrderData) -> None:
        order.gateway_name = self.gateway_name
        order.accountid = self.vnpy_account_id # Ensure VnPy account ID is set
        self.manager_event_callback("order", order, self.vnpy_account_id)

    def on_trade(self, trade: TradeData) -> None:
        trade.gateway_name = self.gateway_name
        trade.accountid = self.vnpy_account_id # Ensure VnPy account ID is set
        self.manager_event_callback("trade", trade, self.vnpy_account_id)

    def on_account(self, account: AccountData) -> None:
        account.gateway_name = self.gateway_name
        account.accountid = self.vnpy_account_id # This is crucial
        self.manager_event_callback("account", account, self.vnpy_account_id)

    def on_position(self, position: PositionData) -> None:
        position.gateway_name = self.gateway_name
        position.accountid = self.vnpy_account_id # This is crucial
        self.manager_event_callback("position", position, self.vnpy_account_id)

    def on_contract(self, contract: ContractData) -> None:
        contract.gateway_name = self.gateway_name
        # contract.accountid is not standard, so not setting it here. Manager decides how to handle contracts from multiple sources.
        self.manager_event_callback("contract", contract, self.vnpy_account_id)

    # Override write_log to send LogData via manager if preferred
    def write_log(self, msg: str, level: str = "info"): # level is for guidance, LogData doesn't store it.
        log_data = LogData(msg=msg, gateway_name=self.gateway_name) # Handler's specific name
        # Log level could be part of the msg string: f"[{level.upper()}] {msg}"
        self.manager_event_callback("log", log_data, self.vnpy_account_id)

    async def _conflation_processor(self) -> None:
        """
        (Async Task) 行情聚合處理器。
        定期或在被觸發時，處理待處理的 vt_symbol 的最新行情數據。
        """
        self.write_log(f"Conflation processor for {self.gateway_name} started.")
        while True:
            try:
                # 等待觸發事件或超時
                # 如果 conflation_interval_sec <= 0, 則 wait() 會一直等待直到事件被 set
                # 如果 conflation_interval_sec > 0, 則 wait_for 會等待這麼長時間，或事件被 set (取先發生者)
                if self.conflation_interval_sec > 0:
                    await asyncio.wait_for(
                        self.conflation_trigger.wait(), 
                        timeout=self.conflation_interval_sec
                    )
                else: # Interval is 0 or negative, means process immediately on trigger
                    await self.conflation_trigger.wait()
                
            except asyncio.TimeoutError:
                # 超時是預期行為，表示按間隔時間處理
                pass 
            except asyncio.CancelledError:
                self.write_log(f"Conflation processor for {self.gateway_name} received cancellation.")
                break # 退出協程
            except Exception as e:
                self.write_log(f"Conflation processor {self.gateway_name} wait error: {e}", level="error")
                await asyncio.sleep(1) # 發生意外錯誤時短暫休眠
                continue # 繼續下一輪循環

            # 清除觸發器，為下一次觸發做準備
            if self.conflation_trigger.is_set():
                self.conflation_trigger.clear()

            symbols_to_process_now: Set[str]
            with self.pending_conflation_lock: # 安全地獲取並清空待處理列表
                if not self.pending_conflation_processing:
                    continue # 如果沒有待處理的，直接進入下一輪等待
                symbols_to_process_now = self.pending_conflation_processing.copy()
                self.pending_conflation_processing.clear()
            
            if not symbols_to_process_now:
                continue

            # self.write_log(f"Conflation processor: processing {len(symbols_to_process_now)} symbols.", level="debug")
            
            # 為每個待處理的 symbol 創建一個異步處理任務
            # 這些任務將在當前的 asyncio 事件迴圈中並行執行
            processing_tasks = []
            for vt_symbol in symbols_to_process_now:
                processing_tasks.append(self._process_conflated_data_async(vt_symbol))
            
            if processing_tasks:
                results = await asyncio.gather(*processing_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        # 從 tasks 中獲取對應的 symbol，需要一點技巧，或者在 task 中包含 symbol
                        # 簡化：只記錄錯誤
                        self.write_log(f"Error processing conflated data for a symbol: {result}", level="error")
        
        self.write_log(f"Conflation processor for {self.gateway_name} stopped.")

    async def _process_conflated_data_async(self, vt_symbol: str) -> None:
        """
        (Async Method) 處理單一 vt_symbol 的聚合後行情數據。
        從 latest_raw_... 快取中獲取最新原始數據，轉換為 VnPy TickData，
        更新 self.tick_cache，並調用 self.on_tick()。
        """
        try:
            # 1. 安全地獲取最新的原始數據 (tick 和 bid/ask)
            # 注意：這裡不再從 latest_raw_xxx 中 pop，因為它們存儲的是「最新值」
            # _conflation_processor 處理的是「哪些 symbol 有過更新」
            latest_raw_tick_stk: Optional[TickSTKv1] = None
            latest_raw_bidask_stk: Optional[BidAskSTKv1] = None
            latest_raw_tick_fop: Optional[TickFOPv1] = None
            latest_raw_bidask_fop: Optional[BidAskFOPv1] = None

            with self.raw_data_cache_lock: # 訪問原始數據快取需要加鎖
                latest_raw_tick_stk = self.latest_raw_ticks.get(vt_symbol)
                latest_raw_bidask_stk = self.latest_raw_bidasks.get(vt_symbol)
                latest_raw_tick_fop = self.latest_raw_fop_ticks.get(vt_symbol)
                latest_raw_bidask_fop = self.latest_raw_fop_bidasks.get(vt_symbol)

            if not any([latest_raw_tick_stk, latest_raw_bidask_stk, latest_raw_tick_fop, latest_raw_bidask_fop]):
                # self.write_log(f"No raw data found for conflation processing of {vt_symbol}", level="debug")
                return # 如果沒有任何原始數據，則不處理

            # 2. 獲取或創建 VnPy TickData 物件 (從 self.tick_cache)
            # self.tick_cache 用於合併來自 tick 和 bidask 的數據到同一個 VnPy TickData 物件
            # 需要用鎖保護對 self.tick_cache 的訪問
            vnpy_tick: TickData
            is_new_tick_object = False
            symbol, exchange_value = vt_symbol.split(".") # vt_symbol格式: "2330.TWSE"
            vn_exchange = Exchange(exchange_value) # 將字串轉回 VnPy Exchange Enum

            with self.tick_cache_lock:
                cached_vnpy_tick = self.tick_cache.get(vt_symbol)
                if cached_vnpy_tick:
                    vnpy_tick = cached_vnpy_tick
                else:
                    is_new_tick_object = True
                    # 創建一個基礎的 TickData 物件
                    vnpy_tick = TickData(
                        gateway_name=self.gateway_name,
                        symbol=symbol,
                        exchange=vn_exchange,
                        name=symbol, # 初始名稱，後面可以從合約或 Shioaji tick 中獲取更準確的名稱
                        datetime=datetime.now(TAIPEI_TZ) # 初始時間，會被覆蓋
                    )
                    # self.tick_cache[vt_symbol] = vnpy_tick # 先不放回去，等欄位都填好

            # 3. 使用最新的原始數據更新 VnPy TickData 物件的欄位
            #    這裡的邏輯來自您原始的 _process_tick_stk, _process_bidask_stk 等方法
            
            final_datetime_to_use = vnpy_tick.datetime # 保留最新的時間戳

            # 處理股票 Tick (TickSTKv1)
            if latest_raw_tick_stk:
                # 假設 latest_raw_tick_stk.datetime 是 Shioaji SDK 提供的 datetime 物件或可轉換的
                tick_dt = datetime.now(TAIPEI_TZ) # Default
                if isinstance(latest_raw_tick_stk.datetime, datetime):
                    tick_dt = latest_raw_tick_stk.datetime.replace(tzinfo=TAIPEI_TZ)
                elif isinstance(latest_raw_tick_stk.datetime, (int, float)): # e.g. nanoseconds
                    tick_dt = datetime.fromtimestamp(latest_raw_tick_stk.datetime / 1e9, tz=TAIPEI_TZ)
                
                # 如果是新物件或者新數據的時間戳更新，則更新時間
                if is_new_tick_object or tick_dt > final_datetime_to_use:
                    final_datetime_to_use = tick_dt
                
                vnpy_tick.name = getattr(latest_raw_tick_stk, 'name', vnpy_tick.name) # Shioaji TickSTKv1 可能沒有 name
                vnpy_tick.last_price = float(latest_raw_tick_stk.close)
                vnpy_tick.last_volume = float(latest_raw_tick_stk.volume) # volume of the last trade
                vnpy_tick.volume = float(latest_raw_tick_stk.total_volume) # cumulative volume
                vnpy_tick.turnover = float(getattr(latest_raw_tick_stk, 'total_amount', 0.0))
                vnpy_tick.open_price = float(latest_raw_tick_stk.open)
                vnpy_tick.high_price = float(latest_raw_tick_stk.high)
                vnpy_tick.low_price = float(latest_raw_tick_stk.low)
                if hasattr(latest_raw_tick_stk, 'price_chg') and latest_raw_tick_stk.price_chg is not None:
                    vnpy_tick.pre_close = float(latest_raw_tick_stk.close - latest_raw_tick_stk.price_chg)
                # limit_up, limit_down 等欄位通常來自快照或合約信息，TickSTKv1 可能不直接提供
                # vnpy_tick.limit_up = ...
                # vnpy_tick.limit_down = ...

            # 處理股票 BidAsk (BidAskSTKv1)
            if latest_raw_bidask_stk:
                bidask_dt = datetime.now(TAIPEI_TZ) # Default
                if isinstance(latest_raw_bidask_stk.datetime, datetime):
                    bidask_dt = latest_raw_bidask_stk.datetime.replace(tzinfo=TAIPEI_TZ)
                elif isinstance(latest_raw_bidask_stk.datetime, (int, float)):
                     bidask_dt = datetime.fromtimestamp(latest_raw_bidask_stk.datetime / 1e9, tz=TAIPEI_TZ)
                
                if is_new_tick_object or bidask_dt > final_datetime_to_use:
                     final_datetime_to_use = bidask_dt

                bids = getattr(latest_raw_bidask_stk, 'bid_price', [])
                b_vols = getattr(latest_raw_bidask_stk, 'bid_volume', [])
                asks = getattr(latest_raw_bidask_stk, 'ask_price', [])
                a_vols = getattr(latest_raw_bidask_stk, 'ask_volume', [])
                for i in range(5):
                    setattr(vnpy_tick, f"bid_price_{i+1}", float(bids[i]) if i < len(bids) else 0.0)
                    setattr(vnpy_tick, f"bid_volume_{i+1}", float(b_vols[i]) if i < len(b_vols) else 0.0)
                    setattr(vnpy_tick, f"ask_price_{i+1}", float(asks[i]) if i < len(asks) else 0.0)
                    setattr(vnpy_tick, f"ask_volume_{i+1}", float(a_vols[i]) if i < len(a_vols) else 0.0)

            # 處理期貨/期權 Tick (TickFOPv1) - 與股票類似，但欄位可能不同
            if latest_raw_tick_fop:
                fop_tick_dt = datetime.now(TAIPEI_TZ)
                if isinstance(latest_raw_tick_fop.datetime, datetime):
                    fop_tick_dt = latest_raw_tick_fop.datetime.replace(tzinfo=TAIPEI_TZ)
                elif isinstance(latest_raw_tick_fop.datetime, (int, float)):
                    fop_tick_dt = datetime.fromtimestamp(latest_raw_tick_fop.datetime / 1e9, tz=TAIPEI_TZ)

                if is_new_tick_object or fop_tick_dt > final_datetime_to_use:
                    final_datetime_to_use = fop_tick_dt
                
                vnpy_tick.name = getattr(latest_raw_tick_fop, 'name', vnpy_tick.name)
                vnpy_tick.last_price = float(latest_raw_tick_fop.close)
                vnpy_tick.last_volume = float(latest_raw_tick_fop.volume)
                vnpy_tick.volume = float(latest_raw_tick_fop.total_volume)
                vnpy_tick.open_price = float(latest_raw_tick_fop.open)
                vnpy_tick.high_price = float(latest_raw_tick_fop.high)
                vnpy_tick.low_price = float(latest_raw_tick_fop.low)
                vnpy_tick.open_interest = float(getattr(latest_raw_tick_fop, 'open_interest', 0.0))
                if hasattr(latest_raw_tick_fop, 'price_chg') and latest_raw_tick_fop.price_chg is not None:
                    vnpy_tick.pre_close = float(latest_raw_tick_fop.close - latest_raw_tick_fop.price_chg)
                vnpy_tick.limit_up = float(getattr(latest_raw_tick_fop, 'limit_up', 0.0))
                vnpy_tick.limit_down = float(getattr(latest_raw_tick_fop, 'limit_down', 0.0))
                # FOP ticks usually don't have turnover
                vnpy_tick.turnover = 0.0 

            # 處理期貨/期權 BidAsk (BidAskFOPv1) - 與股票類似
            if latest_raw_bidask_fop:
                fop_bidask_dt = datetime.now(TAIPEI_TZ)
                if isinstance(latest_raw_bidask_fop.datetime, datetime):
                    fop_bidask_dt = latest_raw_bidask_fop.datetime.replace(tzinfo=TAIPEI_TZ)
                elif isinstance(latest_raw_bidask_fop.datetime, (int, float)):
                     fop_bidask_dt = datetime.fromtimestamp(latest_raw_bidask_fop.datetime / 1e9, tz=TAIPEI_TZ)

                if is_new_tick_object or fop_bidask_dt > final_datetime_to_use:
                     final_datetime_to_use = fop_bidask_dt
                
                bids = getattr(latest_raw_bidask_fop, 'bid_price', [])
                b_vols = getattr(latest_raw_bidask_fop, 'bid_volume', [])
                asks = getattr(latest_raw_bidask_fop, 'ask_price', [])
                a_vols = getattr(latest_raw_bidask_fop, 'ask_volume', [])
                for i in range(5): # Assuming FOP also has 5 levels
                    setattr(vnpy_tick, f"bid_price_{i+1}", float(bids[i]) if i < len(bids) else 0.0)
                    setattr(vnpy_tick, f"bid_volume_{i+1}", float(b_vols[i]) if i < len(b_vols) else 0.0)
                    setattr(vnpy_tick, f"ask_price_{i+1}", float(asks[i]) if i < len(asks) else 0.0)
                    setattr(vnpy_tick, f"ask_volume_{i+1}", float(a_vols[i]) if i < len(a_vols) else 0.0)

            # 設定最終的時間戳和本地接收時間
            vnpy_tick.datetime = final_datetime_to_use
            vnpy_tick.localtime = datetime.now() # 本地接收並處理完成的時間

            # 4. 更新 self.tick_cache 並推送
            with self.tick_cache_lock:
                self.tick_cache[vt_symbol] = vnpy_tick # 更新快取中的合併後 TickData
            
            self.on_tick(copy.copy(vnpy_tick)) # 推送副本給 Manager

        except Exception as e:
            self.write_log(f"處理聚合行情數據 _process_conflated_data_async for {vt_symbol} 時出錯: {e}\n{traceback.format_exc()}", level="error")


    def _on_tick_stk(self, exchange: SjExchange, tick: TickSTKv1) -> None:
        """(Conflation Optimized) 處理 Shioaji 股票 TickSTKv1 原始數據。"""
        # 從 Shioaji Exchange Enum 獲取 VnPy Exchange 字串值
        vn_exchange_val = getattr(self.sj2vnpy.get(exchange), 'value', None)
        if not vn_exchange_val:
            # 在高頻回調中應謹慎打日誌，避免性能影響
            # self.write_log(f"未知交易所 '{exchange}' (股票Tick), 代碼 {tick.code}", level="debug")
            return 
        
        vt_symbol = f"{tick.code}.{vn_exchange_val}"

        # 更新最新原始 Tick 快取 (線程安全)
        with self.raw_data_cache_lock:
            self.latest_raw_ticks[vt_symbol] = tick 
        
        # 將此 vt_symbol 加入待處理集合 (線程安全)
        with self.pending_conflation_lock:
            self.pending_conflation_processing.add(vt_symbol)
            
        # 線程安全地觸發 asyncio 事件，喚醒聚合處理器
        if self.loop.is_running(): # 確保迴圈正在運行
            self.loop.call_soon_threadsafe(self.conflation_trigger.set)

    def _on_bidask_stk(self, exchange: SjExchange, bidask: BidAskSTKv1) -> None:
        """(Conflation Optimized) 處理 Shioaji 股票 BidAskSTKv1 原始數據。"""
        vn_exchange_val = getattr(self.sj2vnpy.get(exchange), 'value', None)
        if not vn_exchange_val:
            return
        vt_symbol = f"{bidask.code}.{vn_exchange_val}"

        with self.raw_data_cache_lock:
            self.latest_raw_bidasks[vt_symbol] = bidask
            
        with self.pending_conflation_lock:
            self.pending_conflation_processing.add(vt_symbol)
            
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.conflation_trigger.set)

    def _on_tick_fop(self, exchange: SjExchange, tick: TickFOPv1) -> None:
        """(Conflation Optimized) 處理 Shioaji 期權/期貨 TickFOPv1 原始數據。"""
        vn_exchange_val = getattr(self.sj2vnpy.get(exchange), 'value', None)
        if not vn_exchange_val:
            return
        vt_symbol = f"{tick.code}.{vn_exchange_val}"
        
        with self.raw_data_cache_lock:
            self.latest_raw_fop_ticks[vt_symbol] = tick # 更新期貨/期權的 Tick 快取
            
        with self.pending_conflation_lock:
            self.pending_conflation_processing.add(vt_symbol)
            
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.conflation_trigger.set)

    def _on_bidask_fop(self, exchange: SjExchange, bidask: BidAskFOPv1) -> None:
        """(Conflation Optimized) 處理 Shioaji 期權/期貨 BidAskFOPv1 原始數據。"""
        vn_exchange_val = getattr(self.sj2vnpy.get(exchange), 'value', None)
        if not vn_exchange_val:
            return
        vt_symbol = f"{bidask.code}.{vn_exchange_val}"

        with self.raw_data_cache_lock:
            self.latest_raw_fop_bidasks[vt_symbol] = bidask # 更新期貨/期權的 BidAsk 快取
            
        with self.pending_conflation_lock:
            self.pending_conflation_processing.add(vt_symbol)
            
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.conflation_trigger.set)

    def connect(self, session_setting: dict) -> None:
        """
        初始化此 Handler 的設定，啟動其內部 asyncio 事件迴圈，並開始連接 Shioaji API。
        session_setting: 包含此特定 session 連線所需配置的字典。
        """
        if self.connected or self.logged_in:
            self.write_log(
                f"Handler {self.gateway_name} (Acc: {self.vnpy_account_id}) 已連線或已登入，無需重複操作。"
            )
            return
        
        if self.connect_thread and self.connect_thread.is_alive():
            self.write_log(
                f"Handler {self.gateway_name} (Acc: {self.vnpy_account_id}) 的連線執行緒已在運行中。"
            )
            return

        self.session_setting = session_setting  # 保存此 session 的專用設定

        # 從 session_setting 中提取並設定 Handler 的屬性
        self.reconnect_limit = int(self.session_setting.get("重連次數", 3))
        self.reconnect_interval = int(self.session_setting.get("重連間隔(秒)", 5))
        self.simulation = self.session_setting.get("simulation", False)
        self.api_key = self.session_setting.get("APIKey", "")
        self.secret_key = self.session_setting.get("SecretKey", "")
        self.ca_path = str(self.session_setting.get("CA路徑", "")).replace("\\", "/") # 確保路徑格式
        self.ca_passwd = self.session_setting.get("CA密碼", "")
        self.person_id_setting = self.session_setting.get("身分證字號", "")
        self.force_download = self.session_setting.get("下載合約", True)
        
        # 讀取行情聚合（Conflation）的時間間隔設定
        self.conflation_interval_sec = float(self.session_setting.get("conflation_interval_sec", 0.050))
        if self.conflation_interval_sec < 0:  # 不可為負
            self.conflation_interval_sec = 0.0
        self.write_log(
            f"Handler {self.gateway_name}: 行情聚合間隔設為 {self.conflation_interval_sec} 秒 (0 表示盡快處理)。"
        )

        # (可選) 讀取 Janus Queue 的批次收集超時 (如果 janus_queue 仍用於訂單/成交等非聚合事件)
        self.batch_collect_timeout = float(self.session_setting.get("janus_batch_timeout_sec", 0.1))
        self.write_log(
            f"Handler {self.gateway_name}: Janus Queue 批次收集超時設為 {self.batch_collect_timeout} 秒。"
        )

        self.write_log(
            f"Handler {self.gateway_name} (Acc: {self.vnpy_account_id}) "
            f"收到連線請求。模擬模式: {self.simulation}。"
        )

        # 確保此 Handler 的 asyncio 事件迴圈已啟動
        # _start_async_loop_thread 內部會調用 _run_async_loop
        self._start_async_loop_thread()

        # 記錄連線開始時間並啟動 _connect_worker 執行緒
        self.connection_start_time = time.time()
        self.connect_thread = Thread(
            target=self._connect_worker, 
            args=(self.session_setting,) # 將此 session 的設定傳給 worker
        )
        self.connect_thread.daemon = True
        self.connect_thread.start()

    def _connect_worker(self, setting: dict) -> None:
        self.reconnect_attempts = 0
        raw_accounts_data: Any = None
        logged_in_person_id: Optional[str] = None

        self.write_log(
            f"Handler {self.gateway_name} (Acc: {self.vnpy_account_id}): Connect worker started. "
            f"Simulation: {self.simulation}"
        )
        try:
            if not self.api_key or not self.secret_key:
                self.write_log(f"Handler {self.gateway_name}: Missing APIKey or SecretKey. Connection aborted.", level="error")
                self._handle_disconnect()
                return

            self.api = sj.Shioaji(simulation=self.simulation)
            self.write_log(f"Handler {self.gateway_name}: Shioaji API instance created (sim={self.simulation}).")

            self.write_log(f"Handler {self.gateway_name}: Attempting Shioaji API login...")
            login_result = self.api.login(
                api_key=self.api_key,
                secret_key=self.secret_key,
                fetch_contract=False,
                subscribe_trade=True,
                contracts_timeout=0
            )
            self.write_log(f"Handler {self.gateway_name}: Shioaji API login call completed.")

            if isinstance(login_result, tuple) and len(login_result) >= 1:
                raw_accounts_data = login_result[0]
                if len(login_result) >= 3:
                    logged_in_person_id = login_result[2]
            elif isinstance(login_result, list):
                raw_accounts_data = login_result
            else:
                 self.write_log(f"Handler {self.gateway_name}: Unexpected login result type: {type(login_result)}. Result: {login_result}", level="error")
                 self._handle_disconnect()
                 return
            self.write_log(f"Handler {self.gateway_name}: Logged in Person ID from API: {logged_in_person_id}")

            accounts_list: List[Union[SjAccount, SjStockAccount, SjFutureAccount]] = []
            if isinstance(raw_accounts_data, list):
                accounts_list = raw_accounts_data
            elif isinstance(raw_accounts_data, (SjAccount, SjStockAccount, SjFutureAccount)):
                accounts_list = [raw_accounts_data]

            if not accounts_list:
                self.write_log(f"Handler {self.gateway_name}: No accounts returned from Shioaji login.", level="warning")
            else:
                self.write_log(f"Handler {self.gateway_name}: Received {len(accounts_list)} account(s) from Shioaji.")
                stock_default_set = False
                future_default_set = False
                for acc_obj in accounts_list:
                    if not hasattr(acc_obj, 'account_type') or not hasattr(acc_obj, 'account_id'):
                        self.write_log(f"Handler {self.gateway_name}: Invalid account object in list: {acc_obj}", level="warning")
                        continue
                    if acc_obj.account_type == SjAccountType.Stock and not stock_default_set:
                        self.api.set_default_account(acc_obj)
                        stock_default_set = True
                    elif acc_obj.account_type == SjAccountType.Future and not future_default_set:
                        self.api.set_default_account(acc_obj)
                        future_default_set = True
                    if stock_default_set and future_default_set: break
                if not self.api.stock_account and not self.api.futopt_account:
                     self.write_log(f"Handler {self.gateway_name}: Failed to set any default Shioaji accounts.", level="warning")

            self.write_log(f"Handler {self.gateway_name}: 準備獲取/處理合約 (force_download={self.force_download}).")
            self._all_contracts_fetched_event.clear()
            self.fetched_security_types.clear()
            with self._processing_contracts_lock: self._contracts_processed_flag = False
            initial_contracts_status = getattr(getattr(self.api, 'Contracts', None), 'status', SjFetchStatus.Unfetch)
            needs_explicit_fetch_call = self.force_download or initial_contracts_status != SjFetchStatus.Fetched
            if needs_explicit_fetch_call:
                self.write_log(f"Handler {self.gateway_name}: 呼叫 self.api.fetch_contracts() (非阻塞)...")
                try:
                    self.api.fetch_contracts(contract_download=True, contracts_timeout=0, contracts_cb=self._contracts_cb)
                except Exception as e_fetch_call:
                    self.write_log(f"Handler {self.gateway_name}: 呼叫 api.fetch_contracts() 錯誤: {e_fetch_call}", level="error")
                    self._handle_disconnect(); return
            else:
                self.write_log(f"Handler {self.gateway_name}: 合約狀態已為 {initial_contracts_status}，且未強制下載。發送已就緒信號。")
                self._all_contracts_fetched_event.set()
            contracts_cb_timeout = float(self.session_setting.get("contracts_cb_timeout_sec", 60.0))
            self.write_log(f"Handler {self.gateway_name}: 等待合約下載回調 (最多 {contracts_cb_timeout}秒)...")
            all_callbacks_event_set = self._all_contracts_fetched_event.wait(timeout=contracts_cb_timeout)
            current_contracts_status_after_wait = getattr(getattr(self.api, 'Contracts', None), 'status', 'N/A_Status')
            if all_callbacks_event_set: self.write_log(f"Handler {self.gateway_name}: 合約回調事件已設置。合約狀態: {current_contracts_status_after_wait}")
            else: self.write_log(f"Handler {self.gateway_name}: 等待所有合約回調超時 ({contracts_cb_timeout}秒)。合約狀態: {current_contracts_status_after_wait}", level="warning")
            with self._processing_contracts_lock:
                if not self._contracts_processed_flag:
                    if (hasattr(self.api, 'Contracts') and self.api.Contracts.status == SjFetchStatus.Fetched) or \
                       (all_callbacks_event_set and self.fetched_security_types.issuperset(self.expected_security_types)):
                        if hasattr(self.api, 'Contracts') and self.api.Contracts.status != SjFetchStatus.Fetched and all_callbacks_event_set:
                            self.write_log(f"Handler {self.gateway_name}: 所有回調已收到，但 Contracts.status 為 {self.api.Contracts.status}。仍嘗試處理。", level="info")
                        self.write_log(f"Handler {self.gateway_name}: 呼叫 _process_contracts()。")
                        self._process_contracts()
                        self._contracts_processed_flag = True
                        self.on_event(EVENT_CONTRACTS_LOADED, {"vnpy_account_id": self.vnpy_account_id, "status": "success"})
                    else:
                        self.write_log(f"Handler {self.gateway_name}: 合約未處理。最終狀態: {current_contracts_status_after_wait}。所有預期類型回調是否收到: {all_callbacks_event_set and self.fetched_security_types.issuperset(self.expected_security_types)}。", level="error")
                        self.on_event(EVENT_CONTRACTS_LOADED, {"vnpy_account_id": self.vnpy_account_id, "status": "failed"})

            person_id_for_ca = self.person_id_setting or logged_in_person_id
            if not self.simulation and self.ca_path and self.ca_passwd and person_id_for_ca:
                self.write_log(f"Handler {self.gateway_name}: Attempting CA activation for Person ID: {person_id_for_ca}...")
                try:
                    self.api.activate_ca(ca_path=self.ca_path, ca_passwd=self.ca_passwd, person_id=person_id_for_ca)
                    self.write_log(f"Handler {self.gateway_name}: CA activation successful for {person_id_for_ca}.")
                except Exception as e_ca:
                    self.write_log(f"Handler {self.gateway_name}: CA activation failed for {person_id_for_ca}: {e_ca}", level="warning")
            elif not self.simulation: self.write_log(f"Handler {self.gateway_name}: CA path, password, or Person ID not fully provided for CA activation. Skipping.", level="info")
            else: self.write_log(f"Handler {self.gateway_name}: Simulation mode, skipping CA activation.", level="info")

            self._set_callbacks()

            self.connected = True
            self.logged_in = True
            self.write_log(
                f"Handler {self.gateway_name} (Acc: {self.vnpy_account_id}) connection fully established."
            )
            self.reconnect_attempts = 0

            self.manager_event_callback(
                "session_status",
                {"status": "connected", "vnpy_account_id": self.vnpy_account_id, "gateway_name": self.gateway_name},
                self.vnpy_account_id
            )
            self.query_all_handler_data()
            # --- 新增：重新訂閱之前已訂閱的行情 ---
            self.write_log(f"Handler {self.gateway_name}: Attempting to re-subscribe to previously subscribed symbols...")
            # 創建一個 self.subscribed 的副本進行迭代，因為 self.subscribe 方法可能會修改 self.subscribed
            symbols_to_resubscribe: Set[str]
            with self.subscribed_lock:
                symbols_to_resubscribe = self.subscribed.copy()

            if not symbols_to_resubscribe:
                self.write_log(f"Handler {self.gateway_name}: No previously subscribed symbols to re-subscribe.")
            else:
                self.write_log(f"Handler {self.gateway_name}: Will attempt to re-subscribe to: {symbols_to_resubscribe}")
                # 在重新訂閱前，先清空 Handler 內部的 self.subscribed 記錄，
                # 讓 subscribe 方法能夠正確地重新加入成功的訂閱。
                # 這是因為 subscribe 方法現在會無條件嘗試 API 調用。
                with self.subscribed_lock:
                    self.subscribed.clear()

                for vt_symbol in symbols_to_resubscribe:
                    try:
                        symbol_code, exchange_value = vt_symbol.split(".")
                        vn_exchange = Exchange(exchange_value)
                        # 創建一個新的 SubscribeRequest 來調用 self.subscribe
                        # 這確保了即使 Manager 沒有主動發起新的訂閱請求，Handler 也能恢復其狀態
                        resub_req = SubscribeRequest(symbol=symbol_code, exchange=vn_exchange)
                        self.write_log(f"Handler {self.gateway_name}: Re-subscribing to {vt_symbol}...")
                        self.subscribe(resub_req) # 調用修改後的 subscribe 方法
                    except ValueError:
                        self.write_log(f"Handler {self.gateway_name}: 無法解析 vt_symbol '{vt_symbol}' 以進行重新訂閱。", level="error")
                    except Exception as e_resub:
                        self.write_log(f"Handler {self.gateway_name}: 重新訂閱 {vt_symbol} 時發生錯誤: {e_resub}", level="error")
            # --- END 重新訂閱 ---

            # 在所有操作完成後，最終通知 Manager 連接成功
            self.manager_event_callback(
                "session_status",
                {"status": "connected", "vnpy_account_id": self.vnpy_account_id, "gateway_name": self.gateway_name},
                self.vnpy_account_id
            )
            self.write_log(f"Handler {self.gateway_name} (Acc: {self.vnpy_account_id}) full connection and re-subscription process completed.")



        except SjTokenError as e_token:
            self.write_log(f"Handler {self.gateway_name}: Shioaji API login TokenError: {e_token}", level="critical")
            self._handle_disconnect()
        except ValueError as e_value:
             self.write_log(f"Handler {self.gateway_name}: ValueError during connection (check API keys for non-ASCII?): {e_value}\n{traceback.format_exc()}", level="critical")
             self._handle_disconnect()
        except Exception as e_outer:
            self.write_log(f"Handler {self.gateway_name}: Unhandled exception in connect worker: {e_outer}\n{traceback.format_exc()}", level="critical")
            self._handle_disconnect()
            
    def close(self) -> None:
        self.write_log(f"Handler {self.gateway_name} (Acc: {self.vnpy_account_id}): Initiating close sequence...")

        # 1. 登出 Shioaji API
        if self.logged_in and self.api:
            try:
                self.api.logout()
                self.write_log(f"Handler {self.gateway_name}: API logged out.")
            except Exception as e:
                self.write_log(f"Handler {self.gateway_name}: Error during API logout: {e}", level="error")
        
        self.logged_in = False # Mark as logged out
        self.connected = False # Mark as disconnected

        # 2. 請求 asyncio 事件迴圈停止
        # This needs to be done carefully.
        if self.loop.is_running():
            self.write_log(f"Handler {self.gateway_name}: Requesting asyncio loop to stop.")
            self.loop.call_soon_threadsafe(self.loop.stop)
            # Tasks (_conflation_task, _janus_consumer_task) should ideally catch CancelledError and exit.
            # loop.stop() will cause run_forever() to exit. The finally block in _run_async_loop will then execute.
        
        # 3. 等待 asyncio 執行緒結束
        if self._async_thread and self._async_thread.is_alive():
            self.write_log(f"Handler {self.gateway_name}: Waiting for asyncio thread to join...")
            self._async_thread.join(timeout=5.0) # Give it some time to shutdown gracefully
            if self._async_thread.is_alive():
                self.write_log(f"Handler {self.gateway_name}: Asyncio thread did not join in time.", level="warning")
        
        # Note: self.loop.close() is called in the _run_async_loop's finally block,
        # which runs in _async_thread. This is the correct place to close the loop.

        # 4. 清理其他資源
        self.api = None
        if self._reconnect_timer and self._reconnect_timer.is_alive():
            self._reconnect_timer.cancel() # Cancel any pending reconnect attempts

        # Clear caches (optional, depending on desired behavior on reconnect)
        # with self.order_map_lock: self.orders.clear(); self.shioaji_trades.clear(); self.shioaji_deals.clear()
        # with self.position_lock: self.positions.clear()
        # with self.contract_lock: self.contracts.clear()
        # with self.subscribed_lock: self.subscribed.clear()
        # with self.tick_cache_lock: self.tick_cache.clear()
        # with self.raw_data_cache_lock: 
        #     self.latest_raw_ticks.clear(); self.latest_raw_bidasks.clear()
        #     self.latest_raw_fop_ticks.clear(); self.latest_raw_fop_bidasks.clear()
        # with self.pending_conflation_lock: self.pending_conflation_processing.clear()

        self.write_log(f"Handler {self.gateway_name} (Acc: {self.vnpy_account_id}) definitively closed.")

    def _set_callbacks(self): # This remains mostly the same, sets callbacks on self.api
        if not self.api:
            self.write_log(f"Handler {self.gateway_name}: Cannot set callbacks, API not initialized.", level="warning")
            return
        self.api.quote.set_on_tick_stk_v1_callback(self._on_tick_stk)
        self.api.quote.set_on_tick_fop_v1_callback(self._on_tick_fop)
        self.api.quote.set_on_bidask_stk_v1_callback(self._on_bidask_stk)
        self.api.quote.set_on_bidask_fop_v1_callback(self._on_bidask_fop)
        self.api.set_order_callback(self._on_order_deal_shioaji) # This processes orders for this session
        if hasattr(self.api, "set_session_down_callback"):
            self.api.set_session_down_callback(self._on_session_down) # Handles session down for THIS api instance
        self.write_log(f"Handler {self.gateway_name}: Shioaji callbacks set.")

    def _on_session_down(self): # Specific to this handler's session
        self.write_log(f"Handler {self.gateway_name}: Detected session down for vnpy_account_id {self.vnpy_account_id}.", level="warning")
        self._handle_disconnect() # Triggers this handler's reconnect logic or cleanup

    def _handle_disconnect(self): # Specific to this handler
        if not self.connected and not self.logged_in:
            return # Already disconnected

        was_connected = self.connected
        self.connected = False
        self.logged_in = False
        self.write_log(f"Handler {self.gateway_name}: Disconnected. Was connected: {was_connected}")
        
        if was_connected and self.reconnect_limit > 0 : # Only if it was properly connected before
            self.write_log(f"Handler {self.gateway_name}: Attempting to reconnect.")
            self._start_reconnect() # This handler's reconnect
        else:
            self.write_log(f"Handler {self.gateway_name}: Not attempting reconnect (was not connected or reconnect limit 0).")
            # Notify manager that this session is definitively down if reconnects exhausted/disabled
            self.manager_event_callback("session_status", {"status": "disconnected_failed", "vnpy_account_id": self.vnpy_account_id}, self.vnpy_account_id)


    def _check_connection(self, check_ca: bool = False) -> bool: # Specific to this handler's API
        if not self.connected or not self.logged_in or not self.api:
            self.write_log(f"Handler {self.gateway_name}: API not connected or not logged in.", level="warning")
            return False
        if check_ca and not self.simulation:
            # This check is tricky. The `self.api.stock_account` might not be set if login didn't yield accounts.
            # Better to check `self.api.is_ca_activated()` or similar if Shioaji provides it,
            # or check specific account signed status after ensuring default accounts are set.
            # For simplicity, we rely on Shioaji to raise AccountNotSignError during place_order if CA is needed and not signed.
            # A more proactive check could be:
            # stock_acc_signed = self.api.stock_account.signed if self.api.stock_account else False
            # futopt_acc_signed = self.api.futopt_account.signed if self.api.futopt_account else False
            # if not (stock_acc_signed or futopt_acc_signed): # If any relevant account type needs to be signed
            #     self.write_log(f"Handler {self.gateway_name}: CA check failed - relevant account not signed.", level="warning")
            #     return False 
            pass # Rely on Shioaji's error for now
        return True

    def query_all_handler_data(self): # Renamed from query_all
        """Queries initial data for this specific session upon connection."""
        self.query_account() # Queries this session's account details
        self.query_position()# Queries this session's positions
        # No timer-based requery here; manager can orchestrate periodic queries if needed.

    # query_account and query_position will now create AccountData/PositionData
    # with `accountid` set to `self.vnpy_account_id` and `gateway_name` to `self.gateway_name`
    # then call self.on_account() / self.on_position() which in turn call manager_event_callback.

    def query_account(self) -> None: # Queries THIS session's account(s)
        if not self._check_connection(): 
            # self.write_log(f"Handler {self.gateway_name}: Cannot query account, not connected.") # Logged in _check_connection
            return
        
        thread_name = threading.current_thread().name # For logging if needed within this specific method
        self.write_log(f"Handler {self.gateway_name}: Querying account details...")

        try:
            # Ensure Shioaji default accounts are set if API calls rely on them implicitly
            # This should have been done in _connect_worker using self.api.set_default_account(shioaji_account_object)
            # For example, self.api.stock_account and self.api.futopt_account should be valid Shioaji account objects.

            # Query and process stock account details
            if self.api.stock_account and self.api.stock_account.signed:
                try:
                    # self.write_log(f"Handler {self.gateway_name}: Querying stock account balance for {self.api.stock_account.account_id}...")
                    balance_info = self.api.account_balance() # <<< MODIFIED: Removed 'account' argument
                    if balance_info:
                        acc_data = AccountData(
                            accountid=self.vnpy_account_id, # Use the VnPy level account ID for this handler
                            balance=float(balance_info.acc_balance),
                            frozen=0.0, # Shioaji stock balance might not directly show 'frozen' for open orders
                            gateway_name=self.gateway_name 
                        )
                        self.on_account(acc_data) # Send to manager
                    else:
                        self.write_log(f"Handler {self.gateway_name}: Received no balance info for stock account.", level="warning")
                except Exception as e_bal_stk:
                    self.write_log(f"Handler {self.gateway_name}: Error querying stock account balance: {e_bal_stk}\n{traceback.format_exc()}", level="error")
            elif self.api.stock_account and not self.api.stock_account.signed:
                self.write_log(f"Handler {self.gateway_name}: Stock account {self.api.stock_account.account_id} not signed. Skipping balance query.", level="info")


            # Query and process futures/options account details
            if self.api.futopt_account and self.api.futopt_account.signed:
                try:
                    # self.write_log(f"Handler {self.gateway_name}: Querying futures/options margin for {self.api.futopt_account.account_id}...")
                    margin_info = self.api.margin(account=self.api.futopt_account) # margin() usually needs the specific account
                    if margin_info:
                        acc_data = AccountData(
                            accountid=self.vnpy_account_id, # Use the VnPy level account ID
                            balance=float(margin_info.equity_amount),
                            frozen=float(margin_info.initial_margin + margin_info.order_margin_premium), # Approximation of frozen
                            gateway_name=self.gateway_name 
                        )
                        self.on_account(acc_data) # Send to manager
                    else:
                        self.write_log(f"Handler {self.gateway_name}: Received no margin info for futopt account.", level="warning")
                except Exception as e_margin_fop:
                    self.write_log(f"Handler {self.gateway_name}: Error querying futopt account margin: {e_margin_fop}\n{traceback.format_exc()}", level="error")
            elif self.api.futopt_account and not self.api.futopt_account.signed:
                 self.write_log(f"Handler {self.gateway_name}: Futopt account {self.api.futopt_account.account_id} not signed. Skipping margin query.", level="info")

        except Exception as e_query_acc_main:
            self.write_log(f"Handler {self.gateway_name}: General error in query_account: {e_query_acc_main}\n{traceback.format_exc()}", level="error")

# Inside ShioajiSessionHandler class:

    def _process_api_positions(
        self,
        api_positions_list: Optional[List[Union[SjStockPosition, SjFuturePosition]]],
        current_handler_positions_keys_tracker: Set[Tuple[str, Direction]]
    ) -> None:
        if not api_positions_list:
            return

        for pos_from_api in api_positions_list:
            try:
                code = getattr(pos_from_api, 'code', None)
                if not code:
                    self.write_log(f"Handler {self.gateway_name}: 持倉物件缺少 'code' 屬性，已跳過。持倉: {pos_from_api}", level="warning")
                    continue

                # 步驟 1: 使用 code 調用 find_sj_contract (不提供交易所提示，讓 find_sj_contract 自行查找)
                sj_contract_details = self.find_sj_contract(code) 

                if not sj_contract_details:
                    self.write_log(f"Handler {self.gateway_name}: 未能找到代碼為 '{code}' 的合約詳細信息，已跳過此持倉。", level="warning")
                    continue

                # 步驟 2: 從找到的合約詳細信息中獲取可靠的 SjExchange，並映射到 VnPy Exchange
                reliable_sj_exchange = sj_contract_details.exchange
                vn_exchange_derived = self.sj2vnpy.get(reliable_sj_exchange)

                if not vn_exchange_derived:
                    self.write_log(f"Handler {self.gateway_name}: 無法映射來自合約的 Shioaji 交易所 '{reliable_sj_exchange.value}' for code {code}，已跳過此持倉。", level="warning")
                    continue
                
                # 至此，vn_exchange_derived 是基於可靠合約信息的 VnPy Exchange Enum

                vt_symbol = f"{code}.{vn_exchange_derived.value}"
                vn_direction = DIRECTION_MAP_REVERSE.get(pos_from_api.direction)
                if not vn_direction:
                    self.write_log(f"Handler {self.gateway_name}: 持倉 {vt_symbol} 方向未知 ({pos_from_api.direction})，已跳過。", level="warning")
                    continue
                
                # volume, yd_volume, frozen, price, pnl 的計算邏輯保持不變
                volume = float(pos_from_api.quantity)
                yd_volume = 0.0
                if isinstance(pos_from_api, SjStockPosition) and hasattr(pos_from_api, 'yd_quantity'):
                    yd_volume = float(pos_from_api.yd_quantity)
                
                frozen = 0.0 
                if isinstance(pos_from_api, SjStockPosition):
                    frozen = max(0.0, volume - yd_volume) 

                position_data = PositionData(
                    gateway_name=self.gateway_name,
                    symbol=code,
                    exchange=vn_exchange_derived, # 使用從合約中確定的 VnPy Exchange
                    direction=vn_direction,
                    volume=volume,
                    yd_volume=yd_volume,
                    frozen=frozen,
                    price=float(pos_from_api.price),
                    pnl=float(getattr(pos_from_api, 'pnl', 0.0)),
                    #accountid=self.vnpy_account_id
                )
                
                position_key = (position_data.vt_symbol, position_data.direction)
                current_handler_positions_keys_tracker.add(position_key)

                with self.position_lock:
                    self.positions[position_key] = position_data
                
                self.on_position(position_data)

            except Exception as e_pos_item:
                item_code_for_log = getattr(pos_from_api, 'code', 'UNKNOWN_CODE_IN_LOOP')
                self.write_log(f"Handler {self.gateway_name}: 處理單筆持倉時發生錯誤 (Code: {item_code_for_log}): {e_pos_item}\n{traceback.format_exc()}", level="error")

    def query_position(self) -> None: # Queries THIS session's positions
        if not self._check_connection():
            return
        
        self.write_log(f"Handler {self.gateway_name}: Querying positions...")
        
        received_position_keys_in_current_query: Set[Tuple[str, Direction]] = set()

        with self.position_lock:
            previous_handler_position_keys: Set[Tuple[str, Direction]] = set(self.positions.keys())

        try:
            # 查詢股票持倉
            if self.api.stock_account:
                stock_account_id_for_log = getattr(self.api.stock_account, 'account_id', 'N/A')
                self.write_log(f"Handler {self.gateway_name}: Listing stock positions for account {stock_account_id_for_log} (unit: Share)...")
                stock_positions = self.api.list_positions(
                    account=self.api.stock_account, 
                    unit=SjUnit.Share # 股票通常以「股」為單位查詢
                )
                self._process_api_positions(stock_positions, received_position_keys_in_current_query)

            # 查詢期貨/選擇權持倉
            if self.api.futopt_account:
                futopt_account_id_for_log = getattr(self.api.futopt_account, 'account_id', 'N/A')
                self.write_log(f"Handler {self.gateway_name}: Listing futopt positions for account {futopt_account_id_for_log} (unit: Common)...")
                futopt_positions = self.api.list_positions(
                    account=self.api.futopt_account, 
                    unit=SjUnit.Common # <<< MODIFIED: 使用 SjUnit.Common 代表「口」
                )
                self._process_api_positions(futopt_positions, received_position_keys_in_current_query)

            # 清理已消失的持倉
            with self.position_lock:
                keys_to_zero_out = previous_handler_position_keys - received_position_keys_in_current_query
                for vt_symbol_key, direction_key in keys_to_zero_out:
                    self.write_log(f"Handler {self.gateway_name}: Zeroing out position for {vt_symbol_key}, Direction {direction_key.value}")
                    
                    old_pos_data = self.positions.pop((vt_symbol_key, direction_key), None) 
                    
                    if old_pos_data: 
                        zeroed_pos = PositionData(
                            gateway_name=self.gateway_name,
                            symbol=old_pos_data.symbol,       
                            exchange=old_pos_data.exchange,   
                            direction=direction_key, 
                            volume=0, 
                            yd_volume=0, 
                            frozen=0,
                            price=old_pos_data.price, 
                            pnl=0.0,                  
                            #accountid=self.vnpy_account_id 
                        )
                        self.on_position(zeroed_pos)
                    else:
                        self.write_log(f"Handler {self.gateway_name}: Tried to zero out {vt_symbol_key} {direction_key.value}, but it was already gone from cache.", level="debug")

        except Exception as e:
            self.write_log(f"Handler {self.gateway_name}: Error during query_position main logic: {e}\n{traceback.format_exc()}", level="error")

    def _on_order_deal_shioaji(self, state: SjOrderState, message: dict) -> None:
        """
        (Callback from Shioaji SDK)
        Puts raw order/deal updates onto the janus_queue for asynchronous processing.
        """
        try:
            event_tuple = ('process_order_deal', state, message)
            if self.loop.is_running():
                self.loop.call_soon_threadsafe(self.janus_queue.sync_q.put_nowait, event_tuple)
            else:
                self.write_log(
                    f"Handler {self.gateway_name} asyncio loop not running. Cannot queue order/deal update.",
                    level="warning"
                )
        except Exception as e:
            self.write_log(
                f"Handler {self.gateway_name}: Error putting Shioaji order/deal update to janus_queue: {e}",
                level="error"
            )

    def _process_single_order_deal_event(self, state: SjOrderState, message: dict) -> None:
        thread_name = threading.current_thread().name 
        seqno: Optional[str] = None 

        try:
            # 1. 提取 Shioaji 訂單序號 (seqno)
            if state in [SjOrderState.StockDeal, SjOrderState.FuturesDeal]:
                seqno = message.get("seqno")
                if not seqno and "order" in message and isinstance(message["order"], dict):
                    seqno = message["order"].get("seqno")
            elif "order" in message and isinstance(message["order"], dict):
                seqno = message["order"].get("seqno")
            elif "status" in message and isinstance(message["status"], dict):
                seqno = message["status"].get("id") or message["status"].get("seqno")

            if not seqno:
                self.write_log(f"[{thread_name}] (Executor) 無法解析SeqNo，跳過。State='{state.value}', Msg='{str(message)[:100]}'", level="warning")
                return

            vt_orderid = f"{self.gateway_name}.{seqno}"
            
            cached_order: Optional[OrderData] = None
            with self.order_map_lock:
                cached_order = self.orders.get(vt_orderid)

            if not cached_order:
                self.write_log(f"[{thread_name}] (Executor) 找不到OrderData快取: {vt_orderid}", level="warning")
                return
            
            # 2. 解析 Shioaji message，確定 VnPy 訂單狀態等
            final_vn_status: Status = cached_order.status
            # final_traded_qty 將在處理成交時累加，此處先取快取值
            final_reference_msg: str = cached_order.reference
            final_order_datetime: datetime = cached_order.datetime

            shioaji_status_block = message.get("status", message.get("order", {}))
            if not isinstance(shioaji_status_block, dict): shioaji_status_block = {}

            shioaji_native_status_str = shioaji_status_block.get("status")
            shioaji_msg_from_status = shioaji_status_block.get("msg", "")
            shioaji_order_time_obj = shioaji_status_block.get("order_datetime")

            if shioaji_msg_from_status: final_reference_msg = shioaji_msg_from_status
            if isinstance(shioaji_order_time_obj, datetime):
                final_order_datetime = shioaji_order_time_obj.replace(tzinfo=TAIPEI_TZ)

            if shioaji_native_status_str:
                try:
                    current_shioaji_status_enum = SjStatus(shioaji_native_status_str)
                    mapped_status = STATUS_MAP.get(current_shioaji_status_enum)
                    if mapped_status: final_vn_status = mapped_status
                except ValueError: pass # Keep previous status if unknown

            operation_block = message.get("operation", {})
            if isinstance(operation_block, dict):
                op_type, op_code, op_msg_from_op = operation_block.get("op_type"), operation_block.get("op_code"), operation_block.get("op_msg")
                if op_msg_from_op: final_reference_msg = op_msg_from_op
                if op_type == "Cancel" and op_code == "00": final_vn_status = Status.CANCELLED
                elif op_type == "New" and op_code != "00": final_vn_status = Status.REJECTED
                elif op_code and op_code != "00": 
                    self.write_log(f"[{thread_name}] (Executor) Shioaji op '{op_type}' failed for {vt_orderid}: code='{op_code}', msg='{final_reference_msg}'", level="warning")
            
            # 3. 如果是成交事件，處理 TradeData 並累計成交量
            if state in [SjOrderState.StockDeal, SjOrderState.FuturesDeal]:
                deals_list = shioaji_status_block.get("deals", [])
                if not deals_list and "price" in message and "quantity" in message:
                    deals_list = [message] 
                
                for deal_item in deals_list:
                    try:
                        deal_price_str = deal_item.get("price")
                        # **重要修正：從 deal_item["quantity"] 獲取本次成交量**
                        deal_quantity_this_fill_str = deal_item.get("quantity") 
                        
                        # **重要修正：使用 deal_item 中的 'exchange_seq' 或 'id' 作為單筆成交的唯一識別**
                        # 根據您的回覆，msg["trade_id"] 是委託的唯一ID，msg["exchange_seq"] 是分筆序號
                        # Deal Event 的 msg (即此處的 deal_item) 應包含 exchange_seq
                        shioaji_fill_id = deal_item.get("exchange_seq") or deal_item.get("id") # 優先 exchange_seq
                        if not shioaji_fill_id and message.get("trade_id") and len(deals_list) == 1:
                            # 如果 deals 列表只有一個，且 deal_item 內無唯一ID，
                            # 可考慮組合 trade_id 和一個內部計數器，但 exchange_seq 是首選
                            shioaji_fill_id = f"{message.get('trade_id')}_fill_{len(self.shioaji_deals)}" # 備用生成方式

                        deal_ts_raw = deal_item.get("ts")

                        if not all([deal_price_str, deal_quantity_this_fill_str, shioaji_fill_id, deal_ts_raw]):
                            self.write_log(f"[{thread_name}] (Executor) 成交回報不完整 (Order: {vt_orderid}): {deal_item}", level="warning")
                            continue
                        
                        deal_price = float(deal_price_str)
                        deal_quantity_this_fill = float(deal_quantity_this_fill_str) # 本次成交量

                        if deal_quantity_this_fill <= 0: continue

                        # **重要修正：成交去重的 key**
                        # 使用 (訂單的 Shioaji SeqNo, 此筆成交的 Shioaji Fill ID)
                        deal_key = (str(seqno), str(shioaji_fill_id)) 
                        with self.order_map_lock: 
                            if deal_key in self.shioaji_deals:
                                continue # 已處理過此筆成交
                            self.shioaji_deals.add(deal_key)
                            # **重要修正：累加成交量到 cached_order.traded**
                            cached_order.traded += deal_quantity_this_fill

                        trade_datetime = datetime.now(TAIPEI_TZ)
                        if isinstance(deal_ts_raw, (int, float)):
                            trade_datetime = datetime.fromtimestamp(deal_ts_raw / 1e9, tz=TAIPEI_TZ)
                        elif isinstance(deal_ts_raw, datetime):
                            trade_datetime = deal_ts_raw.replace(tzinfo=TAIPEI_TZ)
                        
                        vnpy_trade = TradeData(
                            gateway_name=self.gateway_name,
                            accountid=self.vnpy_account_id,
                            symbol=cached_order.symbol,
                            exchange=cached_order.exchange,
                            orderid=cached_order.vt_orderid,
                            # **重要修正：TradeData 的 tradeid**
                            tradeid=f"{cached_order.vt_orderid}.{shioaji_fill_id}", # 父訂單ID + 此筆成交的唯一ID
                            direction=cached_order.direction,
                            offset=cached_order.offset,
                            price=deal_price,
                            volume=deal_quantity_this_fill, # 本次成交量
                            datetime=trade_datetime
                        )
                        self.on_trade(vnpy_trade)
                        final_order_datetime = max(final_order_datetime, trade_datetime)
                    except Exception as e_deal_item_parse:
                        self.write_log(f"[{thread_name}] (Executor) 解析單筆成交數據時出錯 for order {vt_orderid}: {e_deal_item_parse}\nDeal Item: {deal_item}", level="error")

                # 在處理完成交後，根據累加的 cached_order.traded 更新訂單狀態
                if cached_order.traded >= cached_order.volume:
                    final_vn_status = Status.ALLTRADED
                elif cached_order.traded > 0:
                    final_vn_status = Status.PARTTRADED
                # else: 如果 traded 仍為0 (例如 deals_list 為空或無效)，則 final_vn_status 維持先前的判斷

            # 4. 更新 OrderData 快取並推送 (此處 final_traded_qty 應為 cached_order.traded 的最新值)
            if (final_vn_status != cached_order.status or
                abs(cached_order.traded - getattr(self.orders.get(vt_orderid), 'traded', -1.0)) > 1e-6 or # 比較更新後的 traded
                final_reference_msg != cached_order.reference or
                final_order_datetime != cached_order.datetime):

                with self.order_map_lock: # cached_order 可能已在上面被修改 (traded)，這裡再次獲取或直接使用
                    # 直接更新 cached_order (它就是 self.orders[vt_orderid] 的引用)
                    cached_order.status = final_vn_status
                    # cached_order.traded 已經在上面循環中被累加更新了
                    cached_order.reference = final_reference_msg
                    cached_order.datetime = final_order_datetime
                
                self.write_log(
                    f"[{thread_name}] (Executor) 推送訂單更新: {cached_order.vt_orderid}, "
                    f"VnPyStatus={cached_order.status.value}, TradedQty={cached_order.traded}, Ref='{final_reference_msg}'"
                )
                self.on_order(copy.copy(cached_order))
        
        except Exception as e_process_event:
            log_seqno = seqno if seqno else "UnknownSeqno"
            self.write_log(f"[{thread_name}] (Executor) _process_single_order_deal_event 處理時發生嚴重錯誤 for Shioaji SeqNo {log_seqno}: {e_process_event}\n{traceback.format_exc()}", level="error")

    async def _process_batch(self, batch: list) -> None:
        """
        (Async Method) 處理來自 janus_queue 的一個批次項目。
        將任務分派到執行緒池中執行。
        """
        tasks_for_executor: List[asyncio.Future] = [] # 用於保存 run_in_executor 返回的 Future
        loop = asyncio.get_running_loop()

        for item_tuple in batch:
            try:
                task_type = item_tuple[0]
                
                if task_type == 'process_order_deal':
                    if len(item_tuple) == 3:
                        _, state_data, message_data = item_tuple # 解包得到 state 和 message
                        tasks_for_executor.append(
                            loop.run_in_executor(
                                None, # 使用預設執行緒池
                                self._process_single_order_deal_event, # 新的處理單個訂單/成交事件的方法
                                state_data, 
                                message_data
                            )
                        )
                    else:
                        self.write_log(f"Handler {self.gateway_name}: 'process_order_deal' 任務的項目格式無效: {item_tuple}", level="warning")
                
                # 如果 janus_queue 還用於其他類型的任務，可以在此添加 elif task_type == '...':
                # elif task_type == 'stk_tick_via_janus': # 假設部分 tick 也走 janus
                #     _, exchange_data, tick_raw_data = item_tuple
                #     tasks_for_executor.append(loop.run_in_executor(None, self._process_tick_stk, exchange_data, tick_raw_data))

                else:
                    self.write_log(f"Handler {self.gateway_name}: _process_batch 收到未知的任務類型 '{task_type}'", level="warning")

            except Exception as e_dispatch:
                self.write_log(f"Handler {self.gateway_name}: 在 _process_batch 中分派任務時出錯: {item_tuple}, 錯誤: {e_dispatch}", level="error")

        if tasks_for_executor:
            try:
                # 等待此批次中的所有執行緒池任務完成
                results = await asyncio.gather(*tasks_for_executor, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        # 記錄在執行緒池中發生的錯誤
                        original_task_type = batch[i][0] if batch and len(batch) > i else "UnknownTask"
                        self.write_log(f"Handler {self.gateway_name}: 執行緒池任務 ({original_task_type}) 處理時發生錯誤: {result}", level="error")
            except Exception as e_gather:
                 self.write_log(f"Handler {self.gateway_name}: _process_batch 中 asyncio.gather 出錯: {e_gather}", level="error")
        
        # 標記 janus_queue 中的項目已完成
        for _ in batch:
            try:
                self.janus_queue.sync_q.task_done()
            except ValueError: # task_done() called too many times
                pass 
            except Exception as e_task_done: # Other potential errors like queue closed
                self.write_log(f"Handler {self.gateway_name}: 調用 task_done 時出錯: {e_task_done}", level="debug")

    def _process_single_order_deal_event(self, state: SjOrderState, message: dict) -> None:
        """
        (Runs in Executor Thread Pool)
        Processes a single Shioaji order/deal event. Parses the message,
        creates VnPy OrderData and TradeData objects, and calls respective on_order/on_trade.
        Handles partial fills by creating TradeData for each fill and accumulating traded volume.
        """
        thread_name = threading.current_thread().name
        shioaji_order_seqno: Optional[str] = None

        try:
            # 1. Extract Shioaji Order Sequence Number (seqno)
            # This seqno is the key to link Shioaji's update to our cached VnPy OrderData.
            if "order" in message and isinstance(message["order"], dict):
                shioaji_order_seqno = message["order"].get("seqno")
            
            if not shioaji_order_seqno and "status" in message and isinstance(message["status"], dict):
                shioaji_order_seqno = message["status"].get("id") # Often used for order seqno in status messages
                if not shioaji_order_seqno:
                    shioaji_order_seqno = message["status"].get("seqno") # Fallback within status

            if not shioaji_order_seqno and state in [SjOrderState.StockDeal, SjOrderState.FuturesDeal]:
                # For deal messages, 'seqno' at the root often refers to the order's seqno.
                shioaji_order_seqno = message.get("seqno")

            if not shioaji_order_seqno:
                self.write_log(
                    f"[{thread_name}] (Executor) Cannot parse Shioaji Order SeqNo. State: '{state.value}', Msg: '{str(message)[:150]}'",
                    level="warning"
                )
                return

            vt_orderid = f"{self.gateway_name}.{shioaji_order_seqno}"

            # 2. Fetch Cached VnPy OrderData
            with self.order_map_lock:
                cached_order = self.orders.get(vt_orderid)
                if not cached_order:
                    self.write_log(
                        f"[{thread_name}] (Executor) No cached OrderData found for {vt_orderid}. State: '{state.value}'. Msg: '{str(message)[:100]}'",
                        level="warning"
                    )
                    return
                
                # Work on a copy for modification, update original cache at the end if changed
                order_to_update = copy.copy(cached_order) 

            # 3. Initialize/Prepare Fields for VnPy OrderData Update
            # These will be updated based on the incoming message
            final_vn_status: Status = order_to_update.status
            cumulative_traded_qty_for_order: float = order_to_update.traded # Start with previously known traded qty
            latest_activity_datetime: datetime = order_to_update.datetime # Start with previous activity time
            final_reference_msg: str = order_to_update.reference

            # 4. Parse Common Shioaji Blocks (status and operation)
            shioaji_status_block = message.get("status", message.get("order", {}))
            if not isinstance(shioaji_status_block, dict): shioaji_status_block = {}

            shioaji_msg_from_status = shioaji_status_block.get("msg", "")
            if shioaji_msg_from_status:
                final_reference_msg = shioaji_msg_from_status
            
            # Shioaji's order_datetime for F&O is initial submission time.
            # We'll use latest_activity_datetime to track the true last update.
            # shioaji_order_initial_datetime_obj = shioaji_status_block.get("order_datetime")
            # if isinstance(shioaji_order_initial_datetime_obj, datetime):
            #     # This could be used to verify initial submission time if needed,
            #     # but order_to_update.datetime already holds the submission time (or last known activity).
            #     pass

            operation_block = message.get("operation", {})
            if isinstance(operation_block, dict):
                op_msg_from_op = operation_block.get("op_msg")
                if op_msg_from_op: # Operation message can be more specific
                    final_reference_msg = op_msg_from_op

            # 5. Determine Base VnPy Order Status from Shioaji Status/Operation codes
            shioaji_native_status_str = shioaji_status_block.get("status")
            if shioaji_native_status_str:
                try:
                    current_shioaji_status_enum = SjStatus(shioaji_native_status_str)
                    mapped_status = STATUS_MAP.get(current_shioaji_status_enum)
                    if mapped_status:
                        final_vn_status = mapped_status
                    
                    # Futures/Options Rejection Check
                    if order_to_update.exchange == Exchange.TAIFEX: # Assuming TAIFEX for F&O
                        if current_shioaji_status_enum == SjStatus.Failed:
                            final_vn_status = Status.REJECTED
                except ValueError:
                    self.write_log(f"[{thread_name}] (Executor) Unknown Shioaji status string '{shioaji_native_status_str}' for {vt_orderid}", level="warning")

            # Stock Rejection/Failure Check from Operation Block
            if order_to_update.exchange in [Exchange.TWSE, Exchange.TOTC]: # Assuming these for Stocks
                op_code = operation_block.get("op_code")
                op_type = operation_block.get("op_type")
                if op_code and op_code != "00":
                    final_vn_status = Status.REJECTED # Generalize failure/rejection for stocks
                    self.write_log(f"[{thread_name}] (Executor) Stock order {vt_orderid} operation '{op_type}' failed with op_code '{op_code}'. Ref: '{final_reference_msg}'", level="warning")
            
            # Cancellation Confirmation from Operation Block
            if isinstance(operation_block, dict) and \
            operation_block.get("op_type") == "Cancel" and \
            operation_block.get("op_code") == "00":
                final_vn_status = Status.CANCELLED
                # Try to get a timestamp for the cancellation if available in message,
                # otherwise, current time will be used for latest_activity_datetime later.

            # 6. Process Fills if this is a Deal Event
            any_new_fill_processed = False
            if state in [SjOrderState.StockDeal, SjOrderState.FuturesDeal]:
                deals_to_process = []
                if state == SjOrderState.StockDeal: # TFTDeal
                    # The message itself is the deal_item for stocks
                    deals_to_process = [message]
                elif state == SjOrderState.FuturesDeal:
                    # Deals are in a list for futures/options
                    deals_to_process = shioaji_status_block.get("deals", [])

                for deal_item in deals_to_process:
                    try:
                        deal_price_str = deal_item.get("price")
                        deal_quantity_this_fill_str = deal_item.get("quantity") # Per-fill quantity
                        
                        shioaji_fill_id: Optional[str] = None
                        deal_ts_raw: Optional[Union[int, float]] = None

                        if state == SjOrderState.StockDeal:
                            shioaji_fill_id = deal_item.get("exchange_seq") # Unique fill ID for stocks
                            deal_ts_raw = deal_item.get("ts") # Integer seconds for stocks
                        elif state == SjOrderState.FuturesDeal: # deal_item here is a Shioaji Deal object
                            shioaji_fill_id = str(deal_item.seq) if hasattr(deal_item, 'seq') else None # Unique fill ID for F&O
                            deal_ts_raw = deal_item.ts if hasattr(deal_item, 'ts') else None # Float seconds for F&O
                            # Price and quantity might also be attributes like deal_item.price, deal_item.quantity
                            if deal_price_str is None and hasattr(deal_item, 'price'): deal_price_str = str(deal_item.price)
                            if deal_quantity_this_fill_str is None and hasattr(deal_item, 'quantity'): deal_quantity_this_fill_str = str(deal_item.quantity)


                        if not all([deal_price_str, deal_quantity_this_fill_str, shioaji_fill_id, deal_ts_raw is not None]):
                            self.write_log(f"[{thread_name}] (Executor) Incomplete fill data for order {vt_orderid}. Deal: '{deal_item}'", level="warning")
                            continue

                        deal_price = float(deal_price_str)
                        deal_quantity_this_fill = float(deal_quantity_this_fill_str)

                        if deal_quantity_this_fill <= 0:
                            continue

                        trade_datetime = datetime.fromtimestamp(float(deal_ts_raw), TAIPEI_TZ) # Timestamps are in seconds
                        latest_activity_datetime = max(latest_activity_datetime, trade_datetime)

                        # Prevent duplicate TradeData processing
                        deal_key = (str(shioaji_fill_id), str(shioaji_order_seqno))
                        with self.order_map_lock: # Protecting self.shioaji_deals
                            if deal_key in self.shioaji_deals:
                                self.write_log(f"[{thread_name}] (Executor) Duplicate fill {deal_key} for order {vt_orderid}, skipping.", level="debug")
                                continue
                            self.shioaji_deals.add(deal_key)
                        
                        any_new_fill_processed = True
                        cumulative_traded_qty_for_order += deal_quantity_this_fill # Accumulate manually

                        vnpy_trade = TradeData(
                            gateway_name=self.gateway_name,
                            accountid=self.vnpy_account_id,
                            symbol=order_to_update.symbol,
                            exchange=order_to_update.exchange,
                            orderid=order_to_update.vt_orderid,
                            tradeid=f"{self.gateway_name}.{shioaji_fill_id}", # Make trade ID globally unique
                            direction=order_to_update.direction,
                            offset=order_to_update.offset,
                            price=deal_price,
                            volume=deal_quantity_this_fill, # Volume of this specific fill
                            datetime=trade_datetime
                        )
                        self.on_trade(vnpy_trade)
                    except Exception as e_deal_item:
                        self.write_log(f"[{thread_name}] (Executor) Error processing one fill for order {vt_orderid}: {e_deal_item}. Deal: '{deal_item}'\n{traceback.format_exc()}", level="error")

            # 7. Update Final Order Status based on Accumulated Fills (if not already terminal)
            if any_new_fill_processed and final_vn_status not in [Status.CANCELLED, Status.REJECTED]:
                if abs(cumulative_traded_qty_for_order - order_to_update.volume) < 1e-6: # Float comparison
                    final_vn_status = Status.ALLTRADED
                elif cumulative_traded_qty_for_order > 0:
                    final_vn_status = Status.PARTTRADED
            
            # If no new fills but status changed (e.g., from Shioaji status push like "Submitted", "PendingCancel")
            # and if latest_activity_datetime was not updated by a fill, update it to now.
            if not any_new_fill_processed and (final_vn_status != order_to_update.status or final_reference_msg != order_to_update.reference):
                latest_activity_datetime = datetime.now(TAIPEI_TZ)


            # 8. Update OrderData in Cache and Push Event if Changed
            if (final_vn_status != order_to_update.status or
                abs(cumulative_traded_qty_for_order - order_to_update.traded) > 1e-6 or # Compare float
                final_reference_msg != order_to_update.reference or
                latest_activity_datetime != order_to_update.datetime):

                order_to_update.status = final_vn_status
                order_to_update.traded = cumulative_traded_qty_for_order
                order_to_update.reference = final_reference_msg
                order_to_update.datetime = latest_activity_datetime # Reflects last activity

                with self.order_map_lock:
                    self.orders[vt_orderid] = copy.copy(order_to_update) # Update cache with the modified copy

                self.write_log(
                    f"[{thread_name}] (Executor) Pushing Order Update: {order_to_update.vt_orderid}, "
                    f"VnPyStatus={order_to_update.status.value}, TradedQty={order_to_update.traded}, Ref='{order_to_update.reference}'"
                )
                self.on_order(copy.copy(order_to_update)) # Push a new copy

            # else:
            #     self.write_log(f"[{thread_name}] (Executor) Order {vt_orderid} no significant change. Current VnPy Status: {order_to_update.status.value}, Traded: {order_to_update.traded}", level="debug")

        except Exception as e_main:
            log_seqno = shioaji_order_seqno if shioaji_order_seqno else "UnknownSeqno"
            self.write_log(f"[{thread_name}] (Executor) CRITICAL ERROR in _process_single_order_deal_event for Shioaji SeqNo {log_seqno}: {e_main}\n{traceback.format_exc()}", level="critical")


    async def _queue_consumer(self) -> None:
        """
        (Async Task) 從 janus_queue 中消費項目 (主要用於訂單/成交事件)。
        將項目聚合成小批次 (或對關鍵事件逐個處理) 並調用 _process_batch。
        """
        self.write_log(f"Handler {self.gateway_name}: JanusQueue 消費者 (_queue_consumer) 已啟動。")
        # 該旗標應在 __init__ 中初始化為 False，在 _run_async_loop 創建任務後設為 True
        # 且在 close() 中設為 False 以優雅停止
        self._janus_consumer_task_running = True 

        while self._janus_consumer_task_running:
            try:
                # 等待第一個項目，這是異步阻塞的
                first_item_data = await self.janus_queue.async_q.get()
                batch = [first_item_data]

                # 使用 self.batch_collect_timeout (從 session_setting 讀取，應設得很小)
                # 如果 self.batch_collect_timeout <= 0，則不進入此聚合循環
                if self.batch_collect_timeout > 0:
                    try:
                        # 在極短的超時內，盡可能多地獲取事件
                        while True: 
                            item_data = await asyncio.wait_for(
                                self.janus_queue.async_q.get(),
                                timeout=self.batch_collect_timeout 
                            )
                            batch.append(item_data)
                            # 可選: 如果批次達到某個針對訂單事件的小尺寸上限，也立即處理
                            # MAX_ORDER_EVENTS_IN_BATCH = getattr(self, "max_order_batch_size", 5) # 可配置
                            # if len(batch) >= MAX_ORDER_EVENTS_IN_BATCH:
                            #     break
                    except asyncio.TimeoutError:
                        # 超時是預期行為，表示此輪聚合結束
                        pass 
                    except Exception as e_get_batch:
                        self.write_log(f"Handler {self.gateway_name}: _queue_consumer 在收集批次時出錯: {e_get_batch}", level="error")
                
                if batch:
                    # self.write_log(f"Handler {self.gateway_name}: JanusQueue 消費者準備處理 {len(batch)} 個訂單/成交相關事件的批次。", level="debug")
                    await self._process_batch(batch) # 處理收集到的批次

            except asyncio.CancelledError:
                self.write_log(f"Handler {self.gateway_name}: JanusQueue 消費者收到取消信號。")
                self._janus_consumer_task_running = False # 確保迴圈終止
                break 
            except Exception as e_consumer_loop:
                self.write_log(f"Handler {self.gateway_name}: JanusQueue 消費者主迴圈發生錯誤: {e_consumer_loop}", level="error")
                if not self._janus_consumer_task_running: # 如果已標記為停止
                    break
                await asyncio.sleep(0.5) # 發生未知錯誤時，短暫休眠避免CPU空轉
        
        self.write_log(f"Handler {self.gateway_name}: JanusQueue 消費者已停止。")

# Inside ShioajiSessionHandler class:

    def find_sj_contract(self, symbol: str, vn_exchange_str: Optional[str] = None) -> Optional[SjContract]:
        """
        (Handler specific) 根據 VnPy 的 symbol 和可選的 exchange 字串查找對應的 Shioaji Contract 物件。
        如果 vn_exchange_str 為 None，則會嘗試在所有類別中查找 symbol。
        """
        # self.write_log(f"Handler {self.gateway_name}: find_sj_contract - Input symbol='{symbol}', vn_exchange_str='{vn_exchange_str}'", level="debug")

        if not self.api or not hasattr(self.api, 'Contracts') or self.api.Contracts.status != SjFetchStatus.Fetched:
            self.write_log(f"Handler {self.gateway_name}: 合約未下載完成 (status: {getattr(getattr(self.api, 'Contracts', None), 'status', 'N/A')})，無法查找合約 {symbol}。", level="warning")
            return None

        target_contract: Optional[SjContract] = None

        if vn_exchange_str:
            sj_exchange_enum = self.vn2sj.get(vn_exchange_str)
            if not sj_exchange_enum:
                self.write_log(f"Handler {self.gateway_name}: 無法將 VnPy 交易所 '{vn_exchange_str}' 映射到 Shioaji Exchange for symbol {symbol}。", level="warning")
                return None

            # 根據指定的交易所查找
            if sj_exchange_enum in [SjExchange.TSE, SjExchange.OTC, SjExchange.OES]:
                if hasattr(self.api.Contracts, 'Stocks') and hasattr(self.api.Contracts.Stocks, sj_exchange_enum.value):
                    exchange_stock_contracts = getattr(self.api.Contracts.Stocks, sj_exchange_enum.value)
                    if hasattr(exchange_stock_contracts, '_code2contract'):
                        target_contract = exchange_stock_contracts._code2contract.get(symbol)
            elif sj_exchange_enum == SjExchange.TAIFEX:
                if hasattr(self.api.Contracts, 'Futures') and hasattr(self.api.Contracts.Futures, '_code2contract'):
                    target_contract = self.api.Contracts.Futures._code2contract.get(symbol)
                if not target_contract and hasattr(self.api.Contracts, 'Options') and hasattr(self.api.Contracts.Options, '_code2contract'):
                    target_contract = self.api.Contracts.Options._code2contract.get(symbol)
            # else: Other exchanges if any
        else:
            # 未指定交易所，遍歷查找 (優先順序：期貨 -> 選擇權 -> 股票(TSE -> OTC -> OES))
            # 1. 嘗試期貨
            if hasattr(self.api.Contracts, 'Futures') and hasattr(self.api.Contracts.Futures, '_code2contract'):
                target_contract = self.api.Contracts.Futures._code2contract.get(symbol)
            
            # 2. 如果不是期貨，嘗試選擇權
            if not target_contract and hasattr(self.api.Contracts, 'Options') and hasattr(self.api.Contracts.Options, '_code2contract'):
                target_contract = self.api.Contracts.Options._code2contract.get(symbol)

            # 3. 如果也不是期貨或選擇權，嘗試股票市場
            if not target_contract and hasattr(self.api.Contracts, 'Stocks'):
                for sj_ex_enum_val in [SjExchange.TSE.value, SjExchange.OTC.value, SjExchange.OES.value]: # 預設的股票市場順序
                    if hasattr(self.api.Contracts.Stocks, sj_ex_enum_val):
                        exchange_stock_contracts = getattr(self.api.Contracts.Stocks, sj_ex_enum_val)
                        if hasattr(exchange_stock_contracts, '_code2contract'):
                            target_contract = exchange_stock_contracts._code2contract.get(symbol)
                            if target_contract:
                                break # 找到了就跳出
        
        if target_contract:
            # self.write_log(f"Handler {self.gateway_name}: 找到合約 for '{symbol}' (Exchange hint: {vn_exchange_str}): {target_contract.code}@{target_contract.exchange.value}", level="debug")
            pass
        else:
            self.write_log(f"Handler {self.gateway_name}: 未找到合約 for '{symbol}' (Exchange hint: {vn_exchange_str})", level="debug" if vn_exchange_str else "warning")
            
        return target_contract

    def subscribe(self, req: SubscribeRequest) -> None:
        """
        (Handler specific) 執行實際的 Shioaji API 行情訂閱。
        成功或失敗時，通過 on_event 通知 Manager。
        此方法現在確保即使在重連時也會嘗試 API 訂閱。
        """
        vt_symbol = req.vt_symbol
        self.write_log(f"Handler {self.gateway_name}: 嘗試 API 訂閱 {vt_symbol}")

        if not self._check_connection():
            self.write_log(f"Handler {self.gateway_name}: API 訂閱失敗 (未連接) for {vt_symbol}", level="warning")
            self.on_event(EVENT_SUBSCRIBE_FAILED, vt_symbol)
            return

        contract = self.find_sj_contract(req.symbol, req.exchange.value)
        if not contract:
            self.write_log(f"Handler {self.gateway_name}: API 訂閱失敗 (找不到合約 {req.symbol}@{req.exchange.value}) for {vt_symbol}", level="warning")
            self.on_event(EVENT_SUBSCRIBE_FAILED, vt_symbol)
            return

        # 檢查 Shioaji 的訂閱限制 (每個 session 約 190-200 個)
        # 這個檢查在嘗試實際 API 訂閱前進行是合理的
        with self.subscribed_lock:
            # 如果已在 self.subscribed 中，且不是因為重連（即 API 實例未變），則可能無需重複 API 調用
            # 但 Shioaji 的 subscribe 是冪等的，重複調用通常無害
            # 關鍵是在斷線重連後，即使 vt_symbol 在 self.subscribed 中，也要重新執行 API 調用
            # 因此，我們不再檢查 if vt_symbol in self.subscribed 然後直接返回。
            MAX_SESSION_SUBS = 190 # 或從設定讀取
            # 如果 vt_symbol 不在 self.subscribed 中，才檢查是否會超出總數限制
            if vt_symbol not in self.subscribed and len(self.subscribed) >= MAX_SESSION_SUBS:
                self.write_log(f"Handler {self.gateway_name}: API 訂閱失敗，已達此 session 的訂閱上限 ({MAX_SESSION_SUBS}) for {vt_symbol}", level="warning")
                self.on_event(EVENT_SUBSCRIBE_FAILED, vt_symbol)
                return
        try:
            # 實際調用 Shioaji API 進行訂閱
            # Shioaji 的 subscribe 通常是冪等的，重複調用已訂閱的合約不會出錯
            self.api.quote.subscribe(contract, quote_type=SjQuoteType.Tick, version=SjQuoteVersion.v1)
            self.api.quote.subscribe(contract, quote_type=SjQuoteType.BidAsk, version=SjQuoteVersion.v1)

            # 只有在 API 調用沒有引發異常時，才認為訂閱成功並更新內部狀態
            with self.subscribed_lock:
                self.subscribed.add(vt_symbol) # 更新 Handler 內部的已訂閱列表

            self.write_log(f"Handler {self.gateway_name}: 成功發送 API 訂閱請求 for {vt_symbol} (Tick & BidAsk).")
            self.on_event(EVENT_SUBSCRIBE_SUCCESS, vt_symbol) # 通知 Manager 成功

        except Exception as ex:
            self.write_log(f"Handler {self.gateway_name}: API 訂閱時發生錯誤 for {vt_symbol}: {ex}", level="error")
            # 如果 API 訂閱失敗，確保從 self.subscribed 中移除 (如果之前錯誤地加入了)
            with self.subscribed_lock:
                self.subscribed.discard(vt_symbol)
            self.on_event(EVENT_SUBSCRIBE_FAILED, vt_symbol) # 通知 Manager 失敗

    def unsubscribe(self, req: SubscribeRequest) -> None:
        """
        (Handler specific) 執行實際的 Shioaji API 取消行情訂閱。
        """
        vt_symbol = req.vt_symbol
        self.write_log(f"Handler {self.gateway_name}: 嘗試 API 取消訂閱 {vt_symbol}")

        if not self._check_connection():
            self.write_log(f"Handler {self.gateway_name}: API 取消訂閱失敗 (未連接) for {vt_symbol}", level="warning")
            # 此處不發送失敗事件，因為 Manager 是主動方
            return

        with self.subscribed_lock:
            if vt_symbol not in self.subscribed:
                self.write_log(f"Handler {self.gateway_name}: {vt_symbol} 未在此 Handler 的 API 層級訂閱列表中，無需取消。", level="info")
                return # 可能 Manager 記錄有誤，或者此 Handler 已自行取消

        contract = self.find_sj_contract(req.symbol, req.exchange.value)
        if not contract:
            self.write_log(f"Handler {self.gateway_name}: API 取消訂閱失敗 (找不到合約 {req.symbol}@{req.exchange.value}) for {vt_symbol}", level="warning")
            return

        try:
            self.api.quote.unsubscribe(contract, quote_type=SjQuoteType.Tick, version=SjQuoteVersion.v1)
            self.api.quote.unsubscribe(contract, quote_type=SjQuoteType.BidAsk, version=SjQuoteVersion.v1)

            with self.subscribed_lock:
                self.subscribed.discard(vt_symbol) # 從 Handler 內部的已訂閱列表中移除

            self.write_log(f"Handler {self.gateway_name}: 成功發送 API 取消訂閱請求 for {vt_symbol}.")
            # 可選：發送一個取消成功的事件給 Manager，但通常 Manager 主導取消，不需要回饋
            # self.on_event("eUnsubscribeSuccess", vt_symbol)

        except Exception as ex:
            self.write_log(f"Handler {self.gateway_name}: API 取消訂閱時發生錯誤 for {vt_symbol}: {ex}", level="error")
            # 即使 API 取消失敗，Manager 也已經認為此 Handler 不再負責此訂閱
            # 如果需要，可以嘗試將其重新加入 self.subscribed，但邏輯會更複雜


    def send_order(self, req: OrderRequest, **kwargs) -> str:
        """
        發送下單請求 (由單一 Session Handler 處理)。
        kwargs 用於傳遞額外的 Shioaji 特定參數如 order_lot, order_cond, custom_field。
        """
        # 0. 檢查此 Handler 是否負責此訂單的帳戶
        # <<< ADDED: Account ID check against this handler's specific vnpy_account_id >>>
        if req.accountid != self.vnpy_account_id:
            self.write_log(
                f"Order rejected. Request accountid '{req.accountid}' "
                f"does not match handler's vnpy_account_id '{self.vnpy_account_id}'.",
                level="error"
            )
            # 創建一個 REJECTED 狀態的 OrderData 並透過 manager 發送
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.ACC_MISMATCH_{datetime.now().strftime('%H%M%S_%f')}", # Handler-specific order ID
                gateway_name=self.gateway_name # Handler's specific gateway name
            )
            order.status = Status.REJECTED
            order.accountid = req.accountid # Retain original requested accountid for logging/tracing
            order.reference = "Account ID mismatch with handler"
            self.on_order(order) # This now calls self.manager_event_callback
            return order.vt_orderid

        # 1. 檢查此 Handler 的連線和 CA 狀態
        # <<< MODIFIED: Uses self._check_connection of this handler >>>
        if not self._check_connection(check_ca=True):
            self.write_log(f"Order rejected: Handler {self.gateway_name} not connected or CA not signed.", level="warning")
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.CONN_REJECT_{datetime.now().strftime('%H%M%S_%f')}",
                gateway_name=self.gateway_name
            )
            order.status = Status.REJECTED
            order.accountid = self.vnpy_account_id # This handler's account
            order.reference = "Handler not connected or CA not signed"
            self.on_order(order)
            return order.vt_orderid

        # 2. 查找 Shioaji 合約對象 (使用此 Handler 的 self.api.Contracts)
        vn_exchange_str = req.exchange.value
        symbol = req.symbol
        vt_symbol = f"{symbol}.{vn_exchange_str}"

        self.write_log(f"Preparing to call self.find_sj_contract for symbol='{symbol}', vn_exchange_str='{vn_exchange_str}'")
        sj_contract = self.find_sj_contract(symbol, vn_exchange_str) # Uses this handler's contract cache & API

        if not sj_contract:
            self.write_log(f"Order rejected: Contract not found for {vt_symbol} in handler {self.gateway_name}.", level="warning")
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.NO_CONTRACT_{datetime.now().strftime('%H%M%S_%f')}",
                gateway_name=self.gateway_name
            )
            order.status = Status.REJECTED
            order.accountid = self.vnpy_account_id
            order.reference = f"Contract not found: {vt_symbol}"
            self.on_order(order)
            return order.vt_orderid
        
        self.write_log(f"Successfully found SjContract for {vt_symbol} in handler {self.gateway_name}.")

        # 3. 確定產品類型
        product = PRODUCT_MAP_REVERSE.get(sj_contract.security_type)
        if not product:
            self.write_log(f"Order rejected: Cannot determine product type for {sj_contract.security_type} from handler {self.gateway_name}.", level="warning")
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.NO_PRODUCT_{datetime.now().strftime('%H%M%S_%f')}",
                gateway_name=self.gateway_name
            )
            order.status = Status.REJECTED
            order.accountid = self.vnpy_account_id
            order.reference = f"Unknown product type: {sj_contract.security_type}"
            self.on_order(order)
            return order.vt_orderid

        # 4. 準備 Shioaji Order 參數 (調用此 Handler 的輔助方法)
        order_args: Optional[Dict[str, Any]] = None
        if product == Product.EQUITY:
            order_args = self._prepare_stock_order_args(req, **kwargs)
        elif product in [Product.FUTURES, Product.OPTION]:
            order_args = self._prepare_futures_order_args(req, **kwargs)
        else:
            self.write_log(f"Order rejected: Unsupported product type {product.value} by handler {self.gateway_name}.", level="warning")
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.PROD_REJECT_{datetime.now().strftime('%H%M%S_%f')}",
                gateway_name=self.gateway_name
            )
            order.status = Status.REJECTED
            order.accountid = self.vnpy_account_id
            order.reference = f"Unsupported product: {product.value}"
            self.on_order(order)
            return order.vt_orderid

        if order_args is None:
            self.write_log(f"Order rejected: Failed to prepare order arguments for {vt_symbol}, Type: {req.type.value} in handler {self.gateway_name}.", level="warning")
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.PREP_ERR_{datetime.now().strftime('%H%M%S_%f')}",
                gateway_name=self.gateway_name
            )
            order.status = Status.REJECTED
            order.accountid = self.vnpy_account_id
            order.reference = "Order preparation failed"
            self.on_order(order)
            return order.vt_orderid

        # 5. 添加 Shioaji 帳戶到參數中 (使用此 Handler 的 self.api 的預設帳戶)
        # Ensure self.api.stock_account and self.api.futopt_account are set correctly during _connect_worker
        shioaji_api_account = None
        if product == Product.EQUITY:
            shioaji_api_account = self.api.stock_account
        elif product in [Product.FUTURES, Product.OPTION]:
            shioaji_api_account = self.api.futopt_account
        
        if not shioaji_api_account:
            self.write_log(f"Order rejected: No default Shioaji {product.value} account set for handler {self.gateway_name}.", level="error")
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.NO_SJ_ACC_{datetime.now().strftime('%H%M%S_%f')}",
                gateway_name=self.gateway_name
            )
            order.status = Status.REJECTED
            order.accountid = self.vnpy_account_id
            order.reference = f"No Shioaji default {product.value} account"
            self.on_order(order)
            return order.vt_orderid
        
        order_args["account"] = shioaji_api_account

        # 6. 創建 Shioaji Order 物件並實際下單
        try:
            self.write_log(f"Handler {self.gateway_name}: Preparing Shioaji.Order with args: {order_args}")
            sj_order = self.api.Order(**order_args) # Uses this handler's self.api
        except Exception as e_build:
            self.write_log(f"Order rejected: Failed to build Shioaji.Order object: {e_build}\nArgs: {order_args}", level="error")
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.BUILD_ERR_{datetime.now().strftime('%H%M%S_%f')}",
                gateway_name=self.gateway_name
            )
            order.status = Status.REJECTED
            order.accountid = self.vnpy_account_id
            order.reference = f"Build Shioaji.Order failed: {e_build}"
            self.on_order(order)
            return order.vt_orderid

        try:
            self.write_log(f"Handler {self.gateway_name}: Placing order with Shioaji API: Contract='{sj_contract.code}', Order='{sj_order}'")
            trade_resp: Optional[SjTrade] = self.api.place_order(sj_contract, sj_order) # Uses this handler's self.api
            self.write_log(f"Handler {self.gateway_name}: Shioaji place_order returned: {repr(trade_resp)}")

            if trade_resp and trade_resp.order and trade_resp.status:
                shioaji_seqno = trade_resp.order.seqno
                # <<< MODIFIED: vt_orderid now includes handler's unique gateway_name >>>
                vt_orderid = f"{self.gateway_name}.{shioaji_seqno}" 

                order_status_from_resp = STATUS_MAP.get(trade_resp.status.status, Status.SUBMITTING)
                order_datetime_from_resp = datetime.now(TAIPEI_TZ) # Default to now
                if trade_resp.status.order_datetime:
                    order_datetime_from_resp = trade_resp.status.order_datetime.replace(tzinfo=TAIPEI_TZ)

                order = OrderData(
                    gateway_name=self.gateway_name,     # <<< MODIFIED: Handler's unique name
                    accountid=self.vnpy_account_id,     # <<< ADDED: Handler's VnPy account ID
                    symbol=symbol,
                    exchange=req.exchange,
                    orderid=vt_orderid,                 # <<< MODIFIED: Handler-specific vt_orderid
                    type=req.type,
                    direction=req.direction,
                    offset=req.offset,
                    price=req.price, # For market orders, Shioaji might fill this, or use req.price if LMT
                    volume=req.volume,
                    traded=float(trade_resp.status.deal_quantity),
                    status=order_status_from_resp,
                    datetime=order_datetime_from_resp,
                    reference=trade_resp.status.msg
                )
                
                with self.order_map_lock: # Handler's specific lock
                    self.orders[vt_orderid] = order 
                    self.shioaji_trades[shioaji_seqno] = trade_resp 
                
                self.write_log(f"Order sent successfully by handler {self.gateway_name}. VnPy OrderID: {vt_orderid}, Shioaji SeqNo: {shioaji_seqno}")
                self.on_order(copy.copy(order)) # <<< MODIFIED: Sends to manager via callback >>>
                return vt_orderid
            else:
                err_msg = f"Shioaji place_order did not return valid Trade/Order/Status. Response: {repr(trade_resp)}"
                self.write_log(f"Order rejected by handler {self.gateway_name}: {err_msg}", level="error")
                order = req.create_order_data(
                    orderid=f"{self.gateway_name}.RESP_ERR_{datetime.now().strftime('%H%M%S_%f')}",
                    gateway_name=self.gateway_name
                )
                order.status = Status.REJECTED
                order.accountid = self.vnpy_account_id
                order.reference = err_msg
                self.on_order(order)
                return order.vt_orderid

        except SjAccountNotSignError as e_sign:
            self.write_log(f"Order rejected by handler {self.gateway_name}: Account not signed for CA - {e_sign}", level="error")
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.SIGN_ERR_{datetime.now().strftime('%H%M%S_%f')}",
                gateway_name=self.gateway_name
            )
            order.status = Status.REJECTED
            order.accountid = self.vnpy_account_id
            order.reference = f"Account not CA signed: {e_sign}"
            self.on_order(order)
            return order.vt_orderid
        except Exception as e_place:
            self.write_log(f"Order placement error in handler {self.gateway_name}: {e_place}\n{traceback.format_exc()}", level="error")
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.API_ERR_{datetime.now().strftime('%H%M%S_%f')}",
                gateway_name=self.gateway_name
            )
            order.status = Status.REJECTED
            order.accountid = self.vnpy_account_id
            order.reference = f"Shioaji API place_order error: {e_place}"
            self.on_order(order)
            return order.vt_orderid
        
    def _prepare_stock_order_args(self, req: OrderRequest, **kwargs) -> Optional[Dict[str, Any]]:
        """
        (Refactored) Prepares Shioaji Order parameters for stock orders
        using centralized mapping tables and helper functions.
        """
        # 1. Validate and get Shioaji price_type and order_type from VnPy OrderType
        # ORDER_TYPE_STOCK_VT2SJ is a module-level constant
        order_type_tuple = ORDER_TYPE_STOCK_VT2SJ.get(req.type)
        if not order_type_tuple:
            self.write_log(
                f"Handler {self.gateway_name}: Unsupported stock order type {req.type.value} for symbol {req.symbol}", 
                level="error"
            )
            return None
        sj_price_type, sj_order_type = order_type_tuple

        # 2. Determine Shioaji stock order condition using helper function
        # _helper_map_stock_order_cond is a module-level helper function
        sj_order_cond = _helper_map_stock_order_cond(req.offset, req.direction, kwargs, self.write_log)
        
        # 3. Determine Shioaji stock order lot (整股, 零股, etc.) using helper function
        # _helper_map_stock_order_lot is a module-level helper function
        sj_order_lot = _helper_map_stock_order_lot(kwargs.get("order_lot"), self.write_log)

        # 4. Get Shioaji action (Buy/Sell) from VnPy Direction
        # DIRECTION_MAP is a module-level constant
        sj_action = DIRECTION_MAP.get(req.direction)
        if not sj_action: # Should not happen with valid VnPy Direction enum
            self.write_log(
                f"Handler {self.gateway_name}: Invalid order direction {req.direction.value} for symbol {req.symbol}", 
                level="error"
            )
            return None

        # 5. Validate order volume (must be positive integer shares)
        order_quantity_shares: int
        if req.volume is not None and req.volume > 0:
            try:
                order_quantity_shares = int(req.volume)
            except ValueError:
                self.write_log(
                    f"Handler {self.gateway_name}: Stock order volume '{req.volume}' is not a valid integer for symbol {req.symbol}", 
                    level="error"
                )
                return None
        else:
            self.write_log(
                f"Handler {self.gateway_name}: Stock order volume must be greater than 0 (received: {req.volume}) for symbol {req.symbol}", 
                level="error"
            )
            return None
            
        # 6. Determine order price (Shioaji MKT price is 0.0)
        order_price: float = 0.0
        if sj_price_type == SjStockPriceType.LMT: # Limit orders require a price
            if req.price is None or req.price <= 0:
                # FAK/FOK with LMT price type also fall here if price is invalid.
                # Their specific price validation was already in ORDER_TYPE_STOCK_VT2SJ handling or should be.
                self.write_log(
                    f"Handler {self.gateway_name}: Stock LMT order requires a valid positive price (received: {req.price}) for symbol {req.symbol}", 
                    level="error"
                )
                return None
            order_price = req.price

        # 7. Assemble core Shioaji order arguments
        args: Dict[str, Any] = {
            "price": order_price,
            "quantity": order_quantity_shares, # Shioaji stock quantity is in shares
            "action": sj_action,
            "price_type": sj_price_type,
            "order_type": sj_order_type,
            "order_cond": sj_order_cond,
            "order_lot": sj_order_lot,
        }
        
        # 8. Handle 'daytrade_short' flag (for cash short selling on the same day)
        # Based on your feedback, this is for SjStockOrderCond.Cash + SHORT + CLOSETODAY
        # Our _helper_map_stock_order_cond maps (Offset.CLOSETODAY, Direction.SHORT) to SjStockOrderCond.Cash
        if req.offset == Offset.CLOSETODAY and req.direction == Direction.SHORT and sj_order_cond == SjStockOrderCond.Cash:
            args["daytrade_short"] = True
            # self.write_log(f"Handler {self.gateway_name}: Setting stock daytrade_short=True for {req.symbol}", level="info")
        
        # 9. Handle optional 'custom_field'
        custom_field = kwargs.get("custom_field")
        if isinstance(custom_field, str):
            if len(custom_field) <= 6: # Shioaji limit
                args["custom_field"] = custom_field
            else:
                self.write_log(
                    f"Handler {self.gateway_name}: Stock order custom_field '{custom_field}' for {req.symbol} exceeds 6 chars, ignored.", 
                    level="warning"
                )
        elif custom_field is not None:
             self.write_log(
                f"Handler {self.gateway_name}: Stock order custom_field for {req.symbol} is not a string, ignored.", 
                level="warning"
            )

        # 10. Final integrity check for None values in critical arguments
        # (action, price_type, order_type, order_cond, order_lot are enums and should not be None if mapped)
        # Primarily, 'action' from DIRECTION_MAP is the one that could be None if req.direction was weird.
        if args.get("action") is None: # sj_action would be None
             self.write_log(f"Handler {self.gateway_name}: Critical error - 'action' is None after mapping for stock order {req.symbol}.", level="error")
             return None

        self.write_log(f"Handler {self.gateway_name}: Prepared stock order args for {req.symbol}: {args}", level="info")
        return args

    def _prepare_futures_order_args(self, req: OrderRequest, **kwargs) -> Optional[Dict[str, Any]]:
        """
        (Refactored) Prepares Shioaji Order parameters for futures/options orders
        using centralized mapping tables.
        """
        # 1. Validate and get Shioaji price_type and order_type from VnPy OrderType
        # ORDER_TYPE_FUTURES_VT2SJ is a module-level constant
        order_type_tuple = ORDER_TYPE_FUTURES_VT2SJ.get(req.type)
        if not order_type_tuple:
            self.write_log(
                f"Handler {self.gateway_name}: Unsupported futures/options order type {req.type.value} for symbol {req.symbol}", 
                level="error"
            )
            return None
        sj_price_type, sj_order_type = order_type_tuple

        # 2. Determine Shioaji futures open/close type (octype)
        # FUTURES_OFFSET_MAP is a module-level constant
        sj_octype = FUTURES_OFFSET_MAP.get(req.offset)
        if not sj_octype:
            # If VnPy Offset is not directly mapped (e.g., Offset.NONE), default to Auto
            sj_octype = SjFuturesOCType.Auto 
            self.write_log(
                f"Handler {self.gateway_name}: Futures/options order Offset '{req.offset.value}' for {req.symbol} not directly mapped, using SjFuturesOCType.Auto.", 
                level="info"
            )
            
        # 3. Get Shioaji action (Buy/Sell) from VnPy Direction
        # DIRECTION_MAP is a module-level constant
        sj_action = DIRECTION_MAP.get(req.direction)
        if not sj_action:
            self.write_log(
                f"Handler {self.gateway_name}: Invalid order direction {req.direction.value} for symbol {req.symbol}", 
                level="error"
            )
            return None

        # 4. Validate order volume (must be positive integer lots/contracts)
        order_quantity_lots: int
        if req.volume is not None and req.volume > 0:
            try:
                order_quantity_lots = int(req.volume)
            except ValueError:
                self.write_log(
                    f"Handler {self.gateway_name}: Futures/options order volume '{req.volume}' is not a valid integer for symbol {req.symbol}", 
                    level="error"
                )
                return None
        else:
            self.write_log(
                f"Handler {self.gateway_name}: Futures/options order volume must be greater than 0 (received: {req.volume}) for symbol {req.symbol}", 
                level="error"
            )
            return None

        # 5. Determine order price (Shioaji MKP/MKT price is 0.0)
        order_price: float = 0.0
        if sj_price_type == SjFuturesPriceType.LMT: # Limit orders require a price
            if req.price is None: # For futures, LMT price of 0 might be valid for certain strategies/exchanges
                self.write_log(
                    f"Handler {self.gateway_name}: Futures/options LMT order for {req.symbol} has price None. Shioaji requires a numeric price (0 is allowed for LMT). Defaulting to 0.0.",
                    level="warning"
                )
                order_price = 0.0 # Default to 0 if None, as Shioaji expects float.
            else:
                try:
                    order_price = float(req.price)
                except ValueError:
                    self.write_log(
                        f"Handler {self.gateway_name}: Futures/options LMT order price '{req.price}' for {req.symbol} is not a valid float.",
                        level="error"
                    )
                    return None
        
        # 6. Assemble core Shioaji order arguments
        args: Dict[str, Any] = {
            "price": order_price,
            "quantity": order_quantity_lots, # Shioaji futures quantity is in lots/contracts
            "action": sj_action,
            "price_type": sj_price_type,
            "order_type": sj_order_type,
            "octype": sj_octype,
        }

        # 7. Handle optional 'custom_field'
        custom_field = kwargs.get("custom_field")
        if isinstance(custom_field, str):
            if len(custom_field) <= 6: # Shioaji limit
                args["custom_field"] = custom_field
            else:
                self.write_log(
                    f"Handler {self.gateway_name}: Futures/options order custom_field '{custom_field}' for {req.symbol} exceeds 6 chars, ignored.", 
                    level="warning"
                )
        elif custom_field is not None:
             self.write_log(
                f"Handler {self.gateway_name}: Futures/options order custom_field for {req.symbol} is not a string, ignored.", 
                level="warning"
            )
        
        # 8. Final integrity check for None values (action, price_type, order_type, octype)
        # These should have been caught by .get() returning None or mapped correctly.
        # This is a safeguard.
        if any(args.get(k) is None for k in ["action", "price_type", "order_type", "octype"]):
            missing_fields = [k for k, v in args.items() if v is None and k in ["action", "price_type", "order_type", "octype"]]
            self.write_log(
                f"Handler {self.gateway_name}: Critical error - essential fields {missing_fields} are None after mapping for futures/options order {req.symbol}. Args: {args}", 
                level="error"
            )
            return None

        self.write_log(f"Handler {self.gateway_name}: Prepared futures/options order args for {req.symbol}: {args}", level="info")
        return args


    def _on_order_deal_shioaji(self, state: SjOrderState, message: dict) -> None:
        """
        處理來自 Shioaji 的訂單狀態和成交回報。
        - state: Shioaji 的 OrderState 枚舉，指示回報類型 (e.g., Order, StockDeal, FuturesDeal, Status)。
        - message: 包含詳細回報內容的字典。
        """
        thread_name = threading.current_thread().name # 用於日誌，了解回調在哪個執行緒

        # 1. 從 message 中提取 Shioaji 訂單序號 (seqno)
        seqno: Optional[str] = None
        if state in [SjOrderState.StockDeal, SjOrderState.FuturesDeal]: # 成交回報
            # Shioaji 的成交回報中，'seqno' 通常指訂單序號，'trade_id' 或 'id' 可能指成交編號
            seqno = message.get("seqno") 
            if not seqno and "order" in message and isinstance(message["order"], dict): # 有時成交回報會包在 order 結構裡
                seqno = message["order"].get("seqno")
            # 如果還是沒有，嘗試 trade_id 作為備用 (雖然 trade_id 更像是成交自身的ID)
            # if not seqno:
            #     seqno = message.get("trade_id") # 這通常是 deal id，不是 order seqno
        
        elif "order" in message and isinstance(message["order"], dict): # 訂單狀態更新 (非成交)
            seqno = message["order"].get("seqno")
        
        elif "status" in message and isinstance(message["status"], dict): # 另一種可能的訂單狀態更新結構
            seqno = message["status"].get("id") # Shioaji status block might use 'id' for order seqno
            if not seqno: # Backup, if 'id' isn't seqno, try 'seqno' if present
                seqno = message["status"].get("seqno")

        if not seqno:
            self.write_log(
                f"無法從 Shioaji 回調中解析訂單序號 (SeqNo)。State='{state.value}', Msg='{message}'",
                level="warning"
            )
            return

        # 2. 產生 Handler 內部唯一的 vt_orderid，並從快取中獲取 OrderData
        # self.gateway_name 此時是 Handler 的唯一名稱, e.g., "SHIOAJI_MULTI.ACCOUNT1"
        vt_orderid = f"{self.gateway_name}.{seqno}"
        
        self.write_log(f"處理 Shioaji 訂單回調: {vt_orderid}, State='{state.value}'", level="debug")
        # self.write_log(f"Raw Message: {message}", level="debug") # For very detailed debugging

        cached_order: Optional[OrderData] = None
        original_sj_trade_obj: Optional[SjTrade] = None # Shioaji 的 Trade 物件 (代表一筆委託)

        with self.order_map_lock: # 保護對 self.orders 和 self.shioaji_trades 的訪問
            cached_order = self.orders.get(vt_orderid)
            original_sj_trade_obj = self.shioaji_trades.get(seqno) # Keyed by Shioaji seqno

        if not cached_order:
            self.write_log(
                f"找不到對應的 OrderData 快取: {vt_orderid}。可能為早於此連線會話的訂單回報，或 Manager 未正確路由。",
                level="warning"
            )
            # 如果希望處理這種情況 (例如，為盤初收到的未知單建立新的 OrderData)，需要額外邏輯。
            # 目前，如果本地沒有快取，則忽略。
            return

        # 確保 cached_order 的 accountid 是此 Handler 的 vnpy_account_id
        # 這在 send_order 時就應該設定好，此處為防禦性檢查或記錄
        if cached_order.accountid != self.vnpy_account_id:
            self.write_log(
                f"OrderData {vt_orderid} 的 accountid '{cached_order.accountid}' "
                f"與 Handler 的 vnpy_account_id '{self.vnpy_account_id}' 不符。",
                level="error"
            )
            # 根據策略決定是否繼續處理或報錯

        # 3. (可選) 調用 self.api.update_status() 獲取最新狀態
        # 這個操作是同步阻塞的，可能會影響效能，尤其是在高頻回調時。
        # 建議優先依賴 Shioaji 主動推送的 `message`。
        # 如果要使用，需要確保 `original_sj_trade_obj.order.account` 是有效的 Shioaji Account 物件。
        # status_updated_via_api = False
        # if original_sj_trade_obj and original_sj_trade_obj.order and original_sj_trade_obj.order.account:
        #     try:
        #         # self.api.update_status(account=original_sj_trade_obj.order.account)
        #         # status_updated_via_api = True
        #         # self.write_log(f"Updated status via API for order {vt_orderid}, new SjStatus: {original_sj_trade_obj.status.status}", level="debug")
        #     except Exception as e_upd:
        #         self.write_log(f"Error calling api.update_status for order {vt_orderid}: {e_upd}", level="warning")

        # 4. 解析回報訊息，確定 VnPy 訂單狀態、已成交數量、參考訊息和時間
        final_vn_status: Status = cached_order.status # 預設為快取中的狀態，避免不必要的更新
        final_traded_qty: float = cached_order.traded
        final_reference_msg: str = cached_order.reference
        final_order_datetime: datetime = cached_order.datetime

        # 提取 Shioaji 的狀態資訊 (通常在 'status' 或 'order' 字典中)
        shioaji_status_block = message.get("status", message.get("order", {})) # Common patterns
        if not isinstance(shioaji_status_block, dict): # Ensure it's a dict
            shioaji_status_block = {}

        shioaji_native_status_str = shioaji_status_block.get("status") # e.g., "Filled", "Cancelled"
        shioaji_msg = shioaji_status_block.get("msg", "")
        shioaji_deal_qty_str = shioaji_status_block.get("deal_quantity")
        shioaji_order_time_obj = shioaji_status_block.get("order_datetime") # This is often order submission time
        
        # 更新參考訊息
        if shioaji_msg:
            final_reference_msg = shioaji_msg

        # 更新訂單時間 (如果回報中有更新的時間)
        if isinstance(shioaji_order_time_obj, datetime):
            final_order_datetime = shioaji_order_time_obj.replace(tzinfo=TAIPEI_TZ)
        
        # 轉換 Shioaji 狀態為 VnPy 狀態
        current_shioaji_status_enum: Optional[SjStatus] = None
        if shioaji_native_status_str:
            try:
                current_shioaji_status_enum = SjStatus(shioaji_native_status_str) # Try to convert string to SjStatus enum
                mapped_status = STATUS_MAP.get(current_shioaji_status_enum)
                if mapped_status:
                    final_vn_status = mapped_status
                else:
                    self.write_log(f"未映射的 Shioaji 狀態 '{shioaji_native_status_str}' (Enum: {current_shioaji_status_enum}) for order {vt_orderid}.", level="warning")
            except ValueError: # If string is not a valid SjStatus member
                self.write_log(f"無法識別的 Shioaji 狀態字串 '{shioaji_native_status_str}' for order {vt_orderid}.", level="warning")
        
        # 更新已成交數量 (如果回報中有)
        if shioaji_deal_qty_str is not None:
            try:
                final_traded_qty = float(shioaji_deal_qty_str)
            except ValueError:
                self.write_log(f"無法轉換 Shioaji deal_quantity '{shioaji_deal_qty_str}' 為 float for order {vt_orderid}.", level="warning")

        # 特別處理操作型回報 (如撤單成功/失敗)
        operation_block = message.get("operation", {})
        if isinstance(operation_block, dict):
            op_type = operation_block.get("op_type")
            op_code = operation_block.get("op_code")
            op_msg = operation_block.get("op_msg")

            if op_msg: # Operation message often more relevant
                final_reference_msg = op_msg
            
            if op_type == "Cancel" and op_code == "00":
                final_vn_status = Status.CANCELLED
            elif op_code and op_code != "00": # Any operation that failed
                # If it was a cancel op that failed, status might not change to REJECTED,
                # it just means the cancel op itself failed. The order might still be active or filled.
                # However, if it's a New order failing, then REJECTED is appropriate.
                if op_type == "New":
                    final_vn_status = Status.REJECTED
                self.write_log(f"Shioaji operation '{op_type}' failed with code '{op_code}', msg: '{op_msg}' for order {vt_orderid}.", level="warning")


        # 如果是成交事件 (StockDeal, FuturesDeal), 需要特別處理成交量和狀態
        if state in [SjOrderState.StockDeal, SjOrderState.FuturesDeal]:
            # 'message' for a deal usually contains info for *that specific deal*.
            # The cumulative traded quantity needs to be tracked.
            # Shioaji's 'deal_quantity' in the 'status' block of a deal message might be cumulative.
            # If not, we need to sum up individual fills from the 'deals' list if present.
            
            deals_list = shioaji_status_block.get("deals", []) # 'deals' is usually a list in deal messages
            if not deals_list and "price" in message and "quantity" in message: # Single deal structure
                 deals_list = [message] 
            
            is_new_fill_processed = False
            for deal_item in deals_list:
                deal_price_str = deal_item.get("price")
                deal_quantity_str = deal_item.get("quantity")
                # Deal ID: prefer 'exchange_seq' or 'id' from deal_item, fallback to 'trade_id' from main message
                shioaji_deal_id = deal_item.get("id") or deal_item.get("exchange_seq") or message.get("trade_id")
                deal_ts_raw = deal_item.get("ts") # Timestamp of this specific deal

                if deal_price_str is None or deal_quantity_str is None or shioaji_deal_id is None or deal_ts_raw is None:
                    self.write_log(f"成交回報 (Order: {vt_orderid}) 缺少必要欄位 (price/quantity/id/ts): {deal_item}", level="warning")
                    continue
                
                try:
                    deal_price = float(deal_price_str)
                    deal_quantity = float(deal_quantity_str)
                except ValueError:
                    self.write_log(f"無法轉換成交價格/數量為 float (Order: {vt_orderid}): P='{deal_price_str}', Q='{deal_quantity_str}'", level="warning")
                    continue

                if deal_quantity <= 0: # Skip zero or negative quantity fills
                    continue

                # ---- Prevent duplicate trade processing ----
                # Key for shioaji_deals set: (shioaji_deal_id, shioaji_order_seqno)
                deal_key = (str(shioaji_deal_id), str(seqno))
                if deal_key in self.shioaji_deals:
                    self.write_log(f"重複的成交回報 (Deal ID: {shioaji_deal_id}, Order: {vt_orderid})，已忽略。", level="debug")
                    continue
                self.shioaji_deals.add(deal_key)
                # ---- End duplicate prevention ----

                is_new_fill_processed = True

                # Update cumulative traded quantity for the order
                # Note: `final_traded_qty` might have already been updated from `shioaji_status_block.deal_quantity`.
                # If `shioaji_status_block.deal_quantity` is reliably cumulative, this manual sum isn't needed.
                # Assuming for now it might not be, or we want to be sure:
                # current_order_total_traded = cached_order.traded + deal_quantity # This is wrong if multiple deals in one message
                                                                              # and cached_order.traded isn't updated yet.
                # Better: Use the `final_traded_qty` that was parsed from the status block, which SHOULD be cumulative.
                # If the status block's deal_quantity is not cumulative, then:
                # final_traded_qty = cached_order.traded + deal_quantity # if only one fill per message
                # This part requires understanding if shioaji_status_block.deal_quantity in a DEAL message is cumulative or for that deal.
                # Let's assume it IS cumulative as per modern API designs. So final_traded_qty is already set.

                # Create TradeData object for this specific fill
                trade_datetime: datetime
                if isinstance(deal_ts_raw, (int, float)): # Timestamp in nanoseconds or similar
                    trade_datetime = datetime.fromtimestamp(deal_ts_raw / 1e9, tz=TAIPEI_TZ)
                elif isinstance(deal_ts_raw, datetime): # Already a datetime object
                    trade_datetime = deal_ts_raw.replace(tzinfo=TAIPEI_TZ)
                else:
                    self.write_log(f"未知的成交時間戳格式 (Order: {vt_orderid}, Deal ID: {shioaji_deal_id}): {deal_ts_raw}", level="warning")
                    trade_datetime = datetime.now(TAIPEI_TZ) # Fallback

                vnpy_trade = TradeData(
                    gateway_name=self.gateway_name,       # Handler's unique name
                    accountid=self.vnpy_account_id,       # Handler's VnPy account ID
                    symbol=cached_order.symbol,
                    exchange=cached_order.exchange,
                    orderid=cached_order.vt_orderid,      # Parent order's vt_orderid
                    tradeid=f"{self.gateway_name}_{shioaji_deal_id}", # Ensure unique trade ID
                    direction=cached_order.direction,     # Assume fill direction matches order
                    offset=cached_order.offset,           # Assume fill offset matches order
                    price=deal_price,
                    volume=deal_quantity,
                    datetime=trade_datetime
                )
                self.on_trade(vnpy_trade) # Send TradeData to manager
                final_order_datetime = max(final_order_datetime, trade_datetime) # Order's last update time is at least this trade's time

            # After processing all deals in the message, update order status based on cumulative traded quantity
            if is_new_fill_processed: # Only if new fills were actually processed from this message
                if final_traded_qty >= cached_order.volume:
                    final_vn_status = Status.ALLTRADED
                elif final_traded_qty > 0: # Must be > 0 if new fills
                    final_vn_status = Status.PARTTRADED
                # If final_traded_qty is 0 after processing "deals", something is wrong or no actual fill.

        # 5. 更新 OrderData 快取並透過 Manager 發送更新
        # Only push update if essential fields changed
        if (final_vn_status != cached_order.status or
            abs(final_traded_qty - cached_order.traded) > 1e-6 or # Compare floats carefully
            final_reference_msg != cached_order.reference or
            final_order_datetime != cached_order.datetime):

            with self.order_map_lock: # Ensure atomic update of the cached order object
                cached_order.status = final_vn_status
                cached_order.traded = final_traded_qty
                cached_order.reference = final_reference_msg
                cached_order.datetime = final_order_datetime
            
            self.write_log(
                f"推送訂單更新: {cached_order.vt_orderid}, VnPyStatus={cached_order.status}, "
                f"TradedQty={cached_order.traded}, Ref='{cached_order.reference}'"
            )
            self.on_order(copy.copy(cached_order)) # Send a copy to manager
        
        elif state in [SjOrderState.Order, SjOrderState.Status]: # If it was just a status push without apparent change
            self.write_log(f"收到 Shioaji 訂單狀態 '{shioaji_native_status_str}', VnPy 狀態無變化 ({final_vn_status}) for {vt_orderid}", level="debug")


# Inside ShioajiSessionHandler class:

    def _contracts_cb(self, security_type: SjSecurityType) -> None:
        """合約下載進度回調。"""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        current_status = getattr(getattr(self.api, 'Contracts', None), 'status', 'N/A')
        self.write_log(
            f"[{timestamp}] 合約下載進度 (Handler: {self.gateway_name}): "
            f"{security_type.value} 完成。 當前總狀態: {current_status}", 
            level="debug" # 使用 debug 級別避免過多常規日誌
        )

        with self._processing_contracts_lock: # 保護對 fetched_security_types 的訪問
            self.fetched_security_types.add(security_type)

            # 檢查是否所有預期的類型都已下載完成
            if not self._contracts_processed_flag and \
               self.fetched_security_types.issuperset(self.expected_security_types):
                self.write_log(
                    f"Handler {self.gateway_name}: 所有預期合約類型 ({[s.value for s in self.expected_security_types]}) 的回調均已收到。"
                )
                self._all_contracts_fetched_event.set() # 通知 _connect_worker 可能可以處理合約了
                # 注意：此時 self.api.Contracts.status 可能還未變為 Fetched，但我們收到了所有類別的回調
    # --- Helper methods for _process_contracts ---
    # <<< MOVED _find_mxf_contract from inside _process_contracts to be a class method >>>
    def _find_mxf_contract(self) -> Tuple[Optional[str], Optional[date]]:
        """
        (Handler specific) 優先回傳 MXFR1；否則回傳最近未到期 MXFyyyyMM
        Uses self.api of this handler.
        """
        if not self.api or not hasattr(self.api, 'Contracts') or \
           not hasattr(self.api.Contracts, 'Futures') or \
           not hasattr(self.api.Contracts.Futures, 'MXF'):
            self.write_log("警告：_find_mxf_contract: 找不到 Contracts.Futures.MXF，無法查找標的。", level="warning")
            return None, None

        mxf_cat = self.api.Contracts.Futures.MXF
        cont = getattr(mxf_cat, "MXFR1", None)
        if cont and hasattr(cont, 'symbol') and hasattr(cont, 'delivery_date'):
            try:
                if isinstance(cont.delivery_date, str):
                    return cont.symbol, datetime.strptime(cont.delivery_date, "%Y/%m/%d").date()
                else:
                    self.write_log(f"警告：_find_mxf_contract: MXFR1 的 delivery_date 不是字串 ({type(cont.delivery_date)})，跳過。", level="warning")
            except (ValueError, TypeError) as e:
                self.write_log(f"警告：_find_mxf_contract: 解析 MXFR1 日期 '{cont.delivery_date}' 失敗: {e}", level="warning")

        today = date.today()
        best: Optional[Tuple[str, date]] = None
        if hasattr(mxf_cat, '__iter__'): # Ensure mxf_cat is iterable
            for fut in mxf_cat:
                if (hasattr(fut, 'symbol') and fut.symbol and fut.symbol.startswith("MXF") and
                    hasattr(fut, 'delivery_date') and isinstance(fut.delivery_date, str)):
                    try:
                        exp = datetime.strptime(fut.delivery_date, "%Y/%m/%d").date()
                        if exp >= today and (best is None or exp < best[1]):
                            best = (fut.symbol, exp)
                    except (ValueError, TypeError) as e:
                        self.write_log(f"警告：_find_mxf_contract: 解析月合約 {getattr(fut, 'symbol', 'N/A')} 日期 '{fut.delivery_date}' 失敗: {e}", level="warning")
        
        if best:
            return best
        else:
            self.write_log("警告：_find_mxf_contract: 未能找到 MXFR1 或任何未到期的 MXF 月合約。", level="warning")
            return None, None


    # <<< MOVED _size_pricetick_generic from inside _process_contracts to be a class method >>>
    def _size_pricetick_generic(self, sjc: SjContract) -> Tuple[float, float]:
        """
        (Handler specific) 安全讀取 multiplier / tick_size；若缺失則給預設。
        """
        size = 1.0 
        tick = 0.01 
        code_for_log = getattr(sjc, 'code', 'N/A_CODE')

        try:
            if hasattr(sjc, "multiplier") and sjc.multiplier is not None:
                size = float(sjc.multiplier)
            if size == 0: # Multiplier of 0 is usually incorrect, default to 1
                
                #self.write_log(f"提示 ({code_for_log}): Multiplier 為 0，已校正為 1.0", level="debug")
                size = 1.0
        except (ValueError, TypeError) as e:
            self.write_log(f"警告 ({code_for_log}): 無法轉換 multiplier '{getattr(sjc, 'multiplier', 'N/A')}' 為 float: {e}. 使用預設值 {size}", level="warning")

        try:
            if hasattr(sjc, "tick_size") and sjc.tick_size is not None:
                tick = float(sjc.tick_size)
            if tick <= 0: # Price tick should be positive
                #self.write_log(f"提示 ({code_for_log}): Tick size 為非正數 ({tick})，已校正為 0.01", level="debug")
                tick = 0.01
        except (ValueError, TypeError) as e:
            self.write_log(f"警告 ({code_for_log}): 無法轉換 tick_size '{getattr(sjc, 'tick_size', 'N/A')}' 為 float: {e}. 使用預設值 {tick}", level="warning")
        
        return size, tick

    # <<< MOVED _parse_option from inside _process_contracts to be a class method >>>
    # Note: _parse_date and is_third_wednesday are helper functions.
    # _parse_date was already a method in your original code.
    # is_third_wednesday can remain a module-level function or become a static method.
    def _parse_option(self, sjc: sjOption) -> Optional[ContractData]: # sjc is Shioaji Option object
        """
        (Handler specific) 解析 Shioaji 期權合約為 VnPy ContractData.
        Uses self.gateway_name and self.sj2vnpy.
        """
        try:
            required_attrs = ['exchange', 'code', 'name', 'strike_price', 'option_right', 'delivery_date']
            if not all(hasattr(sjc, attr) and getattr(sjc, attr) is not None for attr in required_attrs):
                self.write_log(f"警告 ({getattr(sjc, 'code', 'N/A_CODE')}): 期權合約缺少必要屬性或屬性為 None，跳過。", level="warning")
                return None

            vn_exchange = self.sj2vnpy.get(sjc.exchange)
            if not vn_exchange:
                self.write_log(f"警告 ({sjc.code}): 無法映射 Shioaji 交易所 '{sjc.exchange}' 到 VnPy Exchange，跳過此期權合約。", level="warning")
                return None

            option_size, option_pricetick = self._size_pricetick_generic(sjc) # Use generic helper
            # For many options like TXO, size=50, pricetick=0.1 might be fixed by exchange spec
            # but Shioaji might provide them in contract details. If not, apply defaults.
            if sjc.code.startswith("TXO"): # Example override for TXO if Shioaji data is sparse
                option_size = 50.0
                option_pricetick = 0.1 # This depends on strike price ranges for TXO. Be careful with fixed values.

            cd = ContractData(
                gateway_name=self.gateway_name, # <<< MODIFIED: Use handler's specific gateway_name
                symbol=sjc.code,
                exchange=vn_exchange,
                name=sjc.name,
                product=Product.OPTION,
                size=option_size,
                pricetick=option_pricetick,
                min_volume=1, # Assuming min volume is 1 lot for options
                net_position=True, # Options are typically net position in VnPy
                history_data=True, # Assume history data is available
                # accountid=self.vnpy_account_id # ContractData doesn't usually have accountid
            )

            try:
                cd.option_strike = float(sjc.strike_price)
            except (ValueError, TypeError) as e:
                self.write_log(f"警告 ({sjc.code}): 無法轉換期權履約價 strike_price '{sjc.strike_price}' 為 float: {e}", level="warning")
                return None 

            option_right_val = getattr(sjc.option_right, 'value', None) # Get 'C' or 'P'
            if option_right_val == SjOptionRight.Call.value: # "C"
                cd.option_type = OptionType.CALL
            elif option_right_val == SjOptionRight.Put.value: # "P"
                cd.option_type = OptionType.PUT
            else:
                self.write_log(f"警告 ({sjc.code}): 未知的期權買賣權 option_right 值 '{option_right_val}'", level="warning")
                return None

            expiry_dt_naive: Optional[datetime] = None
            if isinstance(sjc.delivery_date, str):
                try:
                    # Shioaji delivery_date is YYYY/MM/DD
                    expiry_dt_naive = datetime.strptime(sjc.delivery_date, "%Y/%m/%d")
                except ValueError:
                    try: # Fallback for YYYYMMDD
                        expiry_dt_naive = datetime.strptime(sjc.delivery_date, "%Y%m%d")
                    except ValueError as e:
                        self.write_log(f"警告 ({sjc.code}): 無法解析期權到期日 delivery_date '{sjc.delivery_date}': {e}", level="warning")
                        return None
            elif isinstance(sjc.delivery_date, date): # If Shioaji already provides a date object
                 expiry_dt_naive = datetime.combine(sjc.delivery_date, datetime.min.time())

            if not expiry_dt_naive:
                self.write_log(f"警告 ({sjc.code}): 未能確定期權到期日 from '{sjc.delivery_date}'", level="warning")
                return None
            
            cd.option_expiry = expiry_dt_naive # Store as naive datetime for VnPy OptionMaster compatibility

            # option_portfolio, option_index, option_underlying
            # This logic is from your original code.
            category_code = getattr(sjc, 'category', '') or "TXO" # Default to TXO if category is missing
            expiry_date_only = expiry_dt_naive.date()

            # is_third_wednesday needs to be accessible (e.g. module level or static method)
            if is_third_wednesday(expiry_date_only):  # Monthly option
                ym_str = expiry_dt_naive.strftime("%Y%m")
                cd.option_portfolio = f"{category_code}{ym_str}"
                try:
                    cd.option_index = str(int(cd.option_strike)) 
                except ValueError:
                    cd.option_index = str(cd.option_strike) 
                # Default underlying for TXO monthly options
                cd.option_underlying = f"TXF{ym_str}" if category_code == "TXO" else "" 
            else: # Weekly option or other non-standard expiry
                ymd_str = expiry_dt_naive.strftime("%Y%m%d")
                # Using "W" for weekly, or just full date if not strictly weekly by 3rd Wed rule.
                # Portfolio could be like "TXOW20231227" or "TXO20231228"
                # A common convention for weekly is Category + 'W' + ExpiryWeekNumber, but YYYYMMDD is also fine.
                cd.option_portfolio = f"{category_code}W{ymd_str}" # Example for weekly style
                cd.option_index = cd.option_portfolio # Often same as portfolio for weeklies
                cd.option_underlying = ""  # To be patched later by _find_mxf_contract result

            return cd
        except Exception as e:
            self.write_log(f"解析期權合約 _parse_option (Code: {getattr(sjc, 'code', 'N/A_CODE')}) 時發生錯誤: {e}\n{traceback.format_exc()}", level="error")
            return None

    # --- Main _process_contracts method ---
    def _process_contracts(self) -> None:
        """
        (Handler specific) Processes contracts fetched by this handler's Shioaji API instance.
        Populates self.contracts and calls self.on_contract for each.
        """
        self.write_log(f"開始處理合約 for Handler: {self.gateway_name} (Acc: {self.vnpy_account_id})")

        if not self.api:
            self.write_log("錯誤：_process_contracts: self.api is None，無法處理合約。", level="error")
            return
        if not hasattr(self.api, 'Contracts') or self.api.Contracts is None: # Check if Contracts object exists
            self.write_log("錯誤：_process_contracts: self.api.Contracts 未初始化，無法處理合約。", level="error")
            return
        if self.api.Contracts.status != SjFetchStatus.Fetched:
            self.write_log(f"警告：_process_contracts: 合約狀態為 {self.api.Contracts.status} (非 Fetched)，可能合約不完整。", level="warning")
            # Decide whether to proceed or return if not Fetched. Proceeding cautiously.

        # Temporary dictionary to hold parsed ContractData objects, keyed by vt_symbol
        parsed_contracts_tmp: Dict[str, ContractData] = {}

        # 1. Process Options
        options_root = getattr(self.api.Contracts, 'Options', None)
        if options_root and hasattr(options_root, '_code2contract') and isinstance(options_root._code2contract, dict):
            self.write_log(f"處理 {len(options_root._code2contract)} 筆期權合約 for {self.gateway_name}...")
            for code, sj_opt_contract in options_root._code2contract.items():
                if isinstance(sj_opt_contract, sjOption): # Ensure it's an Option object from Shioaji
                    cd = self._parse_option(sj_opt_contract) # Call the method
                    if cd:
                        parsed_contracts_tmp[cd.vt_symbol] = cd
                else:
                    self.write_log(f"警告 ({self.gateway_name}): Options._code2contract 中發現非 SjOption 對象 (Type: {type(sj_opt_contract)}) for code {code}，已跳過。", level="warning")
        else:
            self.write_log(f"提示 ({self.gateway_name}): 未找到期權合約 (self.api.Contracts.Options._code2contract)，跳過期權處理。", level="info")

        # 2. Process Futures
        futures_root = getattr(self.api.Contracts, 'Futures', None)
        if futures_root and hasattr(futures_root, '_code2contract') and isinstance(futures_root._code2contract, dict):
            self.write_log(f"處理 {len(futures_root._code2contract)} 筆期貨合約 for {self.gateway_name}...")
            for code, sj_fut_contract in futures_root._code2contract.items():
                if not isinstance(sj_fut_contract, SjContract) or not all(hasattr(sj_fut_contract, attr) for attr in ['exchange', 'code', 'name']):
                    self.write_log(f"警告 ({self.gateway_name}): Futures._code2contract 中發現無效對象或缺少屬性 for code {getattr(sj_fut_contract, 'code', code)}，已跳過。", level="warning")
                    continue

                vn_exchange = self.sj2vnpy.get(sj_fut_contract.exchange)
                if not vn_exchange:
                    self.write_log(f"警告 ({self.gateway_name}, 期貨 {code}): 無法映射 Shioaji 交易所 '{sj_fut_contract.exchange}'，已跳過。", level="warning")
                    continue
                
                size, tick = self._size_pricetick_generic(sj_fut_contract)
                # Apply Taiwan-specific overrides for common futures
                if code: # Ensure code is not None or empty
                    if code.startswith("TXF"): size, tick = 200.0, 1.0  # 大台指
                    elif code.startswith("MXF"): size, tick = 50.0, 1.0   # 小台指
                    elif code.startswith("EXF"): size, tick = 4000.0, 0.05 # 電子期 (確認最新規格)
                    elif code.startswith("FXF"): size, tick = 1000.0, 0.2  # 金融期 (確認最新規格)
                    # Add more overrides as needed: TE, TF, GDF, XIF, etc.

                cd = ContractData(
                    gateway_name=self.gateway_name, # Handler's specific name
                    symbol=code,
                    exchange=vn_exchange,
                    name=sj_fut_contract.name,
                    product=Product.FUTURES,
                    size=size,
                    pricetick=tick,
                    min_volume=1, # Standard for futures
                    net_position=True, # Futures are typically net position
                    history_data=True,
                )
                #self.write_log(f"Handler: Processing contract - Symbol: {cd.symbol}, Exchange: {cd.exchange.value}, Product: {cd.product.value}, GW: {cd.gateway_name}") # 打印關鍵資訊
                parsed_contracts_tmp[cd.vt_symbol] = cd
                self.on_contract(cd) 

        else:
            self.write_log(f"提示 ({self.gateway_name}): 未找到期貨合約 (self.api.Contracts.Futures._code2contract)，跳過期貨處理。", level="info")

        # 3. Process Stocks / ETFs
        stocks_root = getattr(self.api.Contracts, 'Stocks', None)
        if stocks_root:
            for sj_exchange_enum, vnpy_exchange_enum in self.sj_exchange_map_vnpy_enum.items():
                if sj_exchange_enum not in [SjExchange.TSE, SjExchange.OTC, SjExchange.OES]: # Process only stock/ETF exchanges
                    continue
                
                # getattr to safely access TSE, OTC etc. under Stocks
                exchange_category_on_sj_api = getattr(stocks_root, sj_exchange_enum.value, None) # e.g., stocks_root.TSE
                
                if exchange_category_on_sj_api and \
                   hasattr(exchange_category_on_sj_api, '_code2contract') and \
                   isinstance(exchange_category_on_sj_api._code2contract, dict):
                    
                    self.write_log(f"處理 {len(exchange_category_on_sj_api._code2contract)} 筆 {sj_exchange_enum.value} 股票/ETF 合約 for {self.gateway_name}...")
                    for code, sj_stk_contract in exchange_category_on_sj_api._code2contract.items():
                        if not isinstance(sj_stk_contract, SjContract) or not all(hasattr(sj_stk_contract, attr) for attr in ['exchange', 'code', 'name']):
                            self.write_log(f"警告 ({self.gateway_name}, {sj_exchange_enum.value}): Stocks 中發現無效對象 for code {getattr(sj_stk_contract, 'code', code)}，已跳過。", level="warning")
                            continue
                        
                        # Double check if the exchange from sj_stk_contract matches vnpy_exchange_enum
                        if self.sj2vnpy.get(sj_stk_contract.exchange) != vnpy_exchange_enum:
                             self.write_log(f"警告 ({self.gateway_name}, {code}): 合約記錄的交易所 '{sj_stk_contract.exchange}' 與其分類 '{sj_exchange_enum.value}' 不符，將使用分類的交易所 '{vnpy_exchange_enum}'.", level="warning")
                        
                        size, tick = self._size_pricetick_generic(sj_stk_contract)
                        
                        # Determine product type (EQUITY vs ETF vs WARRANT etc.)
                        # This is a simplified determination. A more robust way might involve checking sj_stk_contract.category or other fields if available.
                        product_type = Product.EQUITY # Default
                        if "ETF" in sj_stk_contract.name.upper(): # Simple ETF check by name
                            product_type = Product.ETF
                        # Add checks for Product.WARRANT if Shioaji provides enough info

                        cd = ContractData(
                            gateway_name=self.gateway_name, # Handler's specific name
                            symbol=code,
                            exchange=vnpy_exchange_enum, # Use the mapped VnPy enum for this category
                            name=sj_stk_contract.name,
                            product=product_type,
                            size=size, # For stocks, usually 1, but multiplier might be for cost calc. Shioaji stock size usually 1.
                            pricetick=tick,
                            min_volume=1, # Smallest unit (1 share for odd lots, or 1 lot unit)
                            net_position=False, # Stocks are not net_position usually in VnPy context
                            history_data=True,
                        )
                        parsed_contracts_tmp[cd.vt_symbol] = cd
                        self.on_contract(cd) # Send to manager
                else:
                    self.write_log(f"提示 ({self.gateway_name}): 未找到 {sj_exchange_enum.value} 股票/ETF 合約，跳過處理。", level="info")
        else:
            self.write_log(f"提示 ({self.gateway_name}): 未找到股票根目錄 (self.api.Contracts.Stocks)，跳過股票/ETF處理。", level="info")

        # 4. Patch underlying for weekly options (TXO specific example)
        # This uses self._find_mxf_contract() which is now a method of this handler.
        mxf_underlying_symbol, _ = self._find_mxf_contract() 
        if mxf_underlying_symbol:
            patched_count = 0
            for cd_obj in parsed_contracts_tmp.values():
                if cd_obj.product == Product.OPTION and not cd_obj.option_underlying:
                    # Heuristic for weekly options: portfolio might contain "W" or not be a simple YYYYMM
                    # Or, if delivery_date is not a 3rd Wednesday.
                    is_likely_weekly = False
                    if cd_obj.option_expiry:
                        if not is_third_wednesday(cd_obj.option_expiry.date()): # is_third_wednesday is module-level helper
                            is_likely_weekly = True
                        elif "W" in cd_obj.option_portfolio: # If portfolio explicitly has 'W'
                            is_likely_weekly = True
                    
                    if is_likely_weekly and cd_obj.option_portfolio.startswith("TXO"): # Only patch TXO weeklies with MXF for now
                        cd_obj.option_underlying = mxf_underlying_symbol
                        patched_count += 1
            if patched_count > 0:
                self.write_log(f"為 {patched_count} 檔週選擇權合約補齊標的為: {mxf_underlying_symbol}")
        else:
            self.write_log("警告 ({self.gateway_name}): 未找到主要期貨標的 (MXF)，週選擇權標的可能未補齊。", level="warning")

        # 5. Store in handler's cache and send to manager
        final_contract_count = 0
        if parsed_contracts_tmp: 
            with self.contract_lock: 
                self.contracts.clear() 
                for vt_symbol, contract_data_from_dict in parsed_contracts_tmp.items(): # Variable here is contract_data_from_dict
                    self.contracts[vt_symbol] = contract_data_from_dict
                    self.on_contract(contract_data_from_dict) # Use the loop variable
                    final_contract_count += 1
        
        self.write_log(f"完成處理合約 for Handler {self.gateway_name}. 推送 {final_contract_count} 檔合約 (共解析 {len(parsed_contracts_tmp)} 檔)。")

    def query_history(self, req: HistoryRequest) -> Optional[List[BarData]]:
        # Queries history using THIS handler's API.
        # BarData objects should have `gateway_name = self.gateway_name`.
        # This method, if called directly on handler, returns List[BarData].
        # The manager might call this and then forward the data.
        # ... (full implementation adapted from your original) ...
        return None # Placeholder

    def cancel_order(self, req: CancelRequest) -> None:
        """
        使用此 Handler 的 Shioaji API 實例發送撤單請求。
        req.orderid 應為此 Handler 生成的 vt_orderid (e.g., "SHIOAJI_MULTI.ACCOUNT1.XXXXX")
        """
        vt_orderid_to_cancel = req.orderid
        self.write_log(f"收到撤單請求 for order: {vt_orderid_to_cancel}")

        # 1. 檢查此 Handler 的連線和 CA 狀態 (如果 Shioaji 撤單需要 CA)
        # 通常撤單也需要簽署 CA。
        if not self._check_connection(check_ca=True):
            self.write_log(
                f"撤單請求 {vt_orderid_to_cancel} 失敗: Handler {self.gateway_name} 未連線或 CA 未簽署。",
                level="warning"
            )
            # 無法執行撤單，可以考慮是否需要更新本地訂單的 reference 欄位，
            # 但不應改變其 VnPy 狀態，因為撤單指令未發出。
            # Manager 層面可能需要處理這種 Gateway 層級的拒絕。
            # For now, we just log and return, as the cancel was not even attempted with the API.
            # If the order exists locally, its status remains unchanged from this failure.
            return

        # 2. 解析 vt_orderid 以獲取 Shioaji 的 seqno
        # vt_orderid 格式預期為 "HANDLER_GATEWAY_NAME.SHIOAJI_SEQNO"
        # 例如: "SHIOAJI_MULTI.ACCOUNT1.S0000123"
        parts = vt_orderid_to_cancel.split('.')
        if not (len(parts) >= 2 and vt_orderid_to_cancel.startswith(self.gateway_name)):
            self.write_log(
                f"撤單請求 {vt_orderid_to_cancel} 失敗: OrderID 格式不正確或與 Handler ({self.gateway_name}) 不匹配。",
                level="error"
            )
            # 如果 OrderID 格式不對，可能此撤單請求不屬於此 Handler，Manager 應已正確路由。
            # 如果路由到此 Handler 但格式仍錯，則記錄錯誤。
            return
        
        # 假設 seqno 是最後一部分，或者根據您在 send_order 中定義的 vt_orderid 結構來解析
        # 如果 self.gateway_name 本身可能包含 '.', 例如 "GW.ACC1", 而 seqno 是 "S123"
        # 則 vt_orderid = "GW.ACC1.S123". parts = ["GW", "ACC1", "S123"]
        # seqno = parts[-1] 仍然適用。
        shioaji_seqno = parts[-1]

        # 3. 從 Handler 的快取中獲取對應的 SjTrade (Shioaji 委託物件) 和 OrderData (VnPy 訂單物件)
        sj_trade_to_cancel: Optional[SjTrade] = None
        vnpy_order_to_update: Optional[OrderData] = None

        with self.order_map_lock: # 保護對快取的訪問
            sj_trade_to_cancel = self.shioaji_trades.get(shioaji_seqno)
            vnpy_order_to_update = self.orders.get(vt_orderid_to_cancel)

        if not vnpy_order_to_update:
            self.write_log(
                f"撤單請求 {vt_orderid_to_cancel} 失敗: 在 Handler {self.gateway_name} 中找不到對應的 VnPy OrderData 快取。",
                level="warning"
            )
            # 可能是 Manager 路由錯誤，或者訂單已極早期清理。
            return
            
        if not sj_trade_to_cancel:
            self.write_log(
                f"撤單請求 {vt_orderid_to_cancel} (SeqNo: {shioaji_seqno}) 失敗: "
                f"在 Handler {self.gateway_name} 中找不到對應的 Shioaji Trade 物件。訂單可能已終結。",
                level="warning"
            )
            # 如果 Shioaji Trade 物件不存在，可能訂單已經是最終狀態 (成交/已撤/失敗)。
            # 更新 reference，但不主動改變狀態，等待回調確認。
            vnpy_order_to_update.reference = "撤單失敗: 內部 Shioaji 委託記錄不存在"
            self.on_order(copy.copy(vnpy_order_to_update)) # 通知 Manager reference 變更
            return

        # 4. 檢查 VnPy 訂單狀態是否允許撤單
        if not vnpy_order_to_update.is_active():
            self.write_log(
                f"訂單 {vt_orderid_to_cancel} 當前狀態為 {vnpy_order_to_update.status.value} (非活躍)，"
                f"無需執行撤單操作 for handler {self.gateway_name}。"
            )
            return # 訂單已完成或已撤銷

        # 5. 執行 Shioaji API 撤單
        try:
            self.write_log(
                f"Handler {self.gateway_name}: 嘗試透過 Shioaji API 撤銷訂單: "
                f"vt_orderid={vt_orderid_to_cancel}, Shioaji SeqNo={shioaji_seqno}"
            )
            self.api.cancel_order(sj_trade_to_cancel) # 使用 Shioaji 的 Trade 物件進行撤單
            
            self.write_log(f"撤單請求已成功發送至 Shioaji for order {vt_orderid_to_cancel} by handler {self.gateway_name}.")

            # 撤單指令發送成功後，可以更新本地 OrderData 的 reference。
            # 最終的 Status.CANCELLED 狀態將由 _on_order_deal_shioaji 回調處理。
            # 有些系統可能會在這裡設定一個臨時的 "Cancelling" 狀態，但 VnPy 沒有標準的此狀態。
            if vnpy_order_to_update:
                with self.order_map_lock: # 確保對 vnpy_order_to_update 的修改是線程安全的
                    vnpy_order_to_update.reference = "撤單請求已發送 (Cancel request sent)"
                # 推送這個帶有更新 reference 的訂單狀態
                self.on_order(copy.copy(vnpy_order_to_update)) 

        except Exception as e:
            self.write_log(
                f"Handler {self.gateway_name}: 調用 Shioaji API cancel_order "
                f"時發生異常 for {vt_orderid_to_cancel}: {e}\n{traceback.format_exc()}",
                level="error"
            )
            # API 調用失敗，更新 reference，但不改變訂單狀態，因為訂單可能仍然在交易所掛著。
            if vnpy_order_to_update:
                with self.order_map_lock:
                    vnpy_order_to_update.reference = f"撤單 API 調用失敗: {e}"
                self.on_order(copy.copy(vnpy_order_to_update))