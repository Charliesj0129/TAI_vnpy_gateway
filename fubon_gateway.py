# -*- coding: utf-8 -*-

import threading
import time
from typing import Dict, Any, List
import datetime
import json

from vnpy.trader.gateway import BaseGateway
from vnpy.trader.object import SubscribeRequest, TickData, OrderData, Offset, OptionType, OrderRequest, OrderType, BarData, AccountData, ContractData, CancelRequest, TradeData,PositionData,HistoryRequest,BaseData
from vnpy.trader.constant import Exchange, Direction, Interval, Product
from vnpy.trader.event import EVENT_LOG
# 引入 vnpy 的狀態常數
from vnpy.trader.constant import Status

# 引入富邦 SDK
from fubon_neo.sdk import FubonSDK ,FutOptOrder,Order
from fubon_neo.constant import (
    BSAction, PriceType, TimeInForce, OrderType as FubonOrderType, MarketType,
    FutOptMarketType, FutOptPriceType, FutOptOrderType, CallPut
)

import functools
import traceback


# --- 建議將此區塊放在 FubonGateway 類別之外或一個獨立的常數檔案中 ---

# 根據官方文件和您的補充，為股票 OrderResult 的 status 欄位建立完整映射
# 來源: https://www.fbs.com.tw/TradeAPI/docs/trading/library/python/trade/GetOrderResults
STOCK_STATUS_MAP = {
    0: Status.SUBMITTING,  # 預約單
    4: Status.SUBMITTING,  # 中台收到委託
    8: Status.SUBMITTING,  # 後台傳送中
    9: Status.REJECTED,    # 後台連線逾時
    10: Status.NOTTRADED, # 委託成功 (尚未有任何成交)
    30: Status.CANCELLED,  # 刪單成功
    40: Status.CANCELLED,  # 部分成交，剩餘取消
    50: Status.ALLTRADED, # 完全成交
    90: Status.REJECTED,   # 失敗
}

# 根據官方文件，為期貨 FutOptOrderResult 的 status 欄位建立完整映射
# 來源: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/trade/GetOrderResults
FUTOPT_STATUS_MAP = {
    10: Status.NOTTRADED, # 委託成功
    30: Status.CANCELLED,  # 刪單成功
    40: Status.CANCELLED,  # 部分成交刪單
    50: Status.ALLTRADED, # 完全成交
    90: Status.REJECTED,   # 失敗
    99: Status.REJECTED,   # 逾時
}

def robust_callback(func):
    """
    一個裝飾器，用於包裹所有 SDK 的回呼函式，
    捕捉並記錄任何未預期的錯誤，防止程式崩潰。
    """
    @functools.wraps(func)
    def wrapper(gateway_instance, code, content):
        try:
            return func(gateway_instance, code, content)
        except Exception:
            # 記錄詳細的錯誤追蹤訊息
            error_msg = f"在回呼函式 {func.__name__} 中發生未預期錯誤:\n{traceback.format_exc()}"
            gateway_instance.write_log(error_msg)
    return wrapper

# --- 新的 FubonApi 類別 (負責與 SDK 互動) ---
class FubonApi:
    def __init__(self, sdk: FubonSDK, accounts: List[Any]):
        self.sdk = sdk
        self.accounts = accounts
        self.stock_account = next((acc for acc in self.accounts if acc.account_type == 'stock'), None)
        self.futopt_account = next((acc for acc in self.accounts if acc.account_type == 'futopt'), None)

    def send_order(self, order_req: OrderRequest) -> dict:
        """統一的下單方法，根據商品類型自動路由。"""
        # 根據 vnpy OrderRequest 的 product 屬性進行路由
        if order_req.product == Product.EQUITY:
            return self._send_stock_order(order_req)
        elif order_req.product in [Product.FUTURES, Product.OPTION]:
            return self._send_futopt_order(order_req)
        else:
            return {"is_success": False, "message": f"不支援的下單商品類型: {order_req.product.value}"}

        
    def _send_stock_order(self, order_req: OrderRequest) -> dict:
        """處理股票下單的私有方法 (根據官方文件最終確認)"""
        if not self.stock_account:
            return {"is_success": False, "message": "找不到可用的證券帳戶"}
            
        # 將 vnpy OrderRequest 映射到 FubonStockOrder
        # 假設 order_req.type 等已轉換為 Fubon 對應的枚舉字串
        stock_order = Order(
            buy_sell=BSAction[order_req.direction.name],
            symbol=order_req.symbol,
            price=str(order_req.price),
            quantity=int(order_req.volume),
            market_type=MarketType.Common,         # 股票通常為普通盤
            price_type=PriceType[order_req.type.name], # e.g. PriceType.LIMIT
            time_in_force=TimeInForce.ROD,         # 預設 ROD
            order_type=FubonOrderType.Stock      # 預設現股
        )
        return self.sdk.stock.place_order(self.stock_account, stock_order)

    # --- _send_futopt_order 確認 ---
    def _send_futopt_order(self, order_req: OrderRequest) -> dict:
        """處理期貨/選擇權下單的私有方法 (根據官方文件最終確認)"""
        if not self.futopt_account:
            return {"is_success": False, "message": "找不到可用的期貨帳戶"}

        # 將 vnpy OrderRequest 映射到 FutOptOrder
        # 這一步驟假設 vnpy 的 OrderRequest 中，透過 extra 欄位或 reference 傳入了期貨所需的額外資訊
        futopt_order = FutOptOrder(
            buy_sell=BSAction[order_req.direction.name],
            symbol=order_req.symbol,
            price=str(order_req.price),
            lot=int(order_req.volume), # 對應到 lot
            market_type=FutOptMarketType[order_req.extra.get("market_type", "Future")],
            price_type=FutOptPriceType[order_req.type.name],
            time_in_force=TimeInForce[order_req.time_in_force.name],
            order_type=FutOptOrderType[order_req.extra.get("order_type", "Auto")]
        )
        
        # 如果是選擇權，還需要設定 call_put
        if order_req.product == Product.OPTION:
            futopt_order.call_put = CallPut[order_req.option_type.name]

        return self.sdk.futopt.place_order(self.futopt_account, futopt_order)
    
    def cancel_order(self, order_to_cancel: object) -> dict:
        """
        統一的取消訂單方法，根據回報物件的類型自動路由。
        """
        # --- 路由邏輯 ---
        # 根據回報物件中 asset_type 的存在與值來判斷
        asset_type = getattr(order_to_cancel, 'asset_type', 0)

        if asset_type == 0: # 股票
            if not self.stock_account:
                return {"is_success": False, "message": "找不到可用的證券帳戶"}
            return self.sdk.stock.cancel_order(
                account=self.stock_account,
                order_result=order_to_cancel
            )
        else: # 期貨或選擇權
            if not self.futopt_account:
                return {"is_success": False, "message": "找不到可用的期貨帳戶"}
            return self.sdk.futopt.cancel_order(
                account=self.futopt_account,
                order_result=order_to_cancel
            )
        
    def parse_stock_order(self, content: object) -> OrderData:
        """將股票 OrderResult 轉成 vn.py 的 OrderData (依官方文件最終確認)"""
        
        status = STOCK_STATUS_MAP.get(content.status, Status.UNKNOWN)
        
        if content.filled_qty > 0 and status not in [Status.ALLTRADED, Status.CANCELLED, Status.REJECTED]:
            status = Status.PARTTRADED

        # 處理交易所映射
        exchange = Exchange.TSE
        if content.market == "TAISDAQ":
            exchange = Exchange.OTC
        elif content.market == "TAIEMG":
            exchange = Exchange.EMERGING

        order = OrderData(
            gateway_name="FUBON",
            symbol=content.stock_no,
            exchange=exchange,
            orderid=content.order_no,
            direction=Direction.LONG if content.buy_sell == "Buy" else Direction.SHORT,
            price=float(content.price),
            volume=int(content.quantity),
            traded=int(content.filled_qty),
            status=status,
            datetime=datetime.strptime(f"{content.date} {content.last_time}", "%Y/%m/%d %H:%M:%S.%f")
        )
        return order

    def parse_futopt_order(self, content: object) -> OrderData:
        """將 FutOptOrderResult 轉成 vn.py 的 OrderData (依官方文件最終確認)"""
        
        status = FUTOPT_STATUS_MAP.get(content.status, Status.UNKNOWN)
        
        if content.filled_lot > 0 and status not in [Status.ALLTRADED, Status.CANCELLED, Status.REJECTED]:
            status = Status.PARTTRADED
        
        order = OrderData(
            gateway_name="FUBON",
            symbol=content.symbol,
            exchange=Exchange.TAIFEX,
            orderid=content.order_no,
            direction=Direction.LONG if content.buy_sell == "Buy" else Direction.SHORT,
            price=float(content.price),
            volume=float(content.lot),
            traded=float(content.filled_lot),
            status=status,
            datetime=datetime.strptime(f"{content.date} {content.lastTime}", "%Y/%m/%d %H:%M:%S")
        )
        return order

    # +++ 新增區塊: 指數資料解析器 +++
    def parse_index_data(self, data: dict) -> TickData:
        """
        將 indices 頻道的 JSON 資料，轉換為 vnpy 的 TickData 物件。
        """
        dt = datetime.fromtimestamp(data.get("timestamp") / 1000.0)

        tick = TickData(
            gateway_name="FUBON",
            symbol=data.get("symbol"),
            exchange=Exchange.TSE, # 指數通常屬於交易所級別
            datetime=dt,
            name=data.get("name"),
            last_price=float(data.get("last_price", 0)),
            # vnpy 的 TickData 中沒有直接對應 change 和 change_rate 的欄位
            # 但我們可以利用 open_price 和 pre_close 來間接儲存或計算
            # 假設 last_price - change = pre_close
            pre_close=float(data.get("last_price", 0)) - float(data.get("change", 0))
        )
        return tick

    def query_account_balance(self, account: object) -> dict:
        """查詢帳戶資金餘額"""
        return self.sdk.accounting.bank_remain(account)

    def query_unrealized_pnl(self, account: object) -> dict:
        """查詢未實現損益"""
        return self.sdk.accounting.unrealized_profit_loss(account)

    def query_positions(self, account: object) -> dict:
        """查詢持股庫存"""
        return self.sdk.accounting.inventories(account)

    def query_history_bars(self, symbol: str, start_date: str, end_date: str, timeframe: str) -> dict:
        """查詢歷史 K 線"""
        return self.sdk.marketdata.rest_client.stock.historical.candles(
            symbol=symbol,
            from_date=start_date,
            to_date=end_date,
            timeframe=timeframe

        )
    def query_all_positions(self) -> list:
        """
        統一查詢所有商品（股票、期貨）的倉位，並返回一個標準化的列表。
        """
        all_positions = []

        # --- 1. 查詢股票庫存 ---
        if self.stock_account:
            stock_res = self.sdk.accounting.inventories(self.stock_account)
            if stock_res.is_success:
                for pos in stock_res.data:
                    # 將股票庫存物件標準化
                    std_pos = {
                        "symbol": pos.symbol,
                        "exchange": Exchange.TSE if len(pos.symbol) == 4 else Exchange.OTC,
                        "direction": Direction.LONG,
                        "volume": int(pos.qty),
                        "price": float(pos.avg_price),
                        "pnl": float(pos.unreal_pnl),
                        "product": Product.EQUITY
                    }
                    all_positions.append(std_pos)

        # --- 2. 查詢期貨/選擇權單式倉位 ---
        if self.futopt_account:
            futopt_res = self.sdk.futopt.get_single_positions(self.futopt_account)
            if futopt_res.is_success:
                for pos in futopt_res.data:
                    # 將期貨倉位物件標準化
                    std_pos = {
                        "symbol": pos.symbol,
                        "exchange": Exchange.TAIFEX,
                        "direction": Direction.LONG if pos.buy_sell == "B" else Direction.SHORT,
                        "volume": int(pos.lot),
                        "price": float(pos.avg_price),
                        "pnl": float(pos.unreal_pnl),
                        "product": Product.FUTURES # 需增加邏輯判斷是期貨還是選擇權
                    }
                    all_positions.append(std_pos)
        
        return all_positions

    def get_intraday_quote(self, symbol: str) -> dict:
        """
        獲取指定商品當前最即時的報價快照 (quote)。
        對應 /intraday/quote/{symbol} 端點。
        """
        try:
            return self.sdk.marketdata.rest_client.stock.intraday.quote(symbol=symbol)
        except Exception as e:
            # 實際應用中可以在此處寫入日誌
            return {"is_success": False, "message": f"查詢 quote 時發生異常: {e}"}

    def get_intraday_candles(self, symbol: str, timeframe: str) -> dict:
        """
        獲取指定商品當日的盤中分時 K 線圖。
        對應 /intraday/candles 端點。
        """
        try:
            return self.sdk.marketdata.rest_client.stock.intraday.candles(symbol=symbol, timeframe=timeframe)
        except Exception as e:
            return {"is_success": False, "message": f"查詢 intraday_candles 時發生異常: {e}"}

    def get_intraday_trades(self, symbol: str) -> dict:
        """
        獲取指定商品當日的所有逐筆成交明細。
        對應 /intraday/trades 端點。
        """
        try:
            return self.sdk.marketdata.rest_client.stock.intraday.trades(symbol=symbol)
        except Exception as e:
            return {"is_success": False, "message": f"查詢 intraday_trades 時發生異常: {e}"}

    def get_intraday_volumes(self, symbol: str) -> dict:
        """
        獲取指定商品當日的分價成交統計量表。
        對應 /intraday/volumes 端點。
        """
        try:
            return self.sdk.marketdata.rest_client.stock.intraday.volumes(symbol=symbol)
        except Exception as e:
            return {"is_success": False, "message": f"查詢 intraday_volumes 時發生異常: {e}"}
        
    def get_historical_stats(self, symbol: str, from_date: str, to_date: str) -> dict:
        """
        獲取指定商品在特定日期範圍內的歷史統計數據。
        對應 /historical/stats 端點。
        """
        try:
            return self.sdk.marketdata.rest_client.stock.historical.stats(
                symbol=symbol,
                from_date=from_date,
                to_date=to_date
            )
        except Exception as e:
            return {"is_success": False, "message": f"查詢 historical_stats 時發生異常: {e}"}


class FubonGateway(BaseGateway):
    """
    富邦 vnpy 交易閘道
    """

    default_setting: Dict[str, Any] = {
        "身份證字號": "",
        "登入密碼": "測試環境為 12345678",
        "憑證路徑": "",
        "憑證密碼": "測試環境為 12345678",
        "環境": ["正式", "測試"] # 提供下拉選單
    }

    def __init__(self, event_engine,gateway_name: str = "FUBON"):
        """
        建構函式
        """
        super().__init__(event_engine, gateway_name)
       # --- 修改點 1: 新增 api 屬性 ---
        self.api: FubonApi = None
        self.current_setting: dict = {}        
        self.sdk: FubonSDK = None
        self.accounts: List[Any] = []
        
        # 用於控制背景執行緒的迴圈
        self.active: bool = False
        # 用於保存背景執行緒物件
        self.thread: threading.Thread = None
        # 用於保護共享資源的鎖
        self.lock = threading.Lock()
        # --- 新增: 訂單生命週期管理屬性 ---
        self.order_id_lock = threading.Lock()
        self.next_order_id: int = 0
        # 建立 vnpy orderid 與 Fubon order_no 的雙向映射
        self.order_map: Dict[str, str] = {}
        self.reverse_order_map: Dict[str, str] = {}
        
        # 建立一個字典來緩存委託物件，以便更新
        self.orders: Dict[str, OrderData] = {}
        self.fubon_orders: Dict[str, object] = {} # 儲存 Fubon 的委託物件，便於後續查詢和更新
        


    def connect(self, setting: dict) -> None:
        """
        初始化並啟動與券商伺服器的連接。
        """
        # --- 認證流程 ---
        user_id = setting["身份證字號"]
        password = setting["登入密碼"]
        cert_path = setting["憑證路徑"]
        cert_pass = setting["憑證密碼"]
        environment = setting["環境"]
        try:
            # --- 根據環境選擇不同的 SDK 初始化方式 ---
            if environment == "測試":
                self.write_log("正在連接到【測試環境】...")
                test_url = "wss://neoapitest.fbs.com.tw/TASP/XCPXWS"
                # 假設使用 v2.2.1 之後版本的 SDK
                self.sdk = FubonSDK(30, 2, url=test_url)
            else:
                self.write_log("正在連接到【正式環境】...")
                self.sdk = FubonSDK()
            login_result = self.sdk.login(
                user_id,
                password,
                cert_path,
                cert_pass
            ) # 
        except Exception as e:
            self.write_log(f"登入異常，請檢查憑證或網路: {e}")
            return

        if not login_result.is_success: # 
            self.write_log(f"登入失敗: {login_result.message}") # 
            return

        # --- 保存帳戶資訊 ---
        self.accounts = login_result.data # 
        # --- 修改點 2: 初始化 FubonApi ---
        self.api = FubonApi(self.sdk, self.accounts)
        
        # --- 使用裝飾器來註冊回呼 ---
        # 註冊交易主動回報
        self.sdk.set_on_order(self._on_order)
        self.sdk.set_on_filled(self._on_filled)
        
        # +++ 新增: 註冊全域事件與交易連線重連 +++
        self.sdk.set_on_event(self._on_event)
        self.write_log("交易回報函式註冊成功。")


        # --- 修正：從 API 載入合約資訊 ---
        # 由於是網路請求，我們在背景執行緒中執行
        thread = threading.Thread(target=self._load_contracts_from_api)
        thread.daemon = True
        thread.start()

        # 啟動 WebSocket 背景任務
        self.active = True
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True
        self.thread.start()

    # +++ 新增: 全域事件處理與交易連線重連邏輯 +++
    @robust_callback
    def _on_event(self, code: str, content: object) -> None:
        """處理全域事件的回報，主要用於斷線重連。"""
        self.write_log(f"收到全域事件: Code={code}, Content={content}")
        
        # 根據官方文件，code "300" 代表交易連線中斷
        if code == "300":
            self.write_log("交易主機連線中斷，將嘗試自動重新登入...")
            
            # 重新登入，這裡直接使用 connect 方法中儲存的 setting
            # 注意：這裡的重登入是一個簡化版，生產環境可能需要更複雜的狀態管理
            setting = self.current_setting # 假設 setting 已被儲存
            user_id = setting["身份證字號"]
            password = setting["登入密碼"]
            cert_path = setting["憑證路徑"]
            cert_pass = setting["憑證密碼"]
            
            try:
                # 再次呼叫 login 來重連
                login_result = self.sdk.login(
                    personal_id=user_id,
                    password=password,
                    cert_path=cert_path,
                    cert_pass=cert_pass
                )
                if login_result.is_success:
                    self.write_log("交易主機重連並登入成功！")
                else:
                    self.write_log(f"交易主機重連失敗: {login_result.message}")
            except Exception as e:
                self.write_log(f"交易主機重連時發生異常: {e}")

    def _run(self) -> None:
        """
        背景執行緒執行的主函式，負責管理 WebSocket 連線和自動重連。
        """
        retry_delay = 5  # 初始重連延遲秒數

        # 初始化即時數據連線
        self.sdk.init_realtime() # 

        # 註冊 WebSocket 事件回調
        ws_client = self.sdk.marketdata.websocket_client.stock
        ws_client.on("connect", self._on_connected)
        ws_client.on("disconnect", self._on_disconnected)
        ws_client.on("message", self._on_message) # 將在行情區塊實現

        while self.active:
            try:
                # 此為阻塞式呼叫，會在此處運行事件迴圈直到連線中斷
                ws_client.connect()
                
                # 如果是正常斷開 (e.g., close()被呼叫)，則跳出迴圈
                if not self.active:
                    break

                # 如果是非預期斷線，則進入重連邏輯
                self.write_log(f"WebSocket 連線中斷，將在 {retry_delay} 秒後嘗試重連。")
                time.sleep(retry_delay)
                
                # 指數退避策略，增加下次重連的等待時間，上限為 60 秒
                retry_delay = min(retry_delay * 2, 60)

            except Exception as e:
                self.write_log(f"WebSocket 發生錯誤: {e}")
                time.sleep(retry_delay)

    def _on_connected(self) -> None:
        """
        WebSocket 連線成功時的回調函式。
        """
        self.write_log("WebSocket 連線成功，交易閘道已就緒。")
        # 此處可以加入自動重新訂閱行情的邏輯

    def _on_disconnected(self) -> None:
        """
        WebSocket 連線中斷時的回調函式。
        """
        self.write_log("WebSocket 連線已斷開。")

    def close(self) -> None:
        """
        關閉與券商伺服器的連接。
        """
        if not self.active:
            return
            
        self.active = False
        
        # 關閉 WebSocket 連線
        if self.sdk and self.sdk.marketdata.websocket_client.stock:
            self.sdk.marketdata.websocket_client.stock.disconnect()
            
        # 等待背景執行緒結束
        if self.thread and self.thread.is_alive():
            self.thread.join()

        # 登出 API
        if self.sdk:
            self.sdk.logout() # 
            
        self.write_log("交易閘道已成功斷開。")

    def subscribe(self, req: SubscribeRequest) -> None:
        """
        訂閱即時市場行情數據。
        """
        try:
            # 根據文件，訂閱 trades 頻道以獲取逐筆成交資料 
            subscription_payload = {
                'channel': 'trades',
                'symbol': req.symbol
            }
            self.sdk.marketdata.websocket_client.stock.subscribe(subscription_payload)
            self.write_log(f"已發送 {req.symbol} 的 'trades' 頻道訂閱請求。")
        except Exception as e:
            self.write_log(f"訂閱行情失敗: {e}")

    def _on_message(self, message: str) -> None:
        """
        處理所有從 WebSocket 收到的原始訊息，並根據 channel 分派。
        """
        data = json.loads(message)

        # 根據 channel 類型分派到不同的處理函式 
        channel = data.get("channel")
        if channel == "trades":
            self._on_tick(data)
        # 未來可以增加對 books 頻道的處理
        elif channel == "books":
            self._on_book(data)
        elif channel == "indice":
            # 將指數資料交給 FubonApi 的新解析器處理
            index_tick = self.api.parse_index_data(data)
            self.on_tick(index_tick)
        else:
            self.write_log(f"未知的 channel: {channel}，無法處理數據。")


    def _on_tick(self, data: dict) -> None:
        """
        處理 'trades' 頻道的逐筆成交數據，將其從 Fubon JSON 格式轉換為 vnpy 的 TickData 物件。
        轉換邏輯嚴格遵循「表 3.3.1」的 trades 頻道結構 。
        """
        try:
            # 使用 Unix timestamp (毫秒) 建立 datetime 物件，更為精確 
            dt = datetime.fromtimestamp(data.get("timestamp") / 1000.0)

            tick = TickData(
                gateway_name=self.gateway_name,
                symbol=data.get("symbol"),
                exchange=Exchange.TSE,
                datetime=dt,
                last_price=float(data.get("price", 0)),      # 正確對應 "price" 欄位 
                last_volume=float(data.get("volume", 0)),    # 正確對應 "volume" 欄位 
            )

            # 處理 tick_type，提供內外盤方向資訊 
            tick_type = data.get("tick_type")
            if tick_type == "I":  # "I" 代表內盤 (Inward)，以賣價成交
                tick.direction = Direction.SHORT
            elif tick_type == "O": # "O" 代表外盤 (Outward)，以買價成交
                tick.direction = Direction.LONG

            # --- 推送標準化數據 ---
            self.on_tick(tick)

        except Exception as e:
            self.write_log(f"處理 Tick 數據時發生錯誤: {e}\n原始數據: {data}")

    def send_order(self, req: OrderRequest) -> str:
        """發送委託 (重構後)"""
        # --- 修改點 3: send_order 內部邏輯替換 ---
        with self.order_id_lock:
            self.next_order_id += 1
            vt_orderid = f"{self.gateway_name}.{self.next_order_id}"

        # 直接呼叫 FubonApi 的方法
        result = self.api.send_stock_order(req)
        
        # 處理回傳結果的邏輯不變
        if result.is_success and result.data:
            fubon_order_no = result.data[0].order_no
            self.order_map[vt_orderid] = fubon_order_no
            self.reverse_order_map[fubon_order_no] = vt_orderid
            self.write_log(f"委託請求已成功發送，VnpyOrderID: {vt_orderid}, FubonOrderNo: {fubon_order_no}")
        else:
            self.write_log(f"委託請求發送失敗: {result.message}")

        return vt_orderid

    def cancel_order(self, req: CancelRequest) -> None:
        """取消一筆現有的委託單 (最終版)"""
        vt_orderid = req.orderid
        
        # 從快取中獲取要取消的 Fubon 原始回報物件
        order_to_cancel = self.fubon_orders.get(vt_orderid)

        if not order_to_cancel:
            self.write_log(f"取消委託失敗：在本地快取中找不到委託 {vt_orderid}。")
            return

        # 直接呼叫 FubonApi 的統一方法
        cancel_res = self.api.cancel_order(order_to_cancel)

        # 處理同步回傳的請求結果
        if not cancel_res.is_success:
            self.write_log(f"送出取消委託請求失敗：{cancel_res.message}")
        else:
            self.write_log(f"已成功送出 {vt_orderid} 的取消請求。")

    @robust_callback
    def _on_order(self, code: int, content: object) -> None:
            """
            處理委託狀態更新的回報。
            """
            fubon_order_no = content.order_no
            vt_orderid = self.reverse_order_map.get(fubon_order_no)
            if not vt_orderid:
                return # 忽略不屬於此工作階段的委託

            # 狀態映射 (範例)
            status_msg = content.status_message
            if "委託成功" in status_msg:
                status = Status.SUBMITTED
            elif "已刪除" in status_msg or "取消成功" in status_msg:
                status = Status.CANCELLED
            elif "委託失敗" in status_msg:
                status = Status.REJECTED
            else:
                # 對於部分成交等狀態，通常由成交回報觸發更新
                # 先獲取現有訂單，如果沒有則創建
                order = self.orders.get(vt_orderid, None)
                if order:
                    status = order.status
                else:
                    status = Status.UNKNOWN

            # 建立或更新 OrderData
            if vt_orderid in self.orders:
                order = self.orders[vt_orderid]
            else:
                order = OrderData(
                    gateway_name=self.gateway_name,
                    orderid=vt_orderid,
                    symbol=content.symbol,
                    exchange=Exchange.TSE,
                    # 初始方向和類型需要從 send_order 時的請求中獲取
                    # 此處暫時留空，或建立 order request 緩存
                )
                self.orders[vt_orderid] = order
            self.fubon_orders[vt_orderid] = content

            order.status = status
            self.on_order(order)
            self.write_log(f"委託更新: {vt_orderid}, 狀態: {status_msg} ({status.value})")

    @robust_callback
    def _on_filled(self, code: int, content: object) -> None:
        """
        處理成交回報。
        """
        fubon_order_no = content.order_no
        vt_orderid = self.reverse_order_map.get(fubon_order_no)
        if not vt_orderid:
            return

        # 1. 建立並推送 TradeData
        trade = TradeData(
            gateway_name=self.gateway_name,
            orderid=vt_orderid,
            tradeid=content.filled_no, # 使用成交序號作為唯一成交 ID
            symbol=content.symbol,
            exchange=Exchange.TSE,
            price=float(content.fill_price),
            volume=float(content.fill_quantity),
            datetime=datetime.strptime(f"{datetime.now().date()} {content.fill_time}", "%Y-%m-%d %H:%M:%S"),
        )
        self.on_trade(trade)
        
        # 2. 更新 OrderData 的狀態
        order = self.orders.get(vt_orderid)
        if order:
            order.traded += trade.volume
            
            # 判斷訂單是否完全成交
            if order.traded >= order.volume:
                order.status = Status.ALLTRADED
            else:
                order.status = Status.PARTTRADED
            
            self.on_order(order)
    def query_account(self) -> None:
        """
        查詢帳戶資金。
        """
        # 透過背景執行緒執行查詢，避免阻塞主線程
        thread = threading.Thread(target=self._query_account)
        thread.daemon = True
        thread.start()

    def _query_account(self):
        """執行帳戶資金查詢的實際邏輯 (重構後)"""
        stock_account = next((acc for acc in self.accounts if acc.account_type == 'stock'), None)
        if not stock_account:
            return

        # 1. 查詢銀行餘額
        balance_res = self.api.query_account_balance(stock_account)
        if not balance_res.is_success:
            self.write_log(f"查詢資金餘額失敗: {balance_res.message}")
            return
        
        # 2. 查詢未實現損益
        pnl_res = self.api.query_unrealized_pnl(stock_account)
        if not pnl_res.is_success:
            self.write_log(f"查詢未實現損益失敗: {pnl_res.message}")
            return
            
        # 3. 組合數據 (此部分邏輯不變)
        bank_info = balance_res.data
        total_unreal_pnl = sum(pnl.unreal_pnl for pnl in pnl_res.data)
        account = AccountData(
            gateway_name=self.gateway_name,
            accountid=stock_account.account,
            balance=float(bank_info.balance),
            frozen=float(bank_info.balance) - float(bank_info.available_balance),
        )
        account.pnl = total_unreal_pnl
        self.on_account(account)


    def query_position(self) -> None:
        """
        查詢持股。
        """
        # 透過背景執行緒執行查詢
        thread = threading.Thread(target=self._query_position)
        thread.daemon = True
        thread.start()

    def _query_position(self):
        """執行持股查詢的實際邏輯 (最終版)"""
        
        # 呼叫 FubonApi 的統一查詢方法
        all_positions = self.api.query_all_positions()

        if not all_positions:
            self.write_log("查詢倉位完成，目前無任何持倉。")
            return

        # 遍歷標準化後的倉位列表，並轉換為 PositionData
        for pos_data in all_positions:
            position = PositionData(
                gateway_name=self.gateway_name,
                symbol=pos_data["symbol"],
                exchange=pos_data["exchange"],
                direction=pos_data["direction"],
                volume=pos_data["volume"],
                price=pos_data["price"],
                pnl=pos_data["pnl"],
                product=pos_data["product"]
            )
            self.on_position(position)
        
        self.write_log(f"倉位查詢完成，共获取 {len(all_positions)} 筆持倉資訊。")

    def query_history(self, req: HistoryRequest) -> List[BarData]:
            """
            查詢歷史 K 線數據。
            此為阻塞式函式，會直接返回查詢結果。
            """
            history: List[BarData] = []

            # 1. 轉換時間區間 (邏輯不變)
            start_date = req.start.strftime("%Y-%m-%d")
            end_date = req.end.strftime("%Y-%m-%d")

            # 2. 轉換 K 線週期 (使用已確認的完整映射表)
            # 注意：vnpy 的 Interval 枚舉沒有完全對應到所有分鐘線，此處僅映射常用週期
            interval_map = {
                Interval.MINUTE: "1",
                Interval.HOUR: "60",
                Interval.DAILY: "D",
                Interval.WEEKLY: "W",
            }
            fubon_interval = interval_map.get(req.interval)

            if not fubon_interval:
                self.write_log(f"不支援的 K 線週期: {req.interval.value}")
                return history

            # --- 新增：處理分鐘線的查詢限制 ---
            is_minute_bar = fubon_interval in ["1", "5", "10", "15", "30", "60"]
            if is_minute_bar:
                self.write_log(f"注意：分鐘級別K線查詢 ({fubon_interval}分線) 僅返回最近5個交易日的資料，您指定的日期範圍將被忽略。")

            # 3. 呼叫 SDK 查詢歷史數據 (邏輯不變)
            try:
                candles_res = self.api.query_history_bars(
                    symbol=req.symbol,
                    start_date=req.start.strftime("%Y-%m-%d"),
                    end_date=req.end.strftime("%Y-%m-%d"),
                    timeframe=fubon_interval
                )
            except Exception as e:
                self.write_log(f"查詢歷史 K 線失敗: {e}")
                return history

            # 4. 處理回傳數據並轉換為 BarData (邏輯不變)
            if not candles_res.is_success:
                self.write_log(f"查詢歷史 K 線失敗: {candles_res.message}")
                return history

            for candle in candles_res.data:
                bar = BarData(
                    gateway_name=self.gateway_name,
                    symbol=req.symbol,
                    exchange=req.exchange,
                    interval=req.interval,
                    datetime=datetime.fromisoformat(candle.get("date")),
                    open_price=float(candle.get("open")),
                    high_price=float(candle.get("high")),
                    low_price=float(candle.get("low")),
                    close_price=float(candle.get("close")),
                    volume=float(candle.get("volume")),
                )
                history.append(bar)

            self.write_log(f"成功獲取 {req.symbol} 的 {len(history)} 筆歷史資料。")
            return history
    
    def query_history_ticks(self, req: HistoryRequest) -> List[TickData]:
        """
        查詢歷史逐筆成交資料 (Tick)。
        這是一個自訂的輔助函式，非 BaseGateway 標準介面。
        """
        ticks: List[TickData] = []

        # 根據文件，此功能對應到 intraday/trades/{symbol} 端點
        # 注意：此端點僅查詢「當日」所有逐筆成交
        self.write_log(f"注意：正透過 REST API 查詢 {req.symbol} 的「當日」歷史逐筆成交資料。")

        try:
            # 呼叫 SDK 查詢當日逐筆成交
            trades_res = self.sdk.marketdata.rest_client.stock.intraday.trades(symbol=req.symbol)

            if not trades_res.is_success:
                self.write_log(f"查詢歷史 Tick 失敗: {trades_res.message}")
                return ticks

            # 遍歷回傳的每一筆成交
            for trade in trades_res.data:
                # 欄位包含 price, size, time, serial 等
                dt = datetime.fromtimestamp(trade.get("time") / 1_000_000_000) # 假設 time 是奈秒時間戳

                tick = TickData(
                    gateway_name=self.gateway_name,
                    symbol=req.symbol,
                    exchange=req.exchange,
                    datetime=dt,
                    last_price=float(trade.get("price")),
                    last_volume=float(trade.get("size")),
                )
                ticks.append(tick)

        except Exception as e:
            self.write_log(f"查詢歷史 Tick 時發生異常: {e}")

        self.write_log(f"成功獲取 {req.symbol} 的 {len(ticks)} 筆當日歷史 Tick 資料。")
        return ticks
    
    def _load_contracts_from_api(self):
        """
        透過 API 查詢並載入所有可交易的證券合約。
        """
        self.write_log("開始從 API 載入證券合約清單...")
        
        try:
            # 1. 查詢所有上市公司股票 (TWSE)
            twse_res = self.sdk.marketdata.rest_client.stock.intraday.tickers(market='TWSE', type='STOCK')
            if twse_res.is_success:
                for contract_info in twse_res.data:
                    self._create_and_push_contract(contract_info)
            else:
                self.write_log(f"查詢上市股票清單失敗: {twse_res.message}")

            # 2. 查詢所有上櫃公司股票 (OTC)
            otc_res = self.sdk.marketdata.rest_client.stock.intraday.tickers(market='OTC', type='STOCK')
            if otc_res.is_success:
                for contract_info in otc_res.data:
                    self._create_and_push_contract(contract_info)
            else:
                self.write_log(f"查詢上櫃股票清單失敗: {otc_res.message}")

            # (未來可擴充查詢 ETF 等其他類型)

        except Exception as e:
            self.write_log(f"載入 API 合約資訊時發生異常: {e}")

    def _create_and_push_contract(self, contract_info: object):
        """
        根據 API 回傳的資訊，建立並推送 ContractData 物件。
        """
        # 由於 API 不提供 pricetick，此部分仍需自行處理
        pricetick = self.get_pricetick(contract_info.referencePrice) # 假設我們有一個根據參考價決定 pricetick 的輔助函式

        contract = ContractData(
            gateway_name=self.gateway_name,
            symbol=contract_info.symbol,
            exchange=Exchange(contract_info.exchange),
            name=contract_info.name,
            product=Product.EQUITY, # 股票類型
            size=1000, # 台股固定為 1000
            pricetick=pricetick,
        )
        self.on_contract(contract)
        
    def get_pricetick(self, price: float) -> float:
        """
        根據價格，回傳對應的最小跳動點。
        此規則基於台灣證券交易所規範。
        """
        if price < 10:
            return 0.01
        elif price < 50:
            return 0.05
        elif price < 100:
            return 0.10
        elif price < 500:
            return 0.50
        elif price < 1000:
            return 1.00
        else:
            return 5.00
