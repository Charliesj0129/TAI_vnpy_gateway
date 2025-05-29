# shioaji_session_manager.py (New file or add to the above)
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from threading import Lock

from vnpy.trader.event import EVENT_LOG
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
    EVENT_SUBSCRIBE_FAILED
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
        self.vt_symbol_to_vnpy_account_id: Dict[str, str] = {}
        # self.global_orderid_map: Dict[str, Tuple[str, str]] = {} # Consider if needed with prefix-based routing
        # self.order_id_counter = 0
        # self.order_id_lock = Lock()

        self.manager_setting_filename = "shioaji_manager_connect.json"
        
        # --- Timer Attributes ---
        self.query_timer_interval: int = 0  # Default to 0 (disabled). Read from config.
        self.query_timer_count: int = 0     # Counter for timer ticks.
        # --- End Timer Attributes ---

        # Note: _register_timer() will be called after settings are loaded in connect()

    def connect(self, setting: Optional[Dict] = None): # `setting` here is for VnPy's overall settings, not used by this manager directly for its file path
        """
        Connects all sessions defined in the manager's setting file.
        Also reads manager-specific settings like timer interval.
        """
        self.write_log("Initializing Shioaji Session Manager...")
        
        # Load the entire configuration structure
        config_data = load_json(self.manager_setting_filename)
        if not config_data:
            self.write_log(f"Failed to load configuration from {self.manager_setting_filename}. Manager cannot start.", level="error")
            return

        # --- Read Manager-Specific Settings ---
        manager_settings = config_data.get("manager_settings", {})
        if isinstance(manager_settings, dict):
            self.query_timer_interval = int(manager_settings.get("query_timer_interval", 0)) # Default to 0 if not found
            if self.query_timer_interval < 0: # Ensure non-negative
                self.query_timer_interval = 0
            self.write_log(f"Manager query timer interval set to: {self.query_timer_interval} seconds (0 means disabled).")
        else:
            self.write_log("'manager_settings' in config is not a dictionary. Using default timer interval (disabled).", level="warning")
            self.query_timer_interval = 0
        # --- End Read Manager-Specific Settings ---

        # --- Connect Session Handlers ---
        session_configs = config_data.get("session_configs", [])
        if not session_configs or not isinstance(session_configs, list):
            self.write_log(f"No 'session_configs' list found or invalid format in {self.manager_setting_filename}. No sessions will be connected.", level="warning")
            # If no sessions, still register timer if interval > 0, though it won't do much.
        else:
            for i, sess_conf in enumerate(session_configs):
                vnpy_account_id = sess_conf.get("vnpy_account_id")
                if not vnpy_account_id:
                    self.write_log(f"Session config #{i} missing 'vnpy_account_id', skipping.", level="warning")
                    continue
                
                if vnpy_account_id in self.handlers:
                    self.write_log(f"Session for vnpy_account_id '{vnpy_account_id}' already exists or configured multiple times, skipping.", level="warning")
                    continue

                handler_gateway_name = f"{self.gateway_name}.{vnpy_account_id}" 
                
                try:
                    handler = ShioajiSessionHandler(
                        manager_event_callback=self._handle_event_from_handler,
                        gateway_name=handler_gateway_name,
                        vnpy_account_id=vnpy_account_id
                    )
                    self.handlers[vnpy_account_id] = handler
                    self.write_log(f"Connecting session for vnpy_account_id: {vnpy_account_id}...")
                    handler.connect(sess_conf) 
                except Exception as e:
                    self.write_log(f"Failed to create or connect handler for {vnpy_account_id}: {e}", level="error")
                    if vnpy_account_id in self.handlers:
                        del self.handlers[vnpy_account_id]
        # --- End Connect Session Handlers ---
        
        # --- Register Timer (after interval is known) ---
        self._register_timer() # <<< ADDED: Register timer after loading config
        # --- End Register Timer ---

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
            super().on_contract(data) # data is ContractData
        elif event_type == "log": # Assuming handler might send LogData
            if isinstance(data, LogData):
                 data.gateway_name = self.gateway_name # Ensure manager's name
                 log_event = Event(type=EVENT_LOG, data=data)
                 self.event_engine.put(log_event)
            else: # Or just a string message
                 self.write_log(f"Msg from {vnpy_account_id_origin}: {data}")
        elif event_type == "event": # Generic event from handler (e.g. subscribe success/fail)
            # data here is {"type": original_event_type, "data": original_data}
            original_event_type = data.get("type")
            original_data = data.get("data")
            if original_event_type == EVENT_SUBSCRIBE_SUCCESS:
                # Manager might want to aggregate this or just log
                self.write_log(f"Subscription success for {original_data} via {vnpy_account_id_origin}")
            elif original_event_type == EVENT_SUBSCRIBE_FAILED:
                self.write_log(f"Subscription failed for {original_data} via {vnpy_account_id_origin}", level="warning")
            # ... other generic events ...
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
        訂閱行情。
        - req: SubscribeRequest 物件。
        - vnpy_account_id_target: (可選) 指定要訂閱此行情的 VnPy 帳戶 ID。
                                  如果為 None，則使用負載均衡策略 (適用於多連線單帳戶)。
        """
        self.write_log(f"收到行情訂閱請求: {req.vt_symbol}, 指定帳戶: {vnpy_account_id_target}")

        if not self.handlers:
            self.write_log(f"無法訂閱 {req.vt_symbol}: 無已啟動的 Session Handlers。", level="warning")
            # 可以考慮透過 _handle_event_from_handler 發送一個總體的訂閱失敗事件
            # self._handle_event_from_handler("event", {"type": EVENT_SUBSCRIBE_FAILED, "data": req.vt_symbol}, "MANAGER")
            return

        target_handler: Optional[ShioajiSessionHandler] = None

        if vnpy_account_id_target:
            if vnpy_account_id_target in self.handlers:
                handler_instance = self.handlers[vnpy_account_id_target]
                if handler_instance.connected:
                    target_handler = handler_instance
                    self.write_log(f"為指定帳戶 {vnpy_account_id_target} 路由訂閱 {req.vt_symbol} 至 Handler {target_handler.gateway_name}")
                else:
                    self.write_log(f"無法為帳戶 {vnpy_account_id_target} 訂閱 {req.vt_symbol}: Handler 未連線。", level="warning")
            else:
                self.write_log(f"無法為帳戶 {vnpy_account_id_target} 訂閱 {req.vt_symbol}: 未找到對應的 Handler。", level="warning")
        else:
            # 無指定帳戶，使用負載均衡策略 (適用於單一邏輯帳戶下的多連線)
            min_subs = float('inf')
            for handler_id, handler_instance in self.handlers.items():
                if handler_instance.connected:
                    if len(handler_instance.subscribed) < min_subs:
                        min_subs = len(handler_instance.subscribed)
                        target_handler = handler_instance
            
            if target_handler:
                self.write_log(f"負載均衡：路由訂閱 {req.vt_symbol} 至 Handler {target_handler.gateway_name} (目前訂閱數: {min_subs})")
            else:
                self.write_log(f"無法透過負載均衡訂閱 {req.vt_symbol}: 無可用/已連線的 Handler。", level="warning")

        if target_handler:
            target_handler.subscribe(req) # Handler 內部會調用 on_event(EVENT_SUBSCRIBE_SUCCESS/FAILED,...)
            # 記錄此 vt_symbol 由哪個 handler (帳戶) 訂閱，方便後續管理 (如果需要)
            self.vt_symbol_to_vnpy_account_id[req.vt_symbol] = target_handler.vnpy_account_id
        else:
            # 如果最終沒有找到合適的 handler，可以考慮發送一個總體的訂閱失敗事件
            # (因為 handler 內部的 on_event(EVENT_SUBSCRIBE_FAILED) 可能不會被觸發)
            log_msg = f"訂閱 {req.vt_symbol} 最終失敗：無合適的 Handler。"
            self.write_log(log_msg, level="error")
            # 手動觸發一個 manager 級別的失敗通知
            self._handle_event_from_handler("event", {"type": EVENT_SUBSCRIBE_FAILED, "data": req.vt_symbol}, "MANAGER_GLOBAL")




    def send_order(self, req: OrderRequest) -> str:
        vnpy_account_id_target = req.accountid # Strategy MUST provide this for multi-account
        
        handler = self.handlers.get(vnpy_account_id_target)
        if handler and handler.connected:
            self.write_log(f"Routing order for {req.vt_symbol} to handler for vnpy_account_id {vnpy_account_id_target}")
            
            # Generate a globally unique order ID if handlers use local IDs only
            # Example:
            # local_handler_order_id_part = handler.send_order(req) # Assuming this returns just the Shioaji seqno part
            # if not local_handler_order_id_part.startswith(handler.gateway_name): # If it's not already prefixed
            #    global_vt_orderid = f"{handler.gateway_name}.{local_handler_order_id_part}"
            # else: # Handler already made it unique with its own gateway_name
            #    global_vt_orderid = local_handler_order_id_part
            # self.global_orderid_map[global_vt_orderid] = (vnpy_account_id_target, local_handler_order_id_part) # Store mapping
            # return global_vt_orderid
            
            # Simpler: assume handler's send_order returns a vt_orderid that includes its unique gateway_name
            vt_orderid = handler.send_order(req)
            return vt_orderid # This vt_orderid from handler should be like "SHIOAJI_MULTI.ACCOUNT1.xxx"
        else:
            self.write_log(f"Order for {req.vt_symbol} rejected: No connected handler for vnpy_account_id '{vnpy_account_id_target}'.", level="error")
            order = req.create_order_data(
                orderid=f"{self.gateway_name}.REJ_{datetime.now().strftime('%H%M%S%f')}", 
                gateway_name=self.gateway_name
            )
            order.status = Status.REJECTED
            order.accountid = req.accountid
            order.reference = f"No_Handler_For_{vnpy_account_id_target}"
            super().on_order(order) # Use super to bypass our own _handle_event_from_handler
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