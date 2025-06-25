# shioaji_session_manager.py (New file or add to the above)
from typing import Dict, Any, Optional, List, Set
from datetime import datetime
from threading import Lock
import time

from vnpy.trader.event import EVENT_LOG, EVENT_CONTRACT
from vnpy.trader.object import BarData
from vnpy.trader.gateway import BaseGateway
from vnpy.event import EventEngine
from vnpy.trader.object import (
    LogData, ContractData, SubscribeRequest, OrderRequest, 
    CancelRequest, HistoryRequest
)
from vnpy.trader.constant import Status          # Status is usually in vnpy.trader.constant
from vnpy.event import Event 
from vnpy.trader.utility import load_json
from shioaji_session_handler import ShioajiSessionHandler # If in separate file
from shioaji_session_handler import (
    EVENT_SUBSCRIBE_SUCCESS, 
    EVENT_SUBSCRIBE_FAILED,
    EVENT_CONTRACTS_LOADED
)
from shioaji_session_handler import SjFetchStatus  # Assuming this is defined in shioaji_session_handler
from vnpy.trader.event import EVENT_TIMER  

# Define constants for manager events if needed (e.g. session connect/disconnect)
EVENT_SJ_MANAGER_SESSION_CONNECTED = "eSJManagerSessionConnected"
EVENT_SJ_MANAGER_SESSION_DISCONNECTED = "eSJManagerSessionDisconnected"


class ShioajiSessionManager(BaseGateway):
    default_name = "SHIOAJI_MULTI"

    def __init__(self, event_engine: EventEngine, gateway_name: Optional[str] = None):
        super().__init__(event_engine, gateway_name or self.default_name)
        
        self.handlers: Dict[str, ShioajiSessionHandler] = {}
        self.vt_symbol_to_vnpy_account_id: Dict[str, str] = {} # Correctly initialized
        # self.global_orderid_map: Dict[str, Tuple[str, str]] = {} 
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
        self.default_order_account_id: Optional[str] = None
        self.connect_delay_seconds: float = 1.0 

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

            self.connect_delay_seconds = float(manager_settings.get("connect_delay_seconds", 1.0))
            if self.connect_delay_seconds < 0:
                self.connect_delay_seconds = 0 
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
                if i > 0 and self.connect_delay_seconds > 0: 
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
                        manager_event_callback=self._handle_event_from_handler,
                        manager_gateway_name=self.gateway_name,
                        gateway_name=handler_gateway_name,
                        vnpy_account_id=vnpy_account_id
                    )
                    self.handlers[vnpy_account_id] = handler
                    self.write_log(f"正在連接 vnpy_account_id: {vnpy_account_id} 的 session... (Primary Contract Handler: {is_primary_contract_h})")
                    if is_primary_contract_h and not self.primary_contract_handler_id:
                        self.primary_contract_handler_id = vnpy_account_id
                        self.write_log(f"將 Handler {vnpy_account_id} 設為實際的主合約處理者。")
                    handler.connect(sess_conf) 
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

    def _register_timer(self) -> None:
        if self.query_timer_interval > 0 and self.event_engine: 
            self.event_engine.register(EVENT_TIMER, self.process_timer_event)
            self.write_log(f"Manager periodic query timer registered (Interval: {self.query_timer_interval}s).")

    def _unregister_timer(self) -> None:
        if self.query_timer_interval > 0 and self.event_engine:
            try:
                self.event_engine.unregister(EVENT_TIMER, self.process_timer_event)
                self.write_log("Manager periodic query timer unregistered.")
            except Exception as e: 
                self.write_log(f"Error unregistering timer (might be already unregistered): {e}", level="warning")

    def process_timer_event(self, event: Event) -> None: 
        self.query_timer_count += 1
        
        if self.query_timer_count % self.query_timer_interval == 0:
            self.write_log("Manager timer: Triggering periodic query for accounts and positions.")
            
            any_handler_connected = any(h.connected for h in self.handlers.values())

            if not any_handler_connected:
                self.write_log("Manager timer: No handlers connected, skipping periodic query.", level="debug")
                return

            try:
                self.query_account()  
                self.query_position() 
            except Exception as e:
                self.write_log(f"Manager timer: Error during periodic query: {e}", level="error")

    def _handle_event_from_handler(
        self, 
        event_type: str, 
        data: Any, 
        vnpy_account_id_origin: str 
    ):
        if hasattr(data, "gateway_name"):
            data.gateway_name = self.gateway_name
        
        if hasattr(data, "accountid") and not getattr(data, "accountid", None):
            data.accountid = vnpy_account_id_origin

        if event_type == "tick":
            super().on_tick(data) 
        elif event_type == "order":
            super().on_order(data) 
        elif event_type == "trade":
            super().on_trade(data) 
        elif event_type == "account":
            super().on_account(data) 
        elif event_type == "position":
            super().on_position(data) 
        elif event_type == "contract":
            if isinstance(data, ContractData):
                contract_event_processed = False
                is_from_primary_handler = (vnpy_account_id_origin == self.primary_contract_handler_id)

                with self.global_contracts_lock:
                    existing_contract = self.global_contracts.get(data.vt_symbol)
                    needs_update = True 

                    if existing_contract:
                        if (existing_contract.name == data.name and
                            existing_contract.exchange == data.exchange and
                            existing_contract.product == data.product and
                            abs(existing_contract.size - data.size) < 1e-6 and 
                            abs(existing_contract.pricetick - data.pricetick) < 1e-6):
                            needs_update = False 
                        
                        if not needs_update and not is_from_primary_handler:
                            contract_event_processed = True 
                        elif needs_update:
                            self.write_log(f"更新全局快取中的合約 {data.vt_symbol} (來源: {vnpy_account_id_origin})。", level="info")
                    
                    if needs_update and not contract_event_processed: 
                        self.global_contracts[data.vt_symbol] = data
                        event = Event(EVENT_CONTRACT, data) 
                        self.event_engine.put(event)
                    
                    if is_from_primary_handler and not self.initial_contracts_loaded:
                        pass 

                contract_event_processed = True 

            if not contract_event_processed: 
                 self.write_log(f"收到未處理的 'contract' 類型事件: {data}", level="warning")
        elif event_type == "log": 
            if isinstance(data, LogData):
                 data.gateway_name = self.gateway_name 
                 log_event = Event(type=EVENT_LOG, data=data)
                 self.event_engine.put(log_event)
            else: 
                 self.write_log(f"Msg from {vnpy_account_id_origin}: {data}")
        elif event_type == "event": 
            original_event_type = data.get("type")
            original_data = data.get("data") 

            if original_event_type == EVENT_SUBSCRIBE_SUCCESS:
                vt_symbol_subscribed = original_data
                with self.subscription_lock:
                    if vt_symbol_subscribed in self.subscribed_api_level and \
                       self.subscribed_api_level[vt_symbol_subscribed] != vnpy_account_id_origin:
                        self.write_log(
                            f"警告：Handler {vnpy_account_id_origin} 報告 {vt_symbol_subscribed} 訂閱成功，"
                            f"但 Manager 記錄的負責 Handler 是 {self.subscribed_api_level[vt_symbol_subscribed]}。"
                            "可能存在訂閱邏輯衝突。", level="critical"
                        )
                    elif vt_symbol_subscribed not in self.subscribed_api_level: # Correctly set if this handler was chosen
                        # Check if this handler was indeed the one manager asked to subscribe
                        # This state should align with manager's decision in subscribe()
                        self.subscribed_api_level[vt_symbol_subscribed] = vnpy_account_id_origin
                        self.write_log(f"Manager 確認：Handler {vnpy_account_id_origin} 已成功在 API 層級訂閱 {vt_symbol_subscribed}。")

                    if vt_symbol_subscribed in self.subscribed_api_level and \
                       self.subscribed_api_level[vt_symbol_subscribed] == vnpy_account_id_origin:
                        self.vt_symbol_to_vnpy_account_id[vt_symbol_subscribed] = vnpy_account_id_origin
                        self.write_log(f"Manager: {vt_symbol_subscribed} is now mapped to handler {vnpy_account_id_origin} for history queries.")

                self.write_log(f"{vt_symbol_subscribed} 行情訂閱成功 (經由 {vnpy_account_id_origin})")


            elif original_event_type == EVENT_SUBSCRIBE_FAILED:
                vt_symbol_failed = original_data
                self.write_log(f"{vt_symbol_failed} 行情訂閱失敗 (經由 {vnpy_account_id_origin})", level="warning")
                with self.subscription_lock:
                    if vt_symbol_failed in self.subscribed_api_level and \
                       self.subscribed_api_level[vt_symbol_failed] == vnpy_account_id_origin:
                        self.write_log(f"由於 Handler {vnpy_account_id_origin} 未能成功訂閱 {vt_symbol_failed}，"
                                       "正在從 Manager 的 API 層級訂閱記錄中移除。", level="warning")
                        del self.subscribed_api_level[vt_symbol_failed]
                        if vt_symbol_failed in self.vt_symbol_to_vnpy_account_id and \
                           self.vt_symbol_to_vnpy_account_id[vt_symbol_failed] == vnpy_account_id_origin:
                            del self.vt_symbol_to_vnpy_account_id[vt_symbol_failed]
                            self.write_log(f"Manager: Cleared history query mapping for {vt_symbol_failed} from handler {vnpy_account_id_origin} due to subscription failure.")

            elif original_event_type == EVENT_CONTRACTS_LOADED:
                if vnpy_account_id_origin == self.primary_contract_handler_id and not self.initial_contracts_loaded:
                    status = data.get("status", "unknown") 
                    if status == "success":
                        self.initial_contracts_loaded = True
                        self.write_log(f"主合約 Handler ({vnpy_account_id_origin}) 已完成初始合約載入。")
                    else:
                        self.write_log(f"主合約 Handler ({vnpy_account_id_origin}) 初始合約載入失敗或狀態未知。", level="warning")

                elif vnpy_account_id_origin != self.primary_contract_handler_id:
                    self.write_log(f"非主合約 Handler ({vnpy_account_id_origin}) 完成合約載入。", level="debug")
        elif event_type == "session_status":
            status_info = data 
            self.write_log(f"Session status update from {vnpy_account_id_origin}: {status_info.get('status')}",
                           level="warning" if "failed" in status_info.get('status', '') else "info")
            if status_info.get('status') == "connected":
                evt = Event(EVENT_SJ_MANAGER_SESSION_CONNECTED, vnpy_account_id_origin)
                self.event_engine.put(evt)
            elif "disconnected" in status_info.get('status', ''):
                evt = Event(EVENT_SJ_MANAGER_SESSION_DISCONNECTED, vnpy_account_id_origin)
                self.event_engine.put(evt)

    def subscribe(self, req: SubscribeRequest, vnpy_account_id_target: Optional[str] = None) -> None:
        vt_symbol = req.vt_symbol
        requester_id = vnpy_account_id_target or "GLOBAL_REQUESTER"

        self.write_log(f"收到行情訂閱請求: {vt_symbol}, 請求者: {requester_id}")

        with self.global_contracts_lock:
            if vt_symbol not in self.global_contracts:
                self.write_log(f"無法訂閱 {vt_symbol}: 在 Manager 全局快取中未找到合約。", level="warning")
                return

        with self.subscription_lock:
            if vt_symbol not in self.subscription_references:
                self.subscription_references[vt_symbol] = set()
            self.subscription_references[vt_symbol].add(requester_id)
            
            # Corrected logging line:
            references_str = str(self.subscription_references[vt_symbol])
            escaped_references_str = references_str.replace("{", "{{").replace("}", "}}")
            self.write_log(f"{vt_symbol} 的訂閱引用者: {escaped_references_str}")

            if vt_symbol in self.subscribed_api_level:
                responsible_handler_id = self.subscribed_api_level[vt_symbol]
                self.write_log(f"{vt_symbol} 已由 Handler {responsible_handler_id} 在 API 層級訂閱。僅增加引用計數。")
                return

            if not self.handlers:
                self.write_log(f"無法訂閱 {vt_symbol}: 無已啟動的 Session Handlers。", level="warning")
                self.subscription_references[vt_symbol].discard(requester_id)
                if not self.subscription_references[vt_symbol]: # Check if set is empty
                    if vt_symbol in self.subscription_references: # Ensure key exists before del
                         del self.subscription_references[vt_symbol]
                return

            target_handler: Optional[ShioajiSessionHandler] = None
            chosen_handler_id: Optional[str] = None

            if vnpy_account_id_target and vnpy_account_id_target in self.handlers:
                handler_instance = self.handlers[vnpy_account_id_target]
                if handler_instance.connected:
                    target_handler = handler_instance
                    chosen_handler_id = vnpy_account_id_target
                    self.write_log(f"為指定帳戶 {vnpy_account_id_target} 路由 API 訂閱 {vt_symbol} 至 Handler {target_handler.gateway_name}")
                else:
                    self.write_log(f"無法為帳戶 {vnpy_account_id_target} 執行 API 訂閱 {vt_symbol}: Handler 未連線。", level="warning")
            
            if not target_handler:
                min_subs = float('inf')
                available_handlers = [h_id for h_id, h in self.handlers.items() if h.connected]
                if not available_handlers:
                    self.write_log(f"無法執行 API 訂閱 {vt_symbol}: 無可用/已連線的 Handler。", level="warning")
                    self.subscription_references[vt_symbol].discard(requester_id)
                    if not self.subscription_references[vt_symbol]: # Check if set is empty
                        if vt_symbol in self.subscription_references: # Ensure key exists before del
                            del self.subscription_references[vt_symbol]
                    return

                for handler_id_candidate in available_handlers:
                    current_api_subs_count = 0
                    for _, handler_responsible_id in self.subscribed_api_level.items():
                        if handler_responsible_id == handler_id_candidate:
                            current_api_subs_count +=1
                    
                    if current_api_subs_count < min_subs:
                        min_subs = current_api_subs_count
                        target_handler = self.handlers[handler_id_candidate]
                        chosen_handler_id = handler_id_candidate

                if target_handler and chosen_handler_id:
                    self.write_log(f"負載均衡：路由 API 訂閱 {vt_symbol} 至 Handler {target_handler.gateway_name} (API 訂閱數: {min_subs})")
                else: 
                     self.write_log(f"無法透過負載均衡選擇 Handler 進行 API 訂閱 {vt_symbol}。", level="warning")
                     self.subscription_references[vt_symbol].discard(requester_id)
                     if not self.subscription_references[vt_symbol]: # Check if set is empty
                         if vt_symbol in self.subscription_references: # Ensure key exists before del
                             del self.subscription_references[vt_symbol]
                     return

            if target_handler and chosen_handler_id:
                target_handler.subscribe(req) 
            else:
                log_msg = f"API 訂閱 {vt_symbol} 最終失敗：無合適的 Handler 可執行。"
                self.write_log(log_msg, level="error")
                self.subscription_references[vt_symbol].discard(requester_id)
                if not self.subscription_references[vt_symbol]: # Check if set is empty
                    if vt_symbol in self.subscription_references: # Ensure key exists before del
                        del self.subscription_references[vt_symbol]

    def unsubscribe(self, req: SubscribeRequest, vnpy_account_id_target: Optional[str] = None) -> None:
        vt_symbol = req.vt_symbol
        requester_id = vnpy_account_id_target or "GLOBAL_REQUESTER"

        self.write_log(f"收到行情取消訂閱請求: {vt_symbol}, 請求者: {requester_id}")

        with self.subscription_lock:
            if vt_symbol not in self.subscription_references or requester_id not in self.subscription_references[vt_symbol]:
                self.write_log(f"請求者 {requester_id} 並未訂閱 {vt_symbol}，無需取消。", level="info")
                return

            self.subscription_references[vt_symbol].discard(requester_id)
            self.write_log(f"已移除請求者 {requester_id} 對 {vt_symbol} 的訂閱引用。")

            if not self.subscription_references.get(vt_symbol): 
                if vt_symbol in self.subscription_references: 
                    del self.subscription_references[vt_symbol]
                self.write_log(f"{vt_symbol} 已無訂閱引用者。")

                if vt_symbol in self.subscribed_api_level:
                    responsible_handler_id = self.subscribed_api_level.pop(vt_symbol) 
                    
                    if vt_symbol in self.vt_symbol_to_vnpy_account_id and \
                       self.vt_symbol_to_vnpy_account_id[vt_symbol] == responsible_handler_id:
                        del self.vt_symbol_to_vnpy_account_id[vt_symbol]
                        self.write_log(f"Manager: Cleared history query mapping for {vt_symbol} from handler {responsible_handler_id} during unsubscribe.")

                    handler_to_unsubscribe = self.handlers.get(responsible_handler_id)

                    if handler_to_unsubscribe and handler_to_unsubscribe.connected:
                        self.write_log(f"指示 Handler {responsible_handler_id} 在 API 層級取消訂閱 {vt_symbol}...")
                        try:
                            handler_to_unsubscribe.unsubscribe(req) 
                            self.write_log(f"已向 Handler {responsible_handler_id} 發送 API 取消訂閱 {vt_symbol} 指令。")
                        except Exception as e:
                            self.write_log(f"調用 Handler {responsible_handler_id} 的 unsubscribe 方法時出錯 for {vt_symbol}: {e}", level="error")
                    elif handler_to_unsubscribe:
                        self.write_log(f"負責 {vt_symbol} API 訂閱的 Handler {responsible_handler_id} 未連接，無法執行 API 取消訂閱。", level="warning")
                    else:
                        self.write_log(f"未找到負責 {vt_symbol} API 訂閱的 Handler {responsible_handler_id} 實例。", level="warning")
                else:
                    self.write_log(f"{vt_symbol} 在 Manager 中無 API 層級訂閱記錄，無需操作 Handler。", level="debug")
            else:
                references_str = str(self.subscription_references[vt_symbol])
                escaped_references_str = references_str.replace("{", "{{").replace("}", "}}")
                self.write_log(f"{vt_symbol} 仍有其他訂閱引用者: {escaped_references_str}。僅減少引用計數。")

    def send_order(self, req: OrderRequest) -> str:
        vnpy_account_id_to_use = req.accountid
        is_default_routing = False

        if not vnpy_account_id_to_use:
            is_default_routing = True
            self.write_log("OrderRequest 中未指定 accountid，嘗試使用預設帳戶路由。", level="info")

            if self.default_order_account_id and self.default_order_account_id in self.handlers:
                if self.handlers[self.default_order_account_id].connected:
                    vnpy_account_id_to_use = self.default_order_account_id
                    self.write_log(f"使用設定檔中指定的預設下單帳戶: {vnpy_account_id_to_use}")
                else:
                    self.write_log(f"設定檔中指定的預設下單帳戶 {self.default_order_account_id} 未連接。", level="warning")
            
            if not vnpy_account_id_to_use and self.primary_contract_handler_id and \
               self.primary_contract_handler_id in self.handlers and \
               self.handlers[self.primary_contract_handler_id].connected:
                vnpy_account_id_to_use = self.primary_contract_handler_id
                self.write_log(f"使用主合約 Handler 對應的帳戶 {vnpy_account_id_to_use} 作為預設下單帳戶。")

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
                super().on_order(order) 
                return order.vt_orderid
            
            req.accountid = vnpy_account_id_to_use
            if is_default_routing:
                self.write_log(f"訂單將路由至預設帳戶: {req.accountid}")

        target_handler = self.handlers.get(req.accountid) 

        if target_handler and target_handler.connected:
            if self.initial_contracts_loaded: 
                with self.global_contracts_lock:
                    if req.vt_symbol not in self.global_contracts:
                        error_msg = f"訂單 for {req.vt_symbol} 被拒絕: 在 Manager 全局快取中未找到合約。"
                        self.write_log(error_msg, level="error")
                        order = req.create_order_data(
                            orderid=f"{self.gateway_name}.REJ_NO_CONTRACT_{datetime.now().strftime('%H%M%S_%f')}",
                            gateway_name=self.gateway_name
                        )
                        order.status = Status.REJECTED
                        order.accountid = req.accountid 
                        order.reference = error_msg
                        super().on_order(order)
                        return order.vt_orderid
            else:
                self.write_log(f"Manager 初始合約尚未完全載入，暫不檢查訂單 {req.vt_symbol} 的合約是否存在於全局快取。", level="info")

            self.write_log(f"路由訂單 for {req.vt_symbol} 至 handler for vnpy_account_id {req.accountid}")
            vt_orderid = target_handler.send_order(req) 
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
            order.accountid = req.accountid 
            order.reference = err_msg
            super().on_order(order)
            return order.vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        vt_orderid_to_cancel = req.orderid
        self.write_log(f"收到撤單請求 for manager order ID: {vt_orderid_to_cancel}")

        target_handler: Optional[ShioajiSessionHandler] = None
        found_handler_for_cancel = False
        for vnpy_acc_id, handler_instance in self.handlers.items():
            if vt_orderid_to_cancel.startswith(handler_instance.gateway_name + "."):
                target_handler = handler_instance
                if target_handler.connected:
                    self.write_log(f"路由撤單請求 {vt_orderid_to_cancel} 至 Handler {target_handler.gateway_name}")
                    target_handler.cancel_order(req) 
                    found_handler_for_cancel = True
                    break
                else:
                    self.write_log(f"無法撤銷訂單 {vt_orderid_to_cancel}: Handler {target_handler.gateway_name} 未連線。", level="warning")
                    found_handler_for_cancel = True 
                    break
        
        if not found_handler_for_cancel:
            self.write_log(f"撤單請求 {vt_orderid_to_cancel} 失敗: 未找到能處理此 OrderID 的 Handler，或 OrderID 格式不符。", level="error")

    def query_account(self) -> None: 
        self.write_log("Manager: Querying account details for all active sessions.")
        for vnpy_account_id, handler in self.handlers.items():
            if handler.connected:
                self.write_log(f"Manager: Requesting account query from handler for {vnpy_account_id}")
                handler.query_account() 

    def query_position(self) -> None: 
        self.write_log("Manager: Querying position details for all active sessions.")
        for vnpy_account_id, handler in self.handlers.items():
            if handler.connected:
                self.write_log(f"Manager: Requesting position query from handler for {vnpy_account_id}")
                handler.query_position() 

    def query_history(self, req: HistoryRequest) -> Optional[List[BarData]]:
        """
        Queries historical bar data.
        This method acts as a guard and a router.
        """
        self.write_log(
            f"Manager: Received history query for {req.vt_symbol} from {req.start} to {req.end}, Interval: {req.interval.value}"
        )

        # 1. 檢查主合約處理器是否存在且已連接
        if not self.primary_contract_handler_id:
            self.write_log(
                f"Manager: History query for {req.vt_symbol} rejected. "
                f"No primary contract handler has been configured or assigned.",
                level="error"
            )
            return []  # 返回空列表，避免策略出錯

        primary_handler = self.handlers.get(self.primary_contract_handler_id)

        if not primary_handler or not primary_handler.connected:
            self.write_log(
                f"Manager: History query for {req.vt_symbol} rejected. "
                f"Primary contract handler '{self.primary_contract_handler_id}' is not available or not connected.",
                level="warning"
            )
            return []  # 返回空列表

        # 2. 檢查主處理器的合約下載狀態
        try:
            if (
                not hasattr(primary_handler, 'api')
                or not primary_handler.api
                or not hasattr(primary_handler.api, 'Contracts')
                or primary_handler.api.Contracts.status != SjFetchStatus.Fetched
            ):
                status_str = "NOT_AVAILABLE"
                if hasattr(primary_handler, 'api') and primary_handler.api and hasattr(primary_handler.api, 'Contracts'):
                    status_str = primary_handler.api.Contracts.status.value

                self.write_log(
                    f"Manager: History query for {req.vt_symbol} rejected. "
                    f"Primary handler's contract status is '{status_str}', not 'Fetched'. "
                    "Please wait for contract download to complete.",
                    level="warning"
                )
                return []  # 返回空列表
        except Exception as e:
            self.write_log(f"Manager: Error checking primary handler's contract status: {e}", level="error")
            return []  # 返回空列表

        # 3. 如果檢查通過，才將請求路由給主處理器
        self.write_log(f"Manager: Gateway is ready. Routing history query for {req.vt_symbol} to primary handler: {primary_handler.gateway_name}")

        try:
            return primary_handler.query_history(req)
        except Exception as e:
            self.write_log(f"Manager: Error calling query_history on handler {primary_handler.gateway_name} for {req.vt_symbol}: {e}", level="error")
            return []  # 返回空列表
        
    def close(self) -> None:
        self.write_log("Closing Shioaji Session Manager...")
        self._unregister_timer() 
        
        for vnpy_account_id, handler in self.handlers.items():
            self.write_log(f"Closing handler for {vnpy_account_id}...")
            try:
                handler.close()
            except Exception as e:
                self.write_log(f"Error closing handler for {vnpy_account_id}: {e}", level="error")
        self.handlers.clear()
        self.write_log("All session handlers closed. Shioaji Session Manager shut down.")

    def write_log(self, msg: str, level: str = "info"): 
        log = LogData(msg=msg, gateway_name=self.gateway_name)
        event = Event(type=EVENT_LOG, data=log) 
        self.event_engine.put(event)

    def _select_new_primary_contract_handler(self) -> Optional[str]:
        for handler_id, handler_instance in self.handlers.items():
            if handler_instance.connected: 
                return handler_id
        return None