# test_callbacks.py (v4 修正版，含下單範例與成交回報)

import time
import logging
import shioaji as sj
from pprint import pprint
from datetime import datetime
from typing import Dict

# 1. 抑制 pysolace 內部錯誤日誌
logging.getLogger("pysolace").setLevel(logging.ERROR)

# 2. 註冊回呼函式
def on_stk_tick(exchange: sj.Exchange, tick: sj.TickSTKv1):
    print("\n--- [股票 TICK] ---")
    print(f"{datetime.now():%H:%M:%S.%f}  型別: {type(tick)}")
    pprint(tick)

def on_stk_bidask(exchange: sj.Exchange, bidask: sj.BidAskSTKv1):
    print("\n--- [股票 BID/ASK] ---")
    print(f"{datetime.now():%H:%M:%S.%f}  型別: {type(bidask)}")
    pprint(bidask)

def on_fop_tick(exchange: sj.Exchange, tick: sj.TickFOPv1):
    print("\n--- [期權 TICK] ---")
    print(f"{datetime.now():%H:%M:%S.%f}  型別: {type(tick)}")
    pprint(tick)

def on_fop_bidask(exchange: sj.Exchange, bidask: sj.BidAskFOPv1):
    print("\n--- [期權 BID/ASK] ---")
    print(f"{datetime.now():%H:%M:%S.%f}  型別: {type(bidask)}")
    pprint(bidask)

def on_order_callback(order_state: sj.order.OrderState, msg: Dict):
    # 這裡會先收到 Order Event，再收到 Deal Event
    print("\n--- [ORDER/DEAL] ---")
    print(f"{datetime.now():%H:%M:%S.%f}  State: {order_state}")
    pprint(msg)

if __name__ == "__main__":
    # 使用者請填入自己的金鑰
    config = {
        "APIKey":    "ANu85PtAbUngajTYfFMTbxTYs8weEWx9KVjaw5h7DWhe",
        "SecretKey": "AcGtYJV7svxTnK5232hqAqkwXhmt4pqdRR6FbaMDi5LQ",
        "simulation": True
    }

    # 登入（若希望手動控制合約下載，可加 fetch_contract=False）
    api = sj.Shioaji(simulation=config["simulation"])
    api.login(
        api_key=config["APIKey"],
        secret_key=config["SecretKey"],
        receive_window=60000
    )
    print("→ 登入成功！")

    # 綁定所有回呼（V1 版皆使用 _callback 方法）
    api.quote.set_on_tick_stk_v1_callback(on_stk_tick)       # 股票 TICK :contentReference[oaicite:1]{index=1}
    api.quote.set_on_bidask_stk_v1_callback(on_stk_bidask)   # 股票 BID/ASK :contentReference[oaicite:2]{index=2}
    api.quote.set_on_tick_fop_v1_callback(on_fop_tick)       # 期權 TICK :contentReference[oaicite:3]{index=3}
    api.quote.set_on_bidask_fop_v1_callback(on_fop_bidask)   # 期權 BID/ASK :contentReference[oaicite:4]{index=4}
    api.set_order_callback(on_order_callback)                # 委託/成交回呼 :contentReference[oaicite:5]{index=5}

    # 下載合約，並等待完成
    print("→ 下載合約中...")
    api.fetch_contracts()
    while api.Contracts.status != 2:
        time.sleep(0.2)
    print("→ 合約下載完成！")

    # 取得台積電合約後訂閱行情（指定 V1）
    contract = api.Contracts.Stocks["TSE"]["2330"]
    api.quote.subscribe(
        contract,
        quote_type=sj.constant.QuoteType.Tick,
        version=sj.constant.QuoteVersion.v1
    )
    api.quote.subscribe(
        contract,
        quote_type=sj.constant.QuoteType.BidAsk,
        version=sj.constant.QuoteVersion.v1
    )
    print(f"→ 已訂閱行情: {contract.code} {contract.name}")

    # 立即下單：限價買進 1 張台積電
    order = api.Order(
        price=contract.price_tick * 100,    # 假設取當前跳動點位 * 100 為價格
        quantity=1,
        action=sj.constant.Action.Buy,
        price_type=sj.constant.StockPriceType.LMT,
        order_type=sj.constant.OrderType.ROD,
        order_lot=sj.constant.StockOrderLot.Common,
        account=api.stock_account,
        custom_field="test_buy"
    )
    trade = api.place_order(contract, order)  # 下單呼叫 :contentReference[oaicite:6]{index=6}
    print("→ 已送出下單，回傳 trade 物件:")
    pprint(trade)

    # 保持運行以接收所有回呼（120 秒後自動登出）
    print("\n→ 開始接收回呼，等待 120 秒…(Ctrl+C 可中斷)")
    try:
        time.sleep(120)
    except KeyboardInterrupt:
        pass
    finally:
        api.logout()
        print("→ 已登出。")
