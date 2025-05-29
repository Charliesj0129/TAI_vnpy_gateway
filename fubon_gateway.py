# fubon_gateway.py

# --- Python 標準庫導入 ---
import copy
import traceback
from datetime import datetime, date, timedelta
from threading import Lock, Thread
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from zoneinfo import ZoneInfo
import calendar
import time
import threading
import asyncio
import janus
import json

# --- VnPy 相關導入 ---
from vnpy.event import EventEngine, Event
from vnpy.trader.event import EVENT_TIMER, EVENT_LOG, EVENT_ERROR
from vnpy.trader.utility import load_json, save_json, extract_vt_symbol
from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import (
    TickData, OrderData, TradeData, PositionData, AccountData, ContractData, OrderRequest, CancelRequest, SubscribeRequest, HistoryRequest, BarData
)
from vnpy.trader.constant import (Exchange, Product, Direction, OrderType, Offset, Status, Interval, OptionType)
from vnpy.trader.utility import BarGenerator

# --- Fubon Neo 相關導入 ---
from fubon_neo.sdk import FubonSDK
from fubon_neo.constant import (
    BSAction as FubonBSAction,
    OrderType as FubonOrderType,
    PriceType as FubonPriceType,
    TimeInForce as FubonTimeInForce,
    MarketType as FubonMarketType,
    FutOptOrderType as FubonFutOptOrderType,
    FutOptPriceType as FubonFutOptPriceType,
    FutOptMarketType as FubonFutOptMarketType,
    CallPut as FubonCallPut
)

# --- 常量定義與映射 ---

# 時區設定 (Fubon API 通常返回台灣時間)
TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# Gateway 設定檔案名稱
GATEWAY_SETTING_FILENAME = "fubon_connect.json"

# 導入映射常量 (從 enum_map 模組)
from src.enum_map import (
    DIRECTION_MAP, DIRECTION_MAP_REVERSE,
    PRICE_TYPE_MAP, PRICE_TYPE_MAP_REVERSE,
    FUTOPT_PRICE_TYPE_MAP, FUTOPT_PRICE_TYPE_MAP_REVERSE,
    FUTURES_OFFSET_MAP, FUTURES_OFFSET_MAP_REVERSE,
    OPTION_TYPE_MAP, OPTION_TYPE_MAP_REVERSE,
    MARKET_TYPE_EXCHANGE_MAP, MARKET_TYPE_PRODUCT_MAP,
    FUTOPT_MARKET_TYPE_MAP,
    STATUS_MAP
)

# --- No Custom Events; Use vn.py Standard Events ---
# Custom events have been removed to align with vn.py standards.
# Use EVENT_LOG for informational messages and EVENT_ERROR for error conditions.

# --- Helper Functions ---

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

def _parse_date(date_str: str, fmt: str = '%Y/%m/%d') -> Optional[date]:
    if not date_str or not isinstance(date_str, str):
        return None
    try:
        return datetime.strptime(date_str, fmt).date()
    except ValueError:
        try:
            return datetime.strptime(date_str, '%Y%m%d').date()
        except ValueError:
            return None

# --- Gateway 類別定義開始 ---

class FubonGateway(BaseGateway):
    """
    適用於 VnPy 4.0 的 Fubon Neo 接口 Gateway。
    """
    default_setting = {
        "UserID": "",
        "Password": "",
        "CA路徑": "",
        "CA密碼": "",
        "simulation": False,
        "下載合約": False,
        "重連次數": 3,
        "重連間隔(秒)": 5
    }
    exchanges: List[Exchange] = [
        Exchange.TWSE,
        Exchange.TOTC,
        Exchange.TAIFEX,
    ]

    def __init__(self, event_engine: EventEngine, gateway_name: str = "FUBON"):
        """構造函數"""
        super().__init__(event_engine, gateway_name)

        self.loop = asyncio.new_event_loop()
        self.janus_queue = janus.Queue(maxsize=3000)
        threading.Thread(target=self._start_loop, daemon=True).start()

        self.sdk: Optional[FubonSDK] = None
        self.connected: bool = False
        self.logged_in: bool = False
        self.connect_thread: Optional[Thread] = None
        self.connection_start_time: float = 0

        self.reconnect_attempts: int = 0
        self.reconnect_limit: int = 3
        self.reconnect_interval: int = 5

        self.connect_setting: dict = {}
        self.user_id: str = ""
        self.password: str = ""
        self.ca_path: str = ""
        self.ca_passwd: str = ""
        self.simulation: bool = False
        self.force_download: bool = False

        self.orders: Dict[str, OrderData] = {}
        self.fubon_trades: Dict[str, Any] = {}
        self.positions: Dict[Tuple[str, Direction], PositionData] = {}
        self.accounts: Dict[str, AccountData] = {}
        self.contracts: Dict[str, ContractData] = {}
        self.subscribed: Set[str] = set()
        self.tick_cache: Dict[str, TickData] = {}

        self.order_map_lock = Lock()
        self.position_lock = Lock()
        self.account_lock = Lock()
        self.contract_lock = Lock()
        self.subscribed_lock = Lock()
        self.tick_cache_lock = Lock()
        self._reconnect_lock = Lock()

        self.market_proxy: Optional[MarketProxy] = None

        loaded_setting = load_json(GATEWAY_SETTING_FILENAME)
        if loaded_setting:
            self.connect_setting = loaded_setting
            self.default_setting.update(loaded_setting)
        else:
            self.write_log(f"__init__: No previous settings file found ({GATEWAY_SETTING_FILENAME}). Using defaults.")

    def _start_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._queue_consumer())
        self.loop.run_forever()

    async def _process_batch(self, batch: list):
        for tick in batch:
            self.on_tick(tick)

    async def _queue_consumer(self):
        while True:
            tick: TickData = await self.janus_queue.async_q.get()
            try:
                await self._process_batch([tick])
            except Exception as e:
                self.write_log(f"队列消费推送错误: {e}")

    def connect(self, setting: dict):
        """
        連接 Fubon Neo API (非阻塞)。
        啟動一個工作線程來執行實際的連接、登入、初始化流程。
        """
        if self.connected or self.logged_in:
            self.write_log("connect: Returning early because already connected or logged in.")
            return

        thread_alive = self.connect_thread and self.connect_thread.is_alive()
        if thread_alive:
            self.write_log("connect: Returning early because connect_thread is still alive.")
            return

        self.connection_start_time = time.time()
        self.connect_setting.update(setting)
        self.connect_thread = Thread(target=self._connect_worker, args=(self.connect_setting,))
        self.connect_thread.daemon = True
        self.connect_thread.start()

    def _connect_worker(self, setting: dict):
        """
        實際執行連接的工作線程函數。
        包括初始化、登入、激活CA、獲取合約、設置回調。
        """
        self.reconnect_attempts = 0

        try:
            self.user_id = setting.get("UserID", "")
            self.password = setting.get("Password", "")
            self.ca_path = setting.get("CA路徑", "").replace("\\", "/")
            self.ca_passwd = setting.get("CA密碼", "")
            self.simulation = setting.get("simulation", False)
            self.force_download = setting.get("下載合約", False)
            self.reconnect_limit = setting.get("重連次數", 3)
            self.reconnect_interval = setting.get("重連間隔(秒)", 5)

            if not self.user_id or not self.password:
                self.write_log("錯誤：缺少 UserID 或 Password")
                self._handle_disconnect()
                return

            self.sdk = FubonSDK()
            accounts = self.sdk.login(
                self.user_id,
                self.password,
                self.ca_path,
                self.ca_passwd
            )

            if not accounts:
                self.write_log("錯誤：Fubon Neo API 登入失敗")
                self._handle_disconnect()
                return

            # 分辨股票與期貨帳戶並存入字典
            self.stock_accounts = []
            self.futopt_accounts = []
            self.accounts = {}
            for acc in accounts:
                if hasattr(acc, 'account') and acc.account:
                    if "stock" in acc.account.lower() or "equity" in acc.account.lower():
                        self.stock_accounts.append(acc)
                        key = f"STOCK_{acc.account}"
                        self.accounts[key] = acc
                    elif "fut" in acc.account.lower() or "opt" in acc.account.lower():
                        self.futopt_accounts.append(acc)
                        key = f"FUTOPT_{acc.account}"
                        self.accounts[key] = acc
            if not self.stock_accounts and not self.futopt_accounts:
                self.write_log("錯誤：未找到有效的股票或期貨帳戶")
                self._handle_disconnect()
                return

            self.write_log(f"登入成功，找到 {len(self.stock_accounts)} 個股票帳戶，{len(self.futopt_accounts)} 個期貨帳戶")
            save_json(GATEWAY_SETTING_FILENAME, setting)
            self.connected = True
            self.logged_in = True
            self.reconnect_attempts = 0
            
            # 設置 Fubon API 回調函數以映射到 vn.py 標準事件
            self._setup_callbacks()
            # 初始化 MarketProxy 來處理行情數據
            self.market_proxy = MarketProxy(self.sdk, self)
            self.query_all()

        except Exception as e_outer:
            self.write_log(f"錯誤：Fubon Neo 連接過程中出錯: {e_outer}\n詳細錯誤:\n{traceback.format_exc()}")
            self._handle_disconnect()

    def _setup_callbacks(self):
        """
        設置 Fubon API 的回調函數，將其映射到 vn.py 標準事件。
        """
        if not self.sdk:
            self.write_log("設置回調失敗：SDK 未初始化")
            return

        try:
            # 股票回調設置
            if self.stock_accounts:
                self.sdk.stock.set_on_order(self._on_stock_order)
                self.sdk.stock.set_on_filled(self._on_stock_filled)
                self.sdk.stock.set_on_order_changed(self._on_stock_order_changed)
                self.write_log("股票回調設置成功")

            # 期貨/選擇權回調設置
            if self.futopt_accounts:
                self.sdk.futopt.set_on_order(self._on_futopt_order)
                self.sdk.futopt.set_on_filled(self._on_futopt_filled)
                self.sdk.futopt.set_on_order_changed(self._on_futopt_order_changed)
                self.write_log("期貨/選擇權回調設置成功")

            # 通用事件回調（如斷線通知）
            self.sdk.set_on_event(self._on_event)
        except Exception as e:
            self.write_error(f"Failed to setup callbacks: {e}")

    def _on_stock_order(self, order_data):
        """處理股票新單回調，映射到 EVENT_ORDER"""
        try:
            vt_symbol = order_data.get('symbol', '')
            seq_no = order_data.get('seq_no', '')
            contract = self.contracts.get(vt_symbol)
            if not contract:
                self.write_log(f"股票新單處理失敗：未找到合約 {vt_symbol}")
                return

            order = OrderData(
                symbol=vt_symbol,
                exchange=contract.exchange,
                orderid=seq_no,
                direction=DIRECTION_MAP_REVERSE.get(str(order_data.get('buy_sell')), Direction.LONG),
                price=float(order_data.get('price', 0.0)),
                volume=float(order_data.get('quantity', 0.0)),
                status=STATUS_MAP.get(order_data.get('status', 'Pending'), Status.SUBMITTING),
                datetime=datetime.now(TAIPEI_TZ),
                gateway_name=self.gateway_name
            )
            with self.order_map_lock:
                self.orders[seq_no] = order
            self.on_order(order)
        except Exception as e:
            self.write_log(f"股票新單處理錯誤: {e}")

    def _on_stock_filled(self, fill_data):
        """處理股票成交回調，映射到 EVENT_TRADE"""
        try:
            vt_symbol = fill_data.get('symbol', '')
            seq_no = fill_data.get('seq_no', '')
            contract = self.contracts.get(vt_symbol)
            if not contract:
                self.write_log(f"股票成交處理失敗：未找到合約 {vt_symbol}")
                return

            trade = TradeData(
                symbol=vt_symbol,
                exchange=contract.exchange,
                orderid=seq_no,
                tradeid=fill_data.get('fill_id', ''),
                direction=DIRECTION_MAP_REVERSE.get(str(fill_data.get('buy_sell')), Direction.LONG),
                price=float(fill_data.get('price', 0.0)),
                volume=float(fill_data.get('quantity', 0.0)),
                datetime=datetime.now(TAIPEI_TZ),
                gateway_name=self.gateway_name
            )
            with self.order_map_lock:
                self.fubon_trades[seq_no] = trade
            self.on_trade(trade)

            # 更新訂單狀態
            if seq_no in self.orders:
                order = self.orders[seq_no]
                order.status = Status.ALLTRADED
                self.on_order(order)
        except Exception as e:
            self.write_log(f"股票成交處理錯誤: {e}")

    def _on_stock_order_changed(self, change_data):
        """處理股票改單回調，映射到 EVENT_ORDER"""
        try:
            seq_no = change_data.get('seq_no', '')
            if seq_no in self.orders:
                order = self.orders[seq_no]
                order.price = float(change_data.get('price', order.price))
                order.volume = float(change_data.get('quantity', order.volume))
                order.status = STATUS_MAP.get(change_data.get('status', 'Pending'), order.status)
                self.on_order(order)
            else:
                self.write_log(f"股票改單處理失敗：未找到訂單 {seq_no}")
        except Exception as e:
            self.write_log(f"股票改單處理錯誤: {e}")

    def _on_futopt_order(self, order_data):
        """處理期貨/選擇權新單回調，映射到 EVENT_ORDER"""
        try:
            vt_symbol = order_data.get('symbol', '')
            seq_no = order_data.get('seq_no', '')
            contract = self.contracts.get(vt_symbol)
            if not contract:
                self.write_log(f"期貨/選擇權新單處理失敗：未找到合約 {vt_symbol}")
                return

            order = OrderData(
                symbol=vt_symbol,
                exchange=contract.exchange,
                orderid=seq_no,
                direction=DIRECTION_MAP_REVERSE.get(str(order_data.get('buy_sell')), Direction.LONG),
                offset=FUTURES_OFFSET_MAP_REVERSE.get(str(order_data.get('order_type')), Offset.NONE),
                price=float(order_data.get('price', 0.0)),
                volume=float(order_data.get('quantity', 0.0)),
                status=STATUS_MAP.get(order_data.get('status', 'Pending'), Status.SUBMITTING),
                datetime=datetime.now(TAIPEI_TZ),
                gateway_name=self.gateway_name
            )
            with self.order_map_lock:
                self.orders[seq_no] = order
            self.on_order(order)
        except Exception as e:
            self.write_log(f"期貨/選擇權新單處理錯誤: {e}")

    def _on_futopt_filled(self, fill_data):
        """處理期貨/選擇權成交回調，映射到 EVENT_TRADE"""
        try:
            vt_symbol = fill_data.get('symbol', '')
            seq_no = fill_data.get('seq_no', '')
            contract = self.contracts.get(vt_symbol)
            if not contract:
                self.write_log(f"期貨/選擇權成交處理失敗：未找到合約 {vt_symbol}")
                return

            trade = TradeData(
                symbol=vt_symbol,
                exchange=contract.exchange,
                orderid=seq_no,
                tradeid=fill_data.get('fill_id', ''),
                direction=DIRECTION_MAP_REVERSE.get(str(fill_data.get('buy_sell')), Direction.LONG),
                offset=FUTURES_OFFSET_MAP_REVERSE.get(str(fill_data.get('order_type')), Offset.NONE),
                price=float(fill_data.get('price', 0.0)),
                volume=float(fill_data.get('quantity', 0.0)),
                datetime=datetime.now(TAIPEI_TZ),
                gateway_name=self.gateway_name
            )
            with self.order_map_lock:
                self.fubon_trades[seq_no] = trade
            self.on_trade(trade)

            # 更新訂單狀態
            if seq_no in self.orders:
                order = self.orders[seq_no]
                order.status = Status.ALLTRADED
                self.on_order(order)
        except Exception as e:
            self.write_log(f"期貨/選擇權成交處理錯誤: {e}")

    def _on_futopt_order_changed(self, change_data):
        """處理期貨/選擇權改單回調，映射到 EVENT_ORDER"""
        try:
            seq_no = change_data.get('seq_no', '')
            if seq_no in self.orders:
                order = self.orders[seq_no]
                order.price = float(change_data.get('price', order.price))
                order.volume = float(change_data.get('quantity', order.volume))
                order.status = STATUS_MAP.get(change_data.get('status', 'Pending'), order.status)
                self.on_order(order)
            else:
                self.write_log(f"期貨/選擇權改單處理失敗：未找到訂單 {seq_no}")
        except Exception as e:
            self.write_log(f"期貨/選擇權改單處理錯誤: {e}")

    def _on_event(self, event_code, event_content):
        """處理通用事件回調，如斷線通知，使用 EVENT_ERROR"""
        try:
            if event_code == "300":  # 斷線事件代碼
                self.write_error(f"Disconnected event received: {event_content}")
                self._handle_disconnect()
            else:
                self.write_log(f"Event {event_code}: {event_content}")
        except Exception as e:
            self.write_log(f"通用事件處理錯誤: {e}")

    def close(self):
        """關閉連接"""
        if not self.logged_in and not self.connected:
            self.write_log("接口未連接，無需斷開")
            return

        self.write_log("正在斷開 Fubon Neo API 連接...")
        if self.logged_in and self.sdk:
            try:
                # Placeholder for logout if SDK supports it
                self.write_log("Fubon Neo API 已登出")
            except Exception as e:
                self.write_log(f"Fubon Neo API 登出時發生錯誤: {e}")

        if self.market_proxy:
            self.market_proxy.disconnect()
            self.market_proxy = None

        self.sdk = None
        self.connected = False
        self.logged_in = False
        self.reconnect_attempts = 0

        with self.order_map_lock:
            self.orders.clear()
            self.fubon_trades.clear()
        with self.position_lock:
            self.positions.clear()
        with self.account_lock:
            self.accounts.clear()
        with self.contract_lock:
            self.contracts.clear()

        self.write_log(f"{self.gateway_name} 接口連接已斷開")

    def _handle_disconnect(self):
        """處理斷線邏輯，使用 EVENT_ERROR 報告斷線錯誤"""
        self.connected = False
        self.logged_in = False
        self.write_error("Fubon Neo API connection lost, attempting reconnect")
        self._start_reconnect()

    def _start_reconnect(self):
        """啟動重連邏輯，使用 EVENT_LOG 記錄重連嘗試"""
        if self.reconnect_attempts < self.reconnect_limit:
            self.reconnect_attempts += 1
            self.write_log(f"Reconnect attempt {self.reconnect_attempts}/{self.reconnect_limit}")
            time.sleep(self.reconnect_interval)
            self._do_reconnect()
        else:
            self.write_error("Max reconnect attempts reached, giving up")

    def _do_reconnect(self):
        """執行重連，使用 EVENT_LOG 記錄重連過程"""
        self.write_log("Executing reconnect...")
        self._connect_worker(self.connect_setting)

    def subscribe(self, req: SubscribeRequest) -> None:
        """
        訂閱即時行情數據。
        使用 MarketProxy 來處理 Fubon Neo API 的 WebSocket 訂閱。
        """
        if not self.connected or not self.logged_in or not self.sdk or not self.market_proxy:
            self.write_log("訂閱失敗：未連接或未登入或 MarketProxy 未初始化")
            return

        vt_symbol = req.symbol
        if vt_symbol in self.subscribed:
            self.write_log(f"已訂閱 {vt_symbol}，跳過")
            return

        try:
            with self.subscribed_lock:
                self.subscribed.add(vt_symbol)

            if self.market_proxy.subscribe([vt_symbol]):
                self.write_log(f"Subscription success for {vt_symbol}")
            else:
                self.write_error(f"Subscription failed for {vt_symbol}")
                with self.subscribed_lock:
                    self.subscribed.discard(vt_symbol)
        except Exception as e:
            self.write_error(f"Subscription failed for {vt_symbol}: {e}")
            with self.subscribed_lock:
                self.subscribed.discard(vt_symbol)

    def unsubscribe(self, req: SubscribeRequest) -> None:
        """
        取消訂閱即時行情數據。
        使用 MarketProxy 來處理 Fubon Neo API 的 WebSocket 取消訂閱。
        """
        if not self.connected or not self.logged_in or not self.sdk or not self.market_proxy:
            self.write_log("取消訂閱失敗：未連接或未登入或 MarketProxy 未初始化")
            return

        symbol = req.symbol
        if symbol not in self.subscribed:
            self.write_log(f"未訂閱 {symbol}，無需取消")
            return

        try:
            if self.market_proxy.unsubscribe([symbol]):
                with self.subscribed_lock:
                    self.subscribed.discard(symbol)
                self.write_log(f"Unsubscription success for {symbol}")
            else:
                self.write_error(f"Unsubscription failed for {symbol}")
        except Exception as e:
            self.write_error(f"Unsubscription failed for {symbol}: {e}")

    def send_order(self, req: OrderRequest, **kwargs) -> str:
        """
        發送下單請求。
        根據合約的交易所或符號前綴，動態選擇對應的帳戶與 SDK 模組進行下單。
        """
        if not self.connected or not self.logged_in or not self.sdk:
            self.write_log("下單失敗：未連接或未登入")
            return ""

        vt_symbol = req.symbol
        contract = self.contracts.get(vt_symbol)
        if not contract:
            self.write_log(f"下單失敗：未找到合約 {vt_symbol}")
            return ""

        try:
            # 根據交易所或符號前綴選擇帳戶與 SDK 模組
            if contract.exchange == Exchange.TAIFEX or vt_symbol.startswith("TXF"):
                if not self.futopt_accounts:
                    self.write_log("下單失敗：未找到期貨/選擇權帳戶")
                    return ""
                account = self.futopt_accounts[0]  # 選擇第一個期貨帳戶
                sdk_module = self.sdk.futopt
                price_type, time_in_force = FUTOPT_PRICE_TYPE_MAP.get(req.type, (FubonFutOptPriceType.Limit, FubonTimeInForce.ROD))
                order_type = FUTURES_OFFSET_MAP.get(req.offset, FubonFutOptOrderType.Auto)
                bs_action = DIRECTION_MAP.get(req.direction, FubonBSAction.Buy)
                order = sdk_module.Order(
                    buy_sell=bs_action,
                    symbol=vt_symbol,
                    price=req.price if req.type == OrderType.LIMIT else 0.0,
                    quantity=int(req.volume),
                    price_type=price_type,
                    order_type=order_type,
                    time_in_force=time_in_force
                )
                result = sdk_module.place_order(account, order)
            else:
                if not self.stock_accounts:
                    self.write_log("下單失敗：未找到股票帳戶")
                    return ""
                account = self.stock_accounts[0]  # 選擇第一個股票帳戶
                sdk_module = self.sdk.stock
                price_type, time_in_force = PRICE_TYPE_MAP.get(req.type, (FubonPriceType.Limit, FubonTimeInForce.ROD))
                bs_action = DIRECTION_MAP.get(req.direction, FubonBSAction.Buy)
                order = sdk_module.Order(
                    buy_sell=bs_action,
                    symbol=vt_symbol,
                    price=req.price if req.type == OrderType.LIMIT else 0.0,
                    quantity=int(req.volume),
                    price_type=price_type,
                    time_in_force=time_in_force
                )
                result = sdk_module.place_order(account, order)

            if not result or not hasattr(result, 'seq_no'):
                self.write_log(f"下單失敗：無效的回應，合約 {vt_symbol}")
                return ""

            seq_no = result.seq_no
            order_data = OrderData(
                symbol=vt_symbol,
                exchange=contract.exchange,
                orderid=seq_no,
                type=req.type,
                direction=req.direction,
                offset=req.offset,
                price=req.price,
                volume=req.volume,
                status=Status.SUBMITTING,
                datetime=datetime.now(TAIPEI_TZ),
                gateway_name=self.gateway_name
            )
            with self.order_map_lock:
                self.orders[seq_no] = order_data
            self.on_order(order_data)
            self.write_log(f"下單成功：{vt_symbol}，序號 {seq_no}")
            return seq_no
        except Exception as e:
            self.write_log(f"下單失敗：{vt_symbol}，錯誤：{e}")
            return ""

    def cancel_order(self, req: CancelRequest) -> None:
        """
        發送撤單請求。
        根據訂單的合約交易所或符號前綴，動態選擇對應的帳戶與 SDK 模組進行撤單。
        """
        if not self.connected or not self.logged_in or not self.sdk:
            self.write_log("撤單失敗：未連接或未登入")
            return

        vt_symbol = req.symbol
        order_id = req.orderid
        contract = self.contracts.get(vt_symbol)
        if not contract:
            self.write_log(f"撤單失敗：未找到合約 {vt_symbol}")
            return

        try:
            with self.order_map_lock:
                order_data = self.orders.get(order_id)
            if not order_data:
                self.write_log(f"撤單失敗：未找到訂單 {order_id}")
                return

            # 根據交易所或符號前綴選擇帳戶與 SDK 模組
            if contract.exchange == Exchange.TAIFEX or vt_symbol.startswith("TXF"):
                if not self.futopt_accounts:
                    self.write_log("撤單失敗：未找到期貨/選擇權帳戶")
                    return
                account = self.futopt_accounts[0]  # 選擇第一個期貨帳戶
                sdk_module = self.sdk.futopt
            else:
                if not self.stock_accounts:
                    self.write_log("撤單失敗：未找到股票帳戶")
                    return
                account = self.stock_accounts[0]  # 選擇第一個股票帳戶
                sdk_module = self.sdk.stock

            result = sdk_module.cancel_order(account, order_id)
            if not result or not hasattr(result, 'success') or not result.success:
                self.write_log(f"撤單失敗：訂單 {order_id}，回應無效或失敗")
                return

            order_data.status = Status.CANCELLED
            self.on_order(order_data)
            self.write_log(f"撤單成功：訂單 {order_id}")
        except Exception as e:
            self.write_log(f"撤單失敗：訂單 {order_id}，錯誤：{e}")

    def modify_order_price(self, vt_symbol: str, order_id: str, new_price: float) -> bool:
        """
        修改訂單價格。
        根據訂單的合約交易所或符號前綴，動態選擇對應的帳戶與 SDK 模組進行價格修改。
        """
        if not self.connected or not self.logged_in or not self.sdk:
            self.write_log("改價失敗：未連接或未登入")
            return False

        contract = self.contracts.get(vt_symbol)
        if not contract:
            self.write_log(f"改價失敗：未找到合約 {vt_symbol}")
            return False

        try:
            with self.order_map_lock:
                order_data = self.orders.get(order_id)
            if not order_data:
                self.write_log(f"改價失敗：未找到訂單 {order_id}")
                return False

            # 根據交易所或符號前綴選擇帳戶與 SDK 模組
            if contract.exchange == Exchange.TAIFEX or vt_symbol.startswith("TXF"):
                if not self.futopt_accounts:
                    self.write_log("改價失敗：未找到期貨/選擇權帳戶")
                    return False
                account = self.futopt_accounts[0]  # 選擇第一個期貨帳戶
                sdk_module = self.sdk.futopt
            else:
                if not self.stock_accounts:
                    self.write_log("改價失敗：未找到股票帳戶")
                    return False
                account = self.stock_accounts[0]  # 選擇第一個股票帳戶
                sdk_module = self.sdk.stock

            result = sdk_module.modify_order_price(account, order_id, new_price)
            if not result or not hasattr(result, 'success') or not result.success:
                self.write_log(f"改價失敗：訂單 {order_id}，回應無效或失敗")
                return False

            order_data.price = new_price
            self.on_order(order_data)
            self.write_log(f"改價成功：訂單 {order_id}，新價格 {new_price}")
            return True
        except Exception as e:
            self.write_log(f"改價失敗：訂單 {order_id}，錯誤：{e}")
            return False

    def modify_order_quantity(self, vt_symbol: str, order_id: str, new_quantity: float) -> bool:
        """
        修改訂單數量。
        根據訂單的合約交易所或符號前綴，動態選擇對應的帳戶與 SDK 模組進行數量修改。
        """
        if not self.connected or not self.logged_in or not self.sdk:
            self.write_log("改量失敗：未連接或未登入")
            return False

        contract = self.contracts.get(vt_symbol)
        if not contract:
            self.write_log(f"改量失敗：未找到合約 {vt_symbol}")
            return False

        try:
            with self.order_map_lock:
                order_data = self.orders.get(order_id)
            if not order_data:
                self.write_log(f"改量失敗：未找到訂單 {order_id}")
                return False

            # 根據交易所或符號前綴選擇帳戶與 SDK 模組
            if contract.exchange == Exchange.TAIFEX or vt_symbol.startswith("TXF"):
                if not self.futopt_accounts:
                    self.write_log("改量失敗：未找到期貨/選擇權帳戶")
                    return False
                account = self.futopt_accounts[0]  # 選擇第一個期貨帳戶
                sdk_module = self.sdk.futopt
            else:
                if not self.stock_accounts:
                    self.write_log("改量失敗：未找到股票帳戶")
                    return False
                account = self.stock_accounts[0]  # 選擇第一個股票帳戶
                sdk_module = self.sdk.stock

            result = sdk_module.modify_order_quantity(account, order_id, int(new_quantity))
            if not result or not hasattr(result, 'success') or not result.success:
                self.write_log(f"改量失敗：訂單 {order_id}，回應無效或失敗")
                return False

            order_data.volume = new_quantity
            self.on_order(order_data)
            self.write_log(f"改量成功：訂單 {order_id}，新數量 {new_quantity}")
            return True
        except Exception as e:
            self.write_log(f"改量失敗：訂單 {order_id}，錯誤：{e}")
            return False

    def query_account(self, event: Event = None) -> None:
        """查詢帳戶資金，使用 EVENT_ACCOUNT 推送更新"""
        if not self.connected or not self.logged_in or not self.sdk:
            self.write_log("查詢帳戶失敗：未連接或未登入")
            return
        
        try:
            if not hasattr(self, 'account_mgr'):
                self.account_mgr = AccountMgr(self.sdk, list(self.accounts.values()))
            accounts_data = self.account_mgr.query_accounts()
            for account_data in accounts_data:
                self.on_account(account_data)
            self.write_log("帳戶資金查詢成功")
        except Exception as e:
            self.write_error(f"Account query failed: {e}")

    def query_position(self, event: Event = None) -> None:
        """查詢持倉，使用 EVENT_POSITION 推送更新"""
        if not self.connected or not self.logged_in or not self.sdk:
            self.write_log("查詢持倉失敗：未連接或未登入")
            return
        
        try:
            if not hasattr(self, 'account_mgr'):
                self.account_mgr = AccountMgr(self.sdk, list(self.accounts.values()))
            positions_data = self.account_mgr.query_positions()
            for position_data in positions_data:
                self.on_position(position_data)
            self.write_log("持倉查詢成功")
        except Exception as e:
            self.write_error(f"Position query failed: {e}")

    def query_history(self, req: HistoryRequest) -> List[BarData]:
        """
        Query historical bar data for the given request parameters.
        """
        if not self.connected or not self.logged_in or not self.sdk:
            self.write_log("查詢歷史數據失敗：未連接或未登入")
            return []

        symbol = req.symbol
        period = req.interval.value  # e.g. '1m', '1d'
        start, end = req.start, req.end
        
        try:
            if req.interval == Interval.MINUTE:
                response = self.sdk.marketdata.rest_client.stock.aggregate(
                    symbol=symbol, period=period, start=start, end=end
                )
            elif req.interval == Interval.DAILY:
                response = self.sdk.marketdata.rest_client.stock.candles(
                    symbol=symbol, period=period, start=start, end=end
                )
            else:
                self.write_log(f"不支持的時間間隔：{req.interval}")
                return []

            bars = []
            if 'data' in response:
                for item in response['data']:
                    bar = BarData(
                        symbol=symbol,
                        exchange=self.contracts[symbol].exchange if symbol in self.contracts else Exchange.TWSE,
                        interval=req.interval,
                        datetime=datetime.fromtimestamp(item['time'], tz=TAIPEI_TZ),
                        open_price=item['open'],
                        high_price=item['high'],
                        low_price=item['low'],
                        close_price=item['close'],
                        volume=item['volume'],
                        gateway_name=self.gateway_name
                    )
                    bars.append(bar)
            self.write_log(f"歷史數據查詢成功：{symbol}，共 {len(bars)} 條記錄")
            return bars
        except Exception as e:
            self.write_log(f"歷史數據查詢失敗：{symbol}，錯誤：{e}")
            return []

    def query_contracts(self, event: Event = None) -> None:
        """
        查詢合約數據。
        使用 ContractLoader 獲取可交易商品列表。
        """
        if not self.connected or not self.logged_in or not self.sdk:
            self.write_log("查詢合約失敗：未連接或未登入")
            return

        try:
            if not hasattr(self, 'contract_loader'):
                self.contract_loader = ContractLoader(self.sdk, self.gateway_name)
            stock_contracts = self.contract_loader.load_stock_contracts()
            futopt_contracts = self.contract_loader.load_futopt_contracts()
            stock_count = 0
            futopt_count = 0
            for contract in stock_contracts:
                with self.contract_lock:
                    self.contracts[contract.symbol] = contract
                self.on_contract(contract)
                stock_count += 1
            for contract in futopt_contracts:
                with self.contract_lock:
                    self.contracts[contract.symbol] = contract
                self.on_contract(contract)
                futopt_count += 1
            self.write_log(f"合約數據查詢成功，獲取 {stock_count} 個股票合約，{futopt_count} 個期貨/選擇權合約")
        except Exception as e:
            self.write_error(f"Contract query failed: {e}")

class MarketProxy:
    """
    A class to manage WebSocket-based market data subscriptions for Fubon Neo SDK,
    converting incoming trades and books data to vn.py TickData objects.
    """
    def __init__(self, sdk: FubonSDK, gateway: BaseGateway):
        """
        Initialize the MarketProxy with Fubon SDK and gateway reference.
        
        Args:
            sdk (FubonSDK): The initialized Fubon Neo SDK instance.
            gateway (BaseGateway): The vn.py gateway instance for event pushing.
        """
        self.sdk = sdk
        self.gateway = gateway
        self.ws_initialized = False
        self.subscribed_symbols = set()
        self.tick_cache = {}
        self.queue = janus.Queue(maxsize=3000)  # For buffering high-frequency data
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 3
        self.reconnect_interval = 5  # seconds
        self.stock_channel = None
        self.futopt_channel = None

    def connect(self) -> bool:
        """
        Initialize the WebSocket connection for real-time market data in low-latency mode.
        Sets up callbacks for incoming messages and starts heartbeat mechanism.
        
        Returns:
            bool: True if connection is successful, False otherwise.
        """
        try:
            if not self.ws_initialized:
                self.sdk.init_realtime(mode="Speed")  # Use low-latency mode
                self.stock_channel = self.sdk.marketdata.websocket_client.stock
                self.futopt_channel = self.sdk.marketdata.websocket_client.futopt
                self.ws_initialized = True
                self.gateway.write_log("即時行情 WebSocket 連接已初始化，使用低延遲模式")
                # Setup callbacks for incoming messages
                self.stock_channel.on('message', self.on_message)
                self.futopt_channel.on('message', self.on_message)
                # Start heartbeat mechanism
                self._start_heartbeat()
                return True
            return False
        except Exception as e:
            self.gateway.write_log(f"MarketProxy 連接失敗: {e}")
            return False

    def disconnect(self) -> None:
        """
        Cleanly close the WebSocket connection and clear any subscriptions or cached data.
        """
        try:
            if self.ws_initialized:
                # Unsubscribe all symbols
                self.subscribed_symbols.clear()
                self.tick_cache.clear()
                # Close WebSocket connections if SDK supports it
                self.ws_initialized = False
                self.gateway.write_log("MarketProxy WebSocket 連接已斷開")
        except Exception as e:
            self.gateway.write_log(f"MarketProxy 斷開連接失敗: {e}")

    def subscribe(self, symbols: List[str]) -> bool:
        """
        Subscribe to trades and books data for the given list of symbols.
        Implements batch subscription with retry mechanism on failure.
        
        Args:
            symbols (List[str]): List of symbol identifiers (e.g., ["2330.TWSE", "0050.TWSE"]).
        
        Returns:
            bool: True if subscription is successful for at least one symbol, False otherwise.
        """
        if not self.ws_initialized:
            self.gateway.write_log("訂閱失敗：WebSocket 未初始化")
            return False

        success = False
        stock_symbols = []
        futopt_symbols = []

        # Categorize symbols by exchange type
        for symbol in symbols:
            if symbol in self.subscribed_symbols:
                self.gateway.write_log(f"已訂閱 {symbol}，跳過")
                continue
            contract = self.gateway.contracts.get(symbol)
            if not contract:
                self.gateway.write_log(f"訂閱失敗：未找到合約 {symbol}")
                continue
            if contract.exchange == Exchange.TAIFEX:
                futopt_symbols.append(symbol)
            else:
                stock_symbols.append(symbol)
            self.subscribed_symbols.add(symbol)

        # Subscribe to stock symbols with retry mechanism
        if stock_symbols:
            for attempt in range(self.max_reconnect_attempts):
                try:
                    self.stock_channel.subscribe({
                        "channel": ["trades", "books"],
                        "symbols": stock_symbols
                    })
                    self.gateway.write_log(f"成功訂閱股票行情：{stock_symbols}")
                    success = True
                    break
                except Exception as e:
                    self.gateway.write_log(f"股票行情訂閱失敗 (嘗試 {attempt+1}/{self.max_reconnect_attempts}): {e}")
                    if attempt == self.max_reconnect_attempts - 1:
                        for symbol in stock_symbols:
                            self.subscribed_symbols.discard(symbol)
                    time.sleep(self.reconnect_interval)

        # Subscribe to futures/options symbols with retry mechanism
        if futopt_symbols:
            for attempt in range(self.max_reconnect_attempts):
                try:
                    self.futopt_channel.subscribe({
                        "channel": ["trades", "books"],
                        "symbols": futopt_symbols
                    })
                    self.gateway.write_log(f"成功訂閱期貨/選擇權行情：{futopt_symbols}")
                    success = True
                    break
                except Exception as e:
                    self.gateway.write_log(f"期貨/選擇權行情訂閱失敗 (嘗試 {attempt+1}/{self.max_reconnect_attempts}): {e}")
                    if attempt == self.max_reconnect_attempts - 1:
                        for symbol in futopt_symbols:
                            self.subscribed_symbols.discard(symbol)
                    time.sleep(self.reconnect_interval)

        return success

    def unsubscribe(self, symbols: List[str]) -> bool:
        """
        Unsubscribe from trades and books data for the given list of symbols.
        
        Args:
            symbols (List[str]): List of symbol identifiers to unsubscribe from.
        
        Returns:
            bool: True if unsubscription is successful for at least one symbol, False otherwise.
        """
        if not self.ws_initialized:
            self.gateway.write_log("取消訂閱失敗：WebSocket 未初始化")
            return False

        success = False
        stock_symbols = []
        futopt_symbols = []

        # Categorize symbols by exchange type
        for symbol in symbols:
            if symbol not in self.subscribed_symbols:
                self.gateway.write_log(f"未訂閱 {symbol}，無需取消")
                continue
            contract = self.gateway.contracts.get(symbol)
            if not contract:
                self.gateway.write_log(f"取消訂閱失敗：未找到合約 {symbol}")
                continue
            if contract.exchange == Exchange.TAIFEX:
                futopt_symbols.append(symbol)
            else:
                stock_symbols.append(symbol)

        # Unsubscribe from stock symbols
        if stock_symbols:
            try:
                self.stock_channel.unsubscribe({"symbols": stock_symbols})
                self.gateway.write_log(f"成功取消訂閱股票行情：{stock_symbols}")
                for symbol in stock_symbols:
                    self.subscribed_symbols.discard(symbol)
                success = True
            except Exception as e:
                self.gateway.write_log(f"股票行情取消訂閱失敗: {e}")

        # Unsubscribe from futures/options symbols
        if futopt_symbols:
            try:
                self.futopt_channel.unsubscribe({"symbols": futopt_symbols})
                self.gateway.write_log(f"成功取消訂閱期貨/選擇權行情：{futopt_symbols}")
                for symbol in futopt_symbols:
                    self.subscribed_symbols.discard(symbol)
                success = True
            except Exception as e:
                self.gateway.write_log(f"期貨/選擇權行情取消訂閱失敗: {e}")

        return success

    def on_message(self, ws, raw: str) -> None:
        """
        Callback for processing incoming WebSocket messages.
        Parses JSON data, converts to TickData, and pushes to queue for event engine.
        
        Args:
            ws: WebSocket instance (provided by SDK).
            raw (str): Raw JSON message string from WebSocket.
        """
        try:
            data = json.loads(raw)
            event_type = data.get('event', '')
            symbol = data.get('symbol', '')
            contract = self.gateway.contracts.get(symbol)
            if not contract:
                self.gateway.write_log(f"消息處理失敗：未找到合約 {symbol}")
                return

            if event_type == 'trade' and 'price' in data and 'size' in data and 'time' in data:
                tick = TickData(
                    symbol=symbol,
                    exchange=contract.exchange,
                    last_price=float(data['price']),
                    last_volume=float(data['size']),
                    datetime=datetime.fromtimestamp(data['time'], tz=TAIPEI_TZ),
                    gateway_name=self.gateway.gateway_name
                )
                self.tick_cache[symbol] = tick
                self.queue.sync_q.put_nowait(tick)
            elif event_type == 'book' and 'bids' in data and 'asks' in data:
                tick = self.tick_cache.get(symbol)
                if tick:
                    # Update bid and ask data for up to 5 levels
                    for i in range(min(5, len(data['bids']))):
                        setattr(tick, f'bid_price_{i+1}', float(data['bids'][i].get('price', 0.0)))
                        setattr(tick, f'bid_volume_{i+1}', float(data['bids'][i].get('size', 0.0)))
                    for i in range(min(5, len(data['asks']))):
                        setattr(tick, f'ask_price_{i+1}', float(data['asks'][i].get('price', 0.0)))
                        setattr(tick, f'ask_volume_{i+1}', float(data['asks'][i].get('size', 0.0)))
                    self.queue.sync_q.put_nowait(tick)
        except Exception as e:
            self.gateway.write_log(f"即時數據處理錯誤: {e}")

    def on_tick(self, tick: TickData) -> None:
        """
        Push the processed TickData to the vn.py event engine.
        Called after parsing and queue processing to ensure non-blocking operation.
        
        Args:
            tick (TickData): The processed market data object.
        """
        self.gateway.on_tick(tick)

    def _start_heartbeat(self):
        """
        Start a background thread to send periodic heartbeat messages to maintain WebSocket connection.
        """
        def beat():
            while self.ws_initialized:
                try:
                    self.sdk.marketdata.websocket_client.ping()
                    self.gateway.write_log("WebSocket 心跳已發送")
                except Exception as e:
                    self.gateway.write_log(f"心跳失敗: {e}")
                    self._attempt_reconnect()
                time.sleep(30)
        Thread(target=beat, daemon=True).start()

    def _attempt_reconnect(self):
        """
        Attempt to reconnect to the WebSocket server if connection is lost.
        """
        if self.reconnect_attempts < self.max_reconnect_attempts:
            self.reconnect_attempts += 1
            self.gateway.write_log(f"WebSocket 重連嘗試 {self.reconnect_attempts}/{self.max_reconnect_attempts}")
            time.sleep(self.reconnect_interval)
            if self.connect():
                self.gateway.write_log("WebSocket 重連成功，重新訂閱符號")
                self.subscribe(list(self.subscribed_symbols))
                self.reconnect_attempts = 0
            else:
                self.gateway.write_log("WebSocket 重連失敗")
        else:
            self.gateway.write_log("WebSocket 重連失敗，超過最大次數")

class ContractLoader:
    """負責從 Fubon Neo API 載入合約數據"""
    def __init__(self, sdk: FubonSDK, gateway_name: str):
        self.sdk = sdk
        self.gateway_name = gateway_name
        self.max_retries = 3
        self.retry_delay = 2  # seconds

    def fetch_with_retry(self, url: str) -> dict:
        for attempt in range(self.max_retries):
            try:
                response = self.sdk.marketdata.get(url)
                if response and 'data' in response:
                    return response
                else:
                    print(f"API 回應無效，URL: {url}，重試 {attempt+1}/{self.max_retries}")
                    time.sleep(self.retry_delay)
            except Exception as e:
                print(f"API 請求失敗，URL: {url}，錯誤: {e}，重試 {attempt+1}/{self.max_retries}")
                time.sleep(self.retry_delay)
        return {}

    def load_stock_contracts(self) -> List[ContractData]:
        response = self.fetch_with_retry("/v1/market/stock/tickers")
        contracts = []
        if response and 'data' in response:
            for item in response['data']:
                symbol = item.get('symbol', '')
                name = item.get('name', '')
                exchange_str = item.get('market_type', '')
                product_str = item.get('market_type', '')

                if not symbol or not exchange_str:
                    continue

                exchange = MARKET_TYPE_EXCHANGE_MAP.get(exchange_str, Exchange.TWSE)
                product = MARKET_TYPE_PRODUCT_MAP.get(product_str, Product.EQUITY)

                contract = ContractData(
                    symbol=symbol,
                    exchange=exchange,
                    name=name,
                    product=product,
                    size=1.0,  # Default size for stocks
                    pricetick=0.01,  # Default price tick for stocks
                    min_volume=1.0,  # Default minimum volume for stocks
                    gateway_name=self.gateway_name
                )
                contracts.append(contract)
        return contracts

    def load_futopt_contracts(self) -> List[ContractData]:
        response = self.fetch_with_retry("/v1/market/future/contractList")
        contracts = []
        if response and 'data' in response:
            for item in response['data']:
                symbol = item.get('symbol', '')
                name = item.get('name', '')
                market_type = item.get('market_type', '')
                multiplier = item.get('multiplier', 1.0)
                tick_size = item.get('tick_size', 0.01)

                if not symbol or not market_type:
                    continue

                exchange = FUTOPT_MARKET_TYPE_MAP.get(market_type, Exchange.TAIFEX)
                product = Product.FUTURES if 'Future' in market_type else Product.OPTION

                try:
                    ticker_info = self.sdk.marketdata.rest_client.futopt.intraday.ticker(symbol=symbol)
                    if not ticker_info or not ticker_info.get('is_tradable', False):
                        continue
                except Exception as e:
                    print(f"無法驗證合約 {symbol} 是否可交易: {e}")
                    continue

                contract = ContractData(
                    symbol=symbol,
                    exchange=exchange,
                    name=name,
                    product=product,
                    size=float(multiplier),
                    pricetick=float(tick_size),
                    min_volume=1.0,  # Default minimum volume for futures/options
                    gateway_name=self.gateway_name
                )
                contracts.append(contract)
        return contracts

class AccountMgr:
    """負責管理帳戶和持倉查詢"""
    def __init__(self, sdk: FubonSDK, accounts: List[Any]):
        self.sdk = sdk
        self.accounts = accounts
        self.gateway_name = "FUBON"

    def query_accounts(self) -> List[AccountData]:
        accounts_data = []
        for acc in self.accounts:
            try:
                if "STOCK" in acc.account.upper():
                    inv = self.sdk.stock.bank_remains(acc)
                    ad = AccountData(
                        accountid=f"{acc.account}.FUBON",
                        balance=float(inv.data.total_balance) if hasattr(inv.data, 'total_balance') else 0.0,
                        frozen=float(inv.data.frozen_balance) if hasattr(inv.data, 'frozen_balance') else 0.0,
                        available=float(inv.data.available_balance) if hasattr(inv.data, 'available_balance') else 0.0,
                        gateway_name=self.gateway_name
                    )
                    accounts_data.append(ad)
                elif "FUTOPT" in acc.account.upper():
                    # Placeholder for futures/options account query
                    inv = self.sdk.futopt.account_balance(acc)
                    ad = AccountData(
                        accountid=f"{acc.account}.FUBON",
                        balance=float(inv.data.balance) if hasattr(inv.data, 'balance') else 0.0,
                        frozen=float(inv.data.frozen) if hasattr(inv.data, 'frozen') else 0.0,
                        available=float(inv.data.available) if hasattr(inv.data, 'available') else 0.0,
                        gateway_name=self.gateway_name
                    )
                    accounts_data.append(ad)
            except Exception as e:
                print(f"帳戶查詢錯誤 for {acc.account}: {e}")
        return accounts_data

    def query_positions(self) -> List[PositionData]:
        positions = []
        for acc in self.accounts:
            try:
                if "STOCK" in acc.account.upper():
                    inv = self.sdk.stock.inventories(acc)
                    for item in inv.data:
                        pd = PositionData(
                            symbol=item.stock_no if hasattr(item, 'stock_no') else '',
                            exchange=Exchange.TWSE,
                            direction=Direction.LONG,
                            volume=float(item.today_qty) if hasattr(item, 'today_qty') else 0.0,
                            frozen=float(item.tradable_qty) if hasattr(item, 'tradable_qty') else 0.0,
                            price=float(item.cost_price) if hasattr(item, 'cost_price') else 0.0,
                            pnl=float(item.unrealized_gain) if hasattr(item, 'unrealized_gain') else 0.0,
                            yd_volume=0.0,
                            gateway_name=self.gateway_name
                        )
                        positions.append(pd)
                elif "FUTOPT" in acc.account.upper():
                    # Placeholder for futures/options position query
                    inv = self.sdk.futopt.positions(acc)
                    for item in inv.data:
                        pd = PositionData(
                            symbol=item.symbol if hasattr(item, 'symbol') else '',
                            exchange=Exchange.TAIFEX,
                            direction=DIRECTION_MAP_REVERSE.get(item.direction, Direction.LONG) if hasattr(item, 'direction') else Direction.LONG,
                            volume=float(item.quantity) if hasattr(item, 'quantity') else 0.0,
                            frozen=0.0,
                            price=float(item.avg_price) if hasattr(item, 'avg_price') else 0.0,
                            pnl=float(item.unrealized_pnl) if hasattr(item, 'unrealized_pnl') else 0.0,
                            yd_volume=0.0,
                            gateway_name=self.gateway_name
                        )
                        positions.append(pd)
            except Exception as e:
                print(f"持倉查詢錯誤 for {acc.account}: {e}")
        return positions
