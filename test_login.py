# test_real_query_order.py

from zoneinfo import ZoneInfo
import shioaji as sj
from vnpy.trader.constant import Exchange, Direction, Offset, Status, OrderType
from vnpy.trader.object import OrderData

# 如果你已經有我們之前寫好的 ShioajiGateway，可改為 import 該類
# from shioaji_gateway import ShioajiGateway, EXCHANGE_SJ2VNPY, DIRECTION_MAP_REVERSE, FUTURES_OFFSET_MAP_REVERSE, STATUS_MAP

TAIPEI_TZ = ZoneInfo("Asia/Taipei")

# --- 1. 登入 Shioaji API ---
api = sj.Shioaji(simulation=True)  # 若要在沙盒模式測試，可 simulation=True
api_key = "5CMvwjbGomFcqfRvWn3QVQ5fczsrUYW2dFS9PdTjVdZw"

secret_key = "ECS8bSCtsVEze9jrXZFQNufUCc19kkdKyhoi55pYoU2c"


# 1.1 執行 CA 啟用（若開實盤）
api.login(
    api_key=api_key,
    secret_key=secret_key,
    fetch_contract=False,
    subscribe_trade=True,
    contracts_timeout=0
)

# 1.2 選擇預設帳戶
accounts = api.list_accounts()
for acc in accounts:
    if acc.account_type == sj.account.AccountType.Stock:
        api.set_default_account(acc)
        break

# --- 2. 更新狀態 & 撈交易 ---
# 2.1 更新帳戶訂單/成交狀態
api.update_status(api.stock_account)

# 2.2 取得所有 Trade 物件
trades = api.list_trades()

# --- 3. 過濾 & 顯示 --- 
print(f"共撈到 {len(trades)} 筆 Trade 記錄\n")

for t in trades:
    o = t.order
    d = t.deal

    # 範例：只顯示 2330.TWSE 的資料
    if o.code != "TXFR1":
        continue

    order_dt = o.datetime.replace(tzinfo=TAIPEI_TZ)

    od = OrderData(
        gateway_name="SHIOAJI",
        symbol=o.code,
        exchange=Exchange.TAIFEX,
        orderid=str(o.seq_no),
        type=OrderType.LIMIT,
        direction=Direction.LONG if o.action == sj.constant.Action.Buy else Direction.SHORT,
        offset=Offset.NONE,  # 如為期貨需自行映射 oc_type
        price=float(o.price),
        volume=float(o.quantity),
        traded=float(d.quantity),
        status=Status.ALLTRADED if o.status == sj.constant.Status.Filled else Status.SUBMITTING,
        datetime=order_dt,
        reference=""
    )

    print("── OrderData ─────────────────────────────")
    print(f"Symbol:  {od.symbol}")
    print(f"Exchange: {od.exchange}")
    print(f"OrderID:  {od.vt_orderid}")
    print(f"Price:    {od.price}")
    print(f"Volume:   {od.volume}")
    print(f"Traded:   {od.traded}")
    print(f"Status:   {od.status}")
    print(f"Datetime: {od.datetime}")
    print("────────────────────────────────────────────\n")

# --- 4. 結束 ---
api.logout()
