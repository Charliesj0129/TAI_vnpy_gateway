# shioaji_session_manager.py (New file or add to the above)
from typing import Dict, Any, Optional, List, Tuple, Set
from datetime import datetime
from threading import Lock, Thread
from queue import Queue, Full
import time

from vnpy.trader.event import EVENT_LOG, EVENT_CONTRACT
from vnpy.trader.object import BarData
from vnpy.trader.gateway import BaseGateway
from vnpy.event import EventEngine
from vnpy.trader.object import (
    LogData, ContractData, TickData, OrderData, TradeData, 
    PositionData, AccountData, SubscribeRequest, OrderRequest, 
    CancelRequest, HistoryRequest
)
from vnpy.trader.constant import Status          # Status is usually in vnpy.trader.constant
from vnpy.event import Event 
from vnpy.trader.utility import load_json
from shioaji_session_handler import ShioajiSessionHandler # If in separate file
from shioaji_session_handler import (
    ShioajiSessionHandler, 
    EVENT_SUBSCRIBE_SUCCESS, 
    EVENT_SUBSCRIBE_FAILED,
    EVENT_CONTRACTS_LOADED
)
from vnpy.trader.event import EVENT_TIMER  

# Define constants for manager events if needed (e.g. session connect/disconnect)
EVENT_SJ_MANAGER_SESSION_CONNECTED = "eSJManagerSessionConnected"
EVENT_SJ_MANAGER_SESSION_DISCONNECTED = "eSJManagerSessionDisconnected"



class ShioajiSessionManager(BaseGateway):
    default_name = "SHIOAJI_MULTI"

    def __init__(self, event_engine: EventEngine, gateway_name: Optional[str] = None):
        super().__init__(event_engine, gateway_name or self.default_name)
        
        self.handlers: Dict[str, ShioajiSessionHandler] = {}
        #self.vt_symbol_to_vnpy_account_id: Dict[str, str] = {}
        # self.global_orderid_map: Dict[str, Tuple[str, str]] = {} # Consider if needed with prefix-based routing
        # self.order_id_counter = 0
        # self.order_id_lock = Lock()

         # --- 全局合約快取 ---
        self.global_contracts: Dict[str, ContractData] = {}
        self.global_contracts_lock: Lock = Lock()
        self.primary_contract_handler_id: Optional[str] = None
        self.initial_contracts_loaded: bool = False
        # --- END 全局合約快取 ---

        # --- 行情訂閱管理 ---
        self.subscribed_api_level: Dict[str, str] = {}  # vt_symbol -> vnpy_account_id of handler
        self.subscription_references: Dict[str, Set[str]] = {} # vt_symbol -> set of requester_ids
        self.subscription_lock: Lock = Lock()
        # --- END 行情訂閱管理 ---

        self.manager_setting_filename = "shioaji_manager_connect.json"
        self.query_timer_interval: int = 0
        self.query_timer_count: int = 0
        # --- End Timer Attributes ---
        self.default_order_account_id: Optional[str] = None
        self.connect_delay_seconds: float = 1.0 # 預設延遲1秒

        # --- Event queue for decoupled handler communication ---
        self._event_queue: Queue = Queue(maxsize=10000)
        self._consumer_thread: Optional[Thread] = None
        self._consumer_running: bool = False

        # Note: _register_timer() will be called after settings are loaded in connect()

    def connect(self, setting: Optional[Dict] = None):
        self.write_log("正在初始化 Shioaji Session Manager...")

        config_data = load_json(self.manager_setting_filename)
        if not config_data:
            self.write_log(f"無法從 {self.manager_setting_filename} 載入設定。Manager 無法啟動。", level="error")
            return

        manager_settings = config_data.get("manager_settings", {})
        if isinstance(manager_settings, dict):
            self.query_timer_interval = int(manager_settings.get("query_timer_interval", 0))
            if self.query_timer_interval < 0: self.query_timer_interval = 0
            self.write_log(f"Manager 定時查詢間隔設為: {self.query_timer_interval} 秒 (0 表示禁用)。")
            
            self.primary_contract_handler_id = manager_settings.get("primary_contract_handler_id")
            if self.primary_contract_handler_id:
                self.write_log(f"指定的主合約處理 Handler ID: {self.primary_contract_handler_id}")
            else:
                self.write_log("未指定主合約處理 Handler ID，將由第一個成功連接的 Handler 處理初始合約。")

            self.default_order_account_id = manager_settings.get("default_order_account_id")
            if self.default_order_account_id:
                self.write_log(f"指定的預設下單帳戶 ID: {self.default_order_account_id}")
            else:
                self.write_log("未指定預設下單帳戶 ID，將在需要時嘗試選擇第一個可用的 Handler。")

            # 新增：從設定檔讀取帳戶連接延遲時間
            self.connect_delay_seconds = float(manager_settings.get("connect_delay_seconds", 1.0))
            if self.connect_delay_seconds < 0:
                self.connect_delay_seconds = 0 # 延遲不能為負
            self.write_log(f"每個帳戶連接之間的延遲時間設為: {self.connect_delay_seconds} 秒。")

        else:
            self.write_log("'manager_settings' 設定無效。使用預設定時器間隔 (禁用) 和預設連接延遲 (1秒)。", level="warning")
            self.query_timer_interval = 0
            self.default_order_account_id = None
            self.connect_delay_seconds = 1.0


        session_configs = config_data.get("session_configs", [])
        if not session_configs or not isinstance(session_configs, list):
            self.write_log(f"在 {self.manager_setting_filename} 中未找到 'session_configs' 或格式無效。將不會連接任何 session。", level="warning")
        else:
            first_configured_handler_id: Optional[str] = None 
            for i, sess_conf in enumerate(session_configs):
                # *** 新增：在連接每個 Handler 之前加入延遲 ***
                if i > 0 and self.connect_delay_seconds > 0: # 從第二個 Handler 開始，且延遲大於0
                    self.write_log(f"等待 {self.connect_delay_seconds} 秒後再連接下一個帳戶...")
                    time.sleep(self.connect_delay_seconds)

                vnpy_account_id = sess_conf.get("vnpy_account_id")
                if not vnpy_account_id:
                    self.write_log(f"Session 設定 #{i} 缺少 'vnpy_account_id'，已跳過。", level="warning")
                    continue
                
                if not first_configured_handler_id: 
                    first_configured_handler_id = vnpy_account_id

                if vnpy_account_id in self.handlers:
                    self.write_log(f"vnpy_account_id '{vnpy_account_id}' 的 Session 已存在或重複設定，已跳過。", level="warning")
                    continue

                handler_gateway_name = f"{self.gateway_name}.{vnpy_account_id}"
                try:
                    is_primary_contract_h = (self.primary_contract_handler_id == vnpy_account_id) or \
                                     (not self.primary_contract_handler_id and not self.handlers)
                    handler = ShioajiSessionHandler(
                        manager_event_callback=self._enqueue_event_from_handler,
                        gateway_name=handler_gateway_name,
                        vnpy_account_id=vnpy_account_id
                    )
                    self.handlers[vnpy_account_id] = handler
                    self.write_log(f"正在連接 vnpy_account_id: {vnpy_account_id} 的 session... (Primary Contract Handler: {is_primary_contract_h})")
                    if is_primary_contract_h and not self.primary_contract_handler_id:
                        self.primary_contract_handler_id = vnpy_account_id
                        self.write_log(f"將 Handler {vnpy_account_id} 設為實際的主合約處理者。")
                    handler.connect(sess_conf) # connect 方法本身是異步啟動連接線程的
                except Exception as e:
                    self.write_log(f"為 {vnpy_account_id} 創建或連接 handler 失敗: {e}", level="error")
                    if vnpy_account_id in self.handlers: del self.handlers[vnpy_account_id]
                    if self.primary_contract_handler_id == vnpy_account_id:
                        self.primary_contract_handler_id = None
                        self.initial_contracts_loaded = False
            
            if not self.default_order_account_id and first_configured_handler_id:
                self.default_order_account_id = first_configured_handler_id
                self.write_log(f"由於未在設定檔中指定，將使用配置中的第一個帳戶 {self.default_order_account_id} 作為預設下單帳戶的備選。")

        self._register_timer()
        self._start_event_consumer()


    def _register_timer(self) -> None:
        """Registers the periodic query timer if interval is set."""
        if self.query_timer_interval > 0 and self.event_engine: # Ensure event_engine is available
            self.event_engine.register(EVENT_TIMER, self.process_timer_event)
            self.write_log(f"Manager periodic query timer registered (Interval: {self.query_timer_interval}s).")

    def _unregister_timer(self) -> None:
        """Unregisters the periodic query timer."""
        if self.query_timer_interval > 0 and self.event_engine:
            try:
                self.event_engine.unregister(EVENT_TIMER, self.process_timer_event)
                self.write_log("Manager periodic query timer unregistered.")
            except Exception as e: # MainEngine might raise error if already unregistered
                self.write_log(f"Error unregistering timer (might be already unregistered): {e}", level="warning")

    def _enqueue_event_from_handler(self, event_type: str, data: Any, origin: str) -> None:
        """Place handler events into the manager's queue for async processing."""
        try:
            self._event_queue.put_nowait((event_type, data, origin))
        except Full:
            self.write_log("Manager event queue full, dropping event.", level="warning")

    def _start_event_consumer(self) -> None:
        """Launch a background thread to consume events from handlers."""
        if self._consumer_thread and self._consumer_thread.is_alive():
            return

        self._consumer_running = True
        self._consumer_thread = Thread(target=self._event_consumer, daemon=True)
        self._consumer_thread.start()

    def _event_consumer(self) -> None:
        while self._consumer_running:
            item = self._event_queue.get()
            if item is None:
                break
            self._handle_event_from_handler(*item)


    def process_timer_event(self, event: Event) -> None: # event type is vnpy.event.Event
        """
        Processes the EVENT_TIMER. 
        Periodically triggers account and position queries for all connected handlers.
        """
        self.query_timer_count += 1
        
        if self.query_timer_count % self.query_timer_interval == 0:
            self.write_log("Manager timer: Triggering periodic query for accounts and positions.")
            
            # Check if there are any connected handlers before querying
            any_handler_connected = any(h.connected for h in self.handlers.values())

            if not any_handler_connected:
                self.write_log("Manager timer: No handlers connected, skipping periodic query.", level="debug")
                return

            try:
                self.query_account()  # This manager method will iterate through handlers
                self.query_position() # This manager method will iterate through handlers
            except Exception as e:
                self.write_log(f"Manager timer: Error during periodic query: {e}", level="error")



    def _handle_event_from_handler(
        self, 
        event_type: str, 
        data: Any, 
        vnpy_account_id_origin: str # The vnpy_account_id of the handler that sent the event
    ):
        """
        Callback method passed to each ShioajiSessionHandler.
        This method receives events from handlers and pushes them to VnPy's main EventEngine.
        It also handles any necessary transformations (e.g., globalizing IDs).
        """
        # Ensure the data has the manager's gateway_name if it's a VnPy object
        if hasattr(data, "gateway_name"):
            data.gateway_name = self.gateway_name
        
        # Ensure accountid is correctly set for objects that have it
        if hasattr(data, "accountid") and not getattr(data, "accountid", None):
            data.accountid = vnpy_account_id_origin

        if event_type == "tick":
            super().on_tick(data) # data is TickData
        elif event_type == "order":
            # OrderID might need to be globalized if strategies see manager as one gateway
            # For now, assume handler's order ID is prefixed with handler's unique gateway_name
            super().on_order(data) # data is OrderData
        elif event_type == "trade":
            super().on_trade(data) # data is TradeData
        elif event_type == "account":
            super().on_account(data) # data is AccountData
        elif event_type == "position":
            super().on_position(data) # data is PositionData
        elif event_type == "contract":
            if isinstance(data, ContractData):
                contract_event_processed = False
                # 只有主合約 Handler 的合約，或者在主合約 Handler 加載完成後其他 Handler 發現的新合約，才進行處理
                is_from_primary_handler = (vnpy_account_id_origin == self.primary_contract_handler_id)

                with self.global_contracts_lock:
                    existing_contract = self.global_contracts.get(data.vt_symbol)
                    needs_update = True # 預設需要更新 (即推送到 MainEngine)

                    if existing_contract:
                        # 如果合約已存在，檢查是否真的有變化 (這裡可以根據需要實現更詳細的比較邏輯)
                        # 簡單比較：如果名稱、交易所、產品、大小、價格精度任一不同，則視為更新
                        if (existing_contract.name == data.name and
                            existing_contract.exchange == data.exchange and
                            existing_contract.product == data.product and
                            abs(existing_contract.size - data.size) < 1e-6 and # 浮點數比較
                            abs(existing_contract.pricetick - data.pricetick) < 1e-6):
                            needs_update = False # 沒有顯著變化，不需要更新
                        
                        if not needs_update and not is_from_primary_handler:
                             # 如果是來自非主 Handler 的無變化合約，則直接忽略，不覆蓋主 Handler 的數據
                            #self.write_log(f"來自非主 Handler {vnpy_account_id_origin} 的重複合約 {data.vt_symbol}，已忽略。", level="debug")
                            contract_event_processed = True # 標記為已處理 (忽略也是一種處理)
                        elif needs_update:
                            self.write_log(f"更新全局快取中的合約 {data.vt_symbol} (來源: {vnpy_account_id_origin})。", level="info")
                    
                    if needs_update and not contract_event_processed: # 只有需要更新且未被忽略時才操作
                        self.global_contracts[data.vt_symbol] = data
                        # 推送 ContractData 到 VnPy 的主事件引擎
                        event = Event(EVENT_CONTRACT, data) # 使用 VnPy 標準的 EVENT_CONTRACT
                        self.event_engine.put(event)
                        #self.write_log(f"已推送合約 {data.vt_symbol} (來源: {vnpy_account_id_origin}) 至 MainEngine。", level="debug")
                    
                    # 標記初始合約已載入 (如果來自主 Handler 且這是第一次)
                    if is_from_primary_handler and not self.initial_contracts_loaded:
                        # 這裡可以設定一個更精確的條件，例如當主 Handler 推送完一定數量的合約後
                        # 為簡化，假設主 Handler 推送第一個合約時就認為初始載入開始
                        # 或者，在主 Handler 的 _connect_worker 成功處理完 _process_contracts 後，
                        # 主 Handler 可以發送一個特定事件給 Manager，Manager 收到後再設置 self.initial_contracts_loaded = True
                        pass # self.initial_contracts_loaded 的管理可能需要更細緻的信號

                contract_event_processed = True # 標記事件已處理

            if not contract_event_processed: # 如果上面的邏輯沒有處理 (例如 data 不是 ContractData)
                 self.write_log(f"收到未處理的 'contract' 類型事件: {data}", level="warning")
            # --- END 合約處理邏輯 ---
            super().on_contract(data) # Push to main EventEngine
        elif event_type == "log": # Assuming handler might send LogData
            if isinstance(data, LogData):
                 data.gateway_name = self.gateway_name # Ensure manager's name
                 log_event = Event(type=EVENT_LOG, data=data)
                 self.event_engine.put(log_event)
            else: # Or just a string message
                 self.write_log(f"Msg from {vnpy_account_id_origin}: {data}")
        elif event_type == "event": # Generic event from handler (e.g. subscribe success/fail)
            original_event_type = data.get("type")
            original_data = data.get("data") # 這通常是 vt_symbol

            if original_event_type == EVENT_SUBSCRIBE_SUCCESS:
                vt_symbol_subscribed = original_data
                with self.subscription_lock:
                    # 只有當 Manager 認為這個 Handler 應該是負責 API 訂閱的時候，才更新 subscribed_api_level
                    # 這種情況發生在 Manager 的 subscribe 方法中選擇了此 Handler 進行 API 訂閱之後
                    # 並且此 Handler 成功回報了 API 訂閱成功
                    
                    # 檢查是否已有其他 Handler 意外地訂閱了此行情 (理論上不應發生，如果 Manager 邏輯正確)
                    if vt_symbol_subscribed in self.subscribed_api_level and \
                       self.subscribed_api_level[vt_symbol_subscribed] != vnpy_account_id_origin:
                        self.write_log(
                            f"警告：Handler {vnpy_account_id_origin} 報告 {vt_symbol_subscribed} 訂閱成功，"
                            f"但 Manager 記錄的負責 Handler 是 {self.subscribed_api_level[vt_symbol_subscribed]}。"
                            "可能存在訂閱邏輯衝突。", level="critical"
                        )
                        # 此處可以選擇是否覆蓋，或者堅持 Manager 的決定
                        # 堅持 Manager 決定：不更新 self.subscribed_api_level
                        # 覆蓋：self.subscribed_api_level[vt_symbol_subscribed] = vnpy_account_id_origin (風險較高)
                    elif vt_symbol_subscribed not in self.subscribed_api_level:
                         # 如果 Manager 中沒有記錄，但 Handler 報成功，表示這是 Manager 剛才委派的訂閱
                        self.subscribed_api_level[vt_symbol_subscribed] = vnpy_account_id_origin
                        self.write_log(f"Manager 確認：Handler {vnpy_account_id_origin} 已成功在 API 層級訂閱 {vt_symbol_subscribed}。")
                    # else: vt_symbol_subscribed 在 subscribed_api_level 中且 Handler ID 匹配，正常情況

                # 將成功事件廣播給 VnPy 主事件引擎 (如果需要讓策略知道)
                # VnPy 本身在收到 Tick 後即認為訂閱成功，所以這個額外事件可能不是必需的
                # super().on_event(...) 或自定義事件
                self.write_log(f"{vt_symbol_subscribed} 行情訂閱成功 (經由 {vnpy_account_id_origin})")


            elif original_event_type == EVENT_SUBSCRIBE_FAILED:
                vt_symbol_failed = original_data
                self.write_log(f"{vt_symbol_failed} 行情訂閱失敗 (經由 {vnpy_account_id_origin})", level="warning")
                # 如果此 Handler 是被 Manager 指定負責 API 訂閱的，則需要清理
                with self.subscription_lock:
                    if vt_symbol_failed in self.subscribed_api_level and \
                       self.subscribed_api_level[vt_symbol_failed] == vnpy_account_id_origin:
                        self.write_log(f"由於 Handler {vnpy_account_id_origin} 未能成功訂閱 {vt_symbol_failed}，"
                                       "正在從 Manager 的 API 層級訂閱記錄中移除。", level="warning")
                        del self.subscribed_api_level[vt_symbol_failed]
                        # 注意：此時 subscription_references 中可能仍有對此 vt_symbol 的引用，
                        # 下次再有對此 vt_symbol 的訂閱請求時，Manager 會嘗試選擇另一個 Handler。
            
            elif original_event_type == EVENT_CONTRACTS_LOADED: # 來自上一問題的修改
                if vnpy_account_id_origin == self.primary_contract_handler_id and not self.initial_contracts_loaded:
                    self.initial_contracts_loaded = True
                    self.write_log(f"主合約 Handler ({vnpy_account_id_origin}) 已完成初始合約載入。")
                elif vnpy_account_id_origin != self.primary_contract_handler_id:
                    self.write_log(f"非主合約 Handler ({vnpy_account_id_origin}) 完成合約載入。", level="debug")
        elif event_type == "session_status":
            status_info = data # e.g. {"status": "disconnected_failed", "vnpy_account_id": ...}
            self.write_log(f"Session status update from {vnpy_account_id_origin}: {status_info.get('status')}", 
                           level="warning" if "failed" in status_info.get('status') else "info")
            if status_info.get('status') == "connected":
                evt = Event(EVENT_SJ_MANAGER_SESSION_CONNECTED, vnpy_account_id_origin)
                self.event_engine.put(evt)
            elif "disconnected" in status_info.get('status'):
                evt = Event(EVENT_SJ_MANAGER_SESSION_DISCONNECTED, vnpy_account_id_origin)
                self.event_engine.put(evt)


    def subscribe(self, req: SubscribeRequest, vnpy_account_id_target: Optional[str] = None) -> None:
        """
        集中管理行情訂閱請求。
        - req: SubscribeRequest 物件。
        - vnpy_account_id_target: (可選) 請求此訂閱的帳戶ID或策略標識。
                                  如果為 None，則使用一個通用標識符。
        """
        vt_symbol = req.vt_symbol
        requester_id = vnpy_account_id_target or "GLOBAL_REQUESTER" # 如果沒有指定目標帳戶，則視為全局請求

        self.write_log(f"收到行情訂閱請求: {vt_symbol}, 請求者: {requester_id}")

        with self.global_contracts_lock: # 檢查合約是否存在
            if vt_symbol not in self.global_contracts:
                self.write_log(f"無法訂閱 {vt_symbol}: 在 Manager 全局快取中未找到合約。", level="warning")
                # 可以考慮透過 _handle_event_from_handler 發送一個總體的訂閱失敗事件
                # 但由於此處是 Manager 主動拒絕，直接記錄日誌可能更合適
                # 如果需要通知策略層，則需要一個機制將此失敗傳遞回去
                # 目前 Handler 的 subscribe 失敗會通過 EVENT_SUBSCRIBE_FAILED，這裡可以類似處理
                # 但 EVENT_SUBSCRIBE_FAILED 通常由 Handler 發出，表示 API 層級的失敗
                return

        with self.subscription_lock:
            # 1. 更新引用計數
            if vt_symbol not in self.subscription_references:
                self.subscription_references[vt_symbol] = set()
            self.subscription_references[vt_symbol].add(requester_id)
            self.write_log(f"{vt_symbol} 的訂閱引用者: {self.subscription_references[vt_symbol]}")

            # 2. 檢查是否已在 API 層級訂閱
            if vt_symbol in self.subscribed_api_level:
                responsible_handler_id = self.subscribed_api_level[vt_symbol]
                self.write_log(f"{vt_symbol} 已由 Handler {responsible_handler_id} 在 API 層級訂閱。僅增加引用計數。")
                # 假設 Handler 訂閱成功後會發送 EVENT_SUBSCRIBE_SUCCESS
                # 這裡可以選擇是否為後續的相同請求者再次觸發成功事件
                # 通常，如果API已訂閱，則認為對所有新請求者也是成功的
                # handler = self.handlers.get(responsible_handler_id)
                # if handler:
                #     handler.on_event(EVENT_SUBSCRIBE_SUCCESS, vt_symbol) # 讓 handler 通知 manager，manager 再廣播
                return

            # 3. 如果未在 API 層級訂閱，則選擇一個 Handler 進行訂閱
            if not self.handlers:
                self.write_log(f"無法訂閱 {vt_symbol}: 無已啟動的 Session Handlers。", level="warning")
                # 清理剛才添加的引用
                self.subscription_references[vt_symbol].discard(requester_id)
                if not self.subscription_references[vt_symbol]:
                    del self.subscription_references[vt_symbol]
                return

            target_handler: Optional[ShioajiSessionHandler] = None
            chosen_handler_id: Optional[str] = None

            # 優先使用 vnpy_account_id_target (如果它是有效的 handler id)
            if vnpy_account_id_target and vnpy_account_id_target in self.handlers:
                handler_instance = self.handlers[vnpy_account_id_target]
                if handler_instance.connected:
                    target_handler = handler_instance
                    chosen_handler_id = vnpy_account_id_target
                    self.write_log(f"為指定帳戶 {vnpy_account_id_target} 路由 API 訂閱 {vt_symbol} 至 Handler {target_handler.gateway_name}")
                else:
                    self.write_log(f"無法為帳戶 {vnpy_account_id_target} 執行 API 訂閱 {vt_symbol}: Handler 未連線。", level="warning")
            
            # 如果沒有指定目標或指定目標無效，則使用負載均衡
            if not target_handler:
                min_subs = float('inf')
                available_handlers = [h_id for h_id, h in self.handlers.items() if h.connected]
                if not available_handlers:
                    self.write_log(f"無法執行 API 訂閱 {vt_symbol}: 無可用/已連線的 Handler。", level="warning")
                    self.subscription_references[vt_symbol].discard(requester_id)
                    if not self.subscription_references[vt_symbol]:
                        del self.subscription_references[vt_symbol]
                    return

                # 簡單的負載均衡：選擇已連接 Handler 中 API 層級訂閱數最少的
                # 注意：len(handler_instance.subscribed) 是 Handler 內部記錄的訂閱，可能與 Manager 的 subscribed_api_level 不同步
                # 更準確的負載均衡可能需要 Manager 維護每個 Handler 的 API 訂閱數
                # 此處簡化為選擇第一個可用的
                for handler_id_candidate in available_handlers:
                    handler_instance_candidate = self.handlers[handler_id_candidate]
                    # 此處的 len(handler_instance_candidate.subscribed) 是 Handler 認為它已訂閱的
                    # 我們需要的是 Manager 視角下，哪個 Handler 承載的 API 訂閱最少
                    # 為了簡化，這裡先用第一個可用的，或者您可以實現更複雜的負載均衡
                    
                    # 修正負載均衡邏輯：基於 Manager 記錄的 API 訂閱
                    current_api_subs_count = 0
                    for subscribed_vt, handler_responsible_id in self.subscribed_api_level.items():
                        if handler_responsible_id == handler_id_candidate:
                            current_api_subs_count +=1
                    
                    if current_api_subs_count < min_subs:
                        min_subs = current_api_subs_count
                        target_handler = handler_instance_candidate
                        chosen_handler_id = handler_id_candidate

                if target_handler and chosen_handler_id:
                    self.write_log(f"負載均衡：路由 API 訂閱 {vt_symbol} 至 Handler {target_handler.gateway_name} (API 訂閱數: {min_subs})")
                else: # 理論上如果 available_handlers 不為空，這裡應該能選到
                     self.write_log(f"無法透過負載均衡選擇 Handler 進行 API 訂閱 {vt_symbol}。", level="warning")
                     self.subscription_references[vt_symbol].discard(requester_id)
                     if not self.subscription_references[vt_symbol]:
                         del self.subscription_references[vt_symbol]
                     return


            if target_handler and chosen_handler_id:
                # 執行實際的 Handler 層級訂閱
                target_handler.subscribe(req) # Handler 內部會調用 on_event(EVENT_SUBSCRIBE_SUCCESS/FAILED,...)
                                            # 我們需要依賴這個事件來更新 subscribed_api_level
                # 注意：此時不立即更新 subscribed_api_level，而是等待 Handler 的 EVENT_SUBSCRIBE_SUCCESS 事件
                # 在 _handle_event_from_handler 中處理 EVENT_SUBSCRIBE_SUCCESS 時，再更新 subscribed_api_level
            else:
                # 如果最終沒有找到合適的 handler
                log_msg = f"API 訂閱 {vt_symbol} 最終失敗：無合適的 Handler 可執行。"
                self.write_log(log_msg, level="error")
                self.subscription_references[vt_symbol].discard(requester_id)
                if not self.subscription_references[vt_symbol]:
                    del self.subscription_references[vt_symbol]
                # 可以考慮發送一個 Manager 級別的失敗通知，如果 Handler 未能發出
                # self._handle_event_from_handler("event", {"type": EVENT_SUBSCRIBE_FAILED, "data": vt_symbol}, "MANAGER_GLOBAL_NO_HANDLER")

    def unsubscribe(self, req: SubscribeRequest, vnpy_account_id_target: Optional[str] = None) -> None:
        """
        處理取消訂閱請求。
        - req: SubscribeRequest 物件 (VnPy 的取消訂閱也使用 SubscribeRequest，通常 symbol 和 exchange 有效)。
        - vnpy_account_id_target: (可選) 請求取消訂閱的帳戶ID或策略標識。
        """
        vt_symbol = req.vt_symbol
        requester_id = vnpy_account_id_target or "GLOBAL_REQUESTER"

        self.write_log(f"收到行情取消訂閱請求: {vt_symbol}, 請求者: {requester_id}")

        with self.subscription_lock:
            if vt_symbol not in self.subscription_references or requester_id not in self.subscription_references[vt_symbol]:
                self.write_log(f"請求者 {requester_id} 並未訂閱 {vt_symbol}，無需取消。", level="info")
                return

            # 移除請求者的引用
            self.subscription_references[vt_symbol].discard(requester_id)
            self.write_log(f"已移除請求者 {requester_id} 對 {vt_symbol} 的訂閱引用。")

            # 如果移除後，該 vt_symbol 不再有任何引用者
            if not self.subscription_references[vt_symbol]:
                del self.subscription_references[vt_symbol] # 從引用計數中刪除
                self.write_log(f"{vt_symbol} 已無訂閱引用者。")

                # 檢查是否有 Handler 負責此 API 層級訂閱
                if vt_symbol in self.subscribed_api_level:
                    responsible_handler_id = self.subscribed_api_level.pop(vt_symbol) # 從 API 訂閱記錄中移除
                    handler_to_unsubscribe = self.handlers.get(responsible_handler_id)

                    if handler_to_unsubscribe and handler_to_unsubscribe.connected:
                        self.write_log(f"指示 Handler {responsible_handler_id} 在 API 層級取消訂閱 {vt_symbol}...")
                        try:
                            # 假設 Handler 有一個 unsubscribe 方法
                            handler_to_unsubscribe.unsubscribe(req) # Handler 內部應更新其 self.subscribed
                            self.write_log(f"已向 Handler {responsible_handler_id} 發送 API 取消訂閱 {vt_symbol} 指令。")
                        except Exception as e:
                            self.write_log(f"調用 Handler {responsible_handler_id} 的 unsubscribe 方法時出錯 for {vt_symbol}: {e}", level="error")
                            # 即使 Handler 取消失敗，也已從 Manager 的記錄中移除，下次訂閱會重新嘗試
                    elif handler_to_unsubscribe:
                        self.write_log(f"負責 {vt_symbol} API 訂閱的 Handler {responsible_handler_id} 未連接，無法執行 API 取消訂閱。", level="warning")
                    else:
                        self.write_log(f"未找到負責 {vt_symbol} API 訂閱的 Handler {responsible_handler_id} 實例。", level="warning")
                else:
                    self.write_log(f"{vt_symbol} 在 Manager 中無 API 層級訂閱記錄，無需操作 Handler。", level="debug")
            else:
                self.write_log(f"{vt_symbol} 仍有其他訂閱引用者: {self.subscription_references[vt_symbol]}。僅減少引用計數。")


    def send_order(self, req: OrderRequest) -> str:
        """
        發送訂單請求。如果 OrderRequest 中未指定 accountid，
        則嘗試使用預設帳戶進行路由。
        """
        vnpy_account_id_to_use = req.accountid
        is_default_routing = False

        if not vnpy_account_id_to_use:
            is_default_routing = True
            self.write_log("OrderRequest 中未指定 accountid，嘗試使用預設帳戶路由。", level="info")

            # 策略 1: 使用在 connect 時從設定檔讀取的 self.default_order_account_id
            if self.default_order_account_id and self.default_order_account_id in self.handlers:
                if self.handlers[self.default_order_account_id].connected:
                    vnpy_account_id_to_use = self.default_order_account_id
                    self.write_log(f"使用設定檔中指定的預設下單帳戶: {vnpy_account_id_to_use}")
                else:
                    self.write_log(f"設定檔中指定的預設下單帳戶 {self.default_order_account_id} 未連接。", level="warning")
            
            # 策略 2: 如果策略1失敗 (未配置或配置的帳戶未連接)，則嘗試使用主合約 Handler
            if not vnpy_account_id_to_use and self.primary_contract_handler_id and \
               self.primary_contract_handler_id in self.handlers and \
               self.handlers[self.primary_contract_handler_id].connected:
                vnpy_account_id_to_use = self.primary_contract_handler_id
                self.write_log(f"使用主合約 Handler 對應的帳戶 {vnpy_account_id_to_use} 作為預設下單帳戶。")

            # 策略 3: 如果以上都失敗，則選擇第一個已連接的 Handler
            if not vnpy_account_id_to_use:
                for handler_id, handler_instance in self.handlers.items():
                    if handler_instance.connected:
                        vnpy_account_id_to_use = handler_id
                        self.write_log(f"使用第一個可用的已連接帳戶 {vnpy_account_id_to_use} 作為預設下單帳戶。")
                        break
            
            if not vnpy_account_id_to_use:
                self.write_log("無可用預設帳戶進行訂單路由。訂單將被拒絕。", level="error")
                order = req.create_order_data(
                    orderid=f"{self.gateway_name}.REJ_NO_DEF_ACC_{datetime.now().strftime('%H%M%S_%f')}",
                    gateway_name=self.gateway_name
                )
                order.status = Status.REJECTED
                order.reference = "No default account available for routing"
                # req.accountid 原本是 None，order.accountid 可以保持 None 或設為特殊值
                super().on_order(order) # 使用 super() 避免進入 _handle_event_from_handler 的 accountid 檢查
                return order.vt_orderid
            
            # 將確定的預設帳戶ID更新回 OrderRequest，以便 Handler 能正確處理
            req.accountid = vnpy_account_id_to_use
            if is_default_routing:
                self.write_log(f"訂單將路由至預設帳戶: {req.accountid}")

        # --- 後續的 Handler 選擇和下單邏輯 ---
        target_handler = self.handlers.get(req.accountid) # 此時 req.accountid 必不為 None

        if target_handler and target_handler.connected:
            # 檢查合約是否存在於全局快取中 (如果 Manager 已載入合約)
            # 這一檢查確保我們只對已知的合約下單
            if self.initial_contracts_loaded: # 只有在主合約載入完成後才進行此檢查
                with self.global_contracts_lock:
                    if req.vt_symbol not in self.global_contracts:
                        error_msg = f"訂單 for {req.vt_symbol} 被拒絕: 在 Manager 全局快取中未找到合約。"
                        self.write_log(error_msg, level="error")
                        order = req.create_order_data(
                            orderid=f"{self.gateway_name}.REJ_NO_CONTRACT_{datetime.now().strftime('%H%M%S_%f')}",
                            gateway_name=self.gateway_name
                        )
                        order.status = Status.REJECTED
                        order.accountid = req.accountid # 記錄嘗試路由到的帳戶
                        order.reference = error_msg
                        super().on_order(order)
                        return order.vt_orderid
            else:
                self.write_log(f"Manager 初始合約尚未完全載入，暫不檢查訂單 {req.vt_symbol} 的合約是否存在於全局快取。", level="info")


            self.write_log(f"路由訂單 for {req.vt_symbol} 至 handler for vnpy_account_id {req.accountid}")
            # Handler 的 send_order 期望 req.accountid 與其自身的 vnpy_account_id 匹配
            # 由於我們上面已經將 req.accountid 設置為目標 Handler 的 ID，所以應該匹配
            vt_orderid = target_handler.send_order(req) # req 此時已包含正確的 accountid
            return vt_orderid
        else:
            err_msg = f"訂單 for {req.vt_symbol} 被拒絕: vnpy_account_id '{req.accountid}' "
            if not target_handler:
                err_msg += "無對應的 handler。"
            elif not target_handler.connected:
                err_msg += "對應的 handler 未連接。"
            
            self.write_log(err_msg, level="error")
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.REJ_HANDLER_ERR_{datetime.now().strftime('%H%M%S_%f')}",
                gateway_name=self.gateway_name
            )
            order.status = Status.REJECTED
            order.accountid = req.accountid # 記錄嘗試路由到的帳戶
            order.reference = err_msg
            super().on_order(order)
            return order.vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        vt_orderid_to_cancel = req.orderid
        self.write_log(f"收到撤單請求 for manager order ID: {vt_orderid_to_cancel}")

        target_handler: Optional[ShioajiSessionHandler] = None
        
        # 從 vt_orderid_to_cancel 解析出 handler 的 gateway_name 和 vnpy_account_id
        # 假設 vt_orderid 格式為 "MANAGER_GATEWAY_NAME.VNPY_ACCOUNT_ID.SHIOAJI_SEQNO"
        # 例如: "SHIOAJI_MULTI.STOCK_ACCOUNT_01.S0000123"
        
        parts = vt_orderid_to_cancel.split('.', 2) # Split max 2 times
        # parts will be like ['SHIOAJI_MULTI', 'STOCK_ACCOUNT_01', 'S0000123']
        # or ['HANDLER_GATEWAY_NAME_WITHOUT_MANAGER_PREFIX', 'SEQNO'] if handler.gateway_name was not prefixed by manager name

        # Let's assume handler.gateway_name IS "MANAGER_GATEWAY_NAME.VNPY_ACCOUNT_ID"
        # and handler.send_order returns "MANAGER_GATEWAY_NAME.VNPY_ACCOUNT_ID.SEQNO"
        
        # Simpler approach: iterate handlers and check if the req.orderid starts with handler's unique name
        found_handler_for_cancel = False
        for vnpy_acc_id, handler_instance in self.handlers.items():
            # handler_instance.gateway_name is like "SHIOAJI_MULTI.STOCK_ACCOUNT_01"
            if vt_orderid_to_cancel.startswith(handler_instance.gateway_name + "."):
                target_handler = handler_instance
                if target_handler.connected:
                    self.write_log(f"路由撤單請求 {vt_orderid_to_cancel} 至 Handler {target_handler.gateway_name}")
                    # req 包含的 orderid (vt_orderid_to_cancel) 已經是 Handler 能夠識別的 ID
                    target_handler.cancel_order(req) 
                    found_handler_for_cancel = True
                    break
                else:
                    self.write_log(f"無法撤銷訂單 {vt_orderid_to_cancel}: Handler {target_handler.gateway_name} 未連線。", level="warning")
                    # 應考慮是否要更新本地 OrderData 的 reference，但通常是等待回調
                    found_handler_for_cancel = True # Found but cannot process
                    break
        
        if not found_handler_for_cancel:
            self.write_log(f"撤單請求 {vt_orderid_to_cancel} 失敗: 未找到能處理此 OrderID 的 Handler，或 OrderID 格式不符。", level="error")
            # 如果需要，可以嘗試從 self.global_orderid_map (如果實現了更複雜的ID映射) 查找
            # 並創建一個代表「撤單指令被拒絕」的 OrderData 更新 (但這比較複雜)
            # 通常，如果找不到 Handler，表示這個 orderid 有問題或系統狀態不一致。


    def query_account(self) -> None: # Query all connected handlers
        self.write_log("Manager: Querying account details for all active sessions.")
        for vnpy_account_id, handler in self.handlers.items():
            if handler.connected:
                self.write_log(f"Manager: Requesting account query from handler for {vnpy_account_id}")
                handler.query_account() # Handler will call _handle_event_from_handler with AccountData

    def query_position(self) -> None: # Query all connected handlers
        self.write_log("Manager: Querying position details for all active sessions.")
        for vnpy_account_id, handler in self.handlers.items():
            if handler.connected:
                self.write_log(f"Manager: Requesting position query from handler for {vnpy_account_id}")
                handler.query_position() # Handler will call _handle_event_from_handler with PositionData

    def query_history(self, req: HistoryRequest) -> Optional[List[BarData]]:
        self.write_log(
            f"Manager: Received history query for {req.vt_symbol} from {req.start} to {req.end}, Interval: {req.interval.value}"
        )

        target_handler: Optional[ShioajiSessionHandler] = None
        
        # 策略 1: 嘗試根據 self.vt_symbol_to_vnpy_account_id 找到訂閱了該行情的 Handler
        # 這假設行情訂閱與歷史數據查詢應由同一個帳戶/連線處理
        if req.vt_symbol in self.vt_symbol_to_vnpy_account_id:
            vnpy_account_id_target = self.vt_symbol_to_vnpy_account_id[req.vt_symbol]
            if vnpy_account_id_target in self.handlers and self.handlers[vnpy_account_id_target].connected:
                target_handler = self.handlers[vnpy_account_id_target]
                self.write_log(f"Manager: Routing history query for {req.vt_symbol} to handler for vnpy_account_id {vnpy_account_id_target} (based on subscription).")
            else:
                self.write_log(f"Manager: Handler for {vnpy_account_id_target} (subscribed to {req.vt_symbol}) not connected or found. Will try default.", level="warning")
        
        # 策略 2: 如果策略1失敗，或者沒有訂閱信息，則使用第一個可用的已連接 Handler
        if not target_handler:
            if not self.handlers:
                self.write_log(f"Manager: Cannot query history for {req.vt_symbol}, no handlers available.", level="error")
                return None
            
            for handler_id, handler_instance in self.handlers.items():
                if handler_instance.connected:
                    target_handler = handler_instance
                    self.write_log(f"Manager: Routing history query for {req.vt_symbol} to first available connected handler: {handler_instance.gateway_name}", level="info")
                    break # 使用第一個找到的
            
            if not target_handler:
                self.write_log(f"Manager: Cannot query history for {req.vt_symbol}, no connected handlers available.", level="error")
                return None
        
        # 現在 target_handler 應該是一個已連接的 ShioajiSessionHandler 實例
        try:
            return target_handler.query_history(req) # 直接調用 Handler 的 query_history
        except Exception as e:
            self.write_log(f"Manager: Error calling query_history on handler {target_handler.gateway_name} for {req.vt_symbol}: {e}", level="error")
            return None
        
    def close(self) -> None:
        self.write_log("Closing Shioaji Session Manager...")
        self._unregister_timer() # <<< ADDED: Unregister timer first

        if self._consumer_running:
            self._consumer_running = False
            self._event_queue.put(None)
            if self._consumer_thread:
                self._consumer_thread.join()
                self._consumer_thread = None

        for vnpy_account_id, handler in self.handlers.items():
            self.write_log(f"Closing handler for {vnpy_account_id}...")
            try:
                handler.close()
            except Exception as e:
                self.write_log(f"Error closing handler for {vnpy_account_id}: {e}", level="error")
        self.handlers.clear()
        self.write_log("All session handlers closed. Shioaji Session Manager shut down.")


    def write_log(self, msg: str, level: str = "info"): # Overload BaseGateway's write_log or use custom
        log = LogData(msg=msg, gateway_name=self.gateway_name)
        # Assuming 'level' can be used by logger; VnPy's LogData doesn't have level by default.
        # For simplicity, just put to event engine.
        event = Event(type=EVENT_LOG, data=log) # Ensure EVENT_LOG is imported
        self.event_engine.put(event)

    def _select_new_primary_contract_handler(self) -> Optional[str]:
        """選擇一個新的主合約 Handler (如果當前主 Handler 斷線)。"""
        for handler_id, handler_instance in self.handlers.items():
            if handler_instance.connected: # 選擇第一個已連接的作為新的主 Handler
                return handler_id
        return None