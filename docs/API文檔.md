
### **登入 (login)**

#### **輸入參數**

| 參數 | 類別 | 說明 |
| :--- | :--- | :--- |
| `user_id` | String | 登入的 ID |
| `user_password` | String | 登入的密碼 |
| `ca_path` | String | 憑證路徑 |
| `ca_password` | String | 憑證密碼 |

#### **Result 回傳**

| 參數 | 類別 | 說明 |
| :--- | :--- | :--- |
| `is_success` | bool | 是否成功 |
| `data` | List | 回傳帳號資訊 |
| `message` | string | 當 `is_success` = `false` 回傳錯誤訊息 |

#### **帳號資訊 Account 欄位**

Return type: Object

| 參數 | 類別 | 說明 |
| :--- | :--- | :--- |
| `name` | String | 客戶姓名 |
| `account` | String | 客戶帳號 |
| `branch_no` | String | 分公司代號 |
| `account_type` | string | 帳號類型 回傳 `stock` 證券 `futopt` 期貨 |

#### **請求範例**

```python
from fubon_neo.sdk import FubonSDK, Order
from fubon_neo.constant import TimeInForce, OrderType, PriceType, MarketType, BSAction

sdk = FubonSDK()
accounts = sdk.login("Your ID", "Your Password","Your Cert Path","Your Cert Password")
print(accounts) #若有多帳號，則回傳多個
```

#### **回傳範例**

```
Result {
    is_success: True,
    message: None,
    data : Account{
        name : "富邦Bill", # 客戶姓名 (string)
        account : "28", # 客戶帳號 (string)
        branch_no : "6460", # 分公司代號 (string)
        account_type : "futopt" # 帳號類型 (string)
    }
}
```

---

# 取得委託單結果（期貨/選擇權｜Python SDK）

**方法名**：`sdk.futopt.get_order_results(account, market_type=None)`
**用途**：查詢帳戶在期貨/選擇權之委託單最新狀態（含有效價量、成交口數、狀態碼等）。([FBS][1])

---

## 輸入參數（Request）

| 參數            | 型別                 | 是否必填 | 說明                                                                                                 |
| ------------- | ------------------ | ---: | -------------------------------------------------------------------------------------------------- |
| `account`     | `Account`          |   必填 | 交易帳號物件。([FBS][1])                                                                                  |
| `market_type` | `FutOptMarketType` |   選填 | 盤別種類：`Future`（日盤期貨）、`FutureNight`（夜盤期貨）、`Option`（日盤選擇權）、`OptionNight`（夜盤選擇權）。**不帶則查全部**。([FBS][1]) |

---

## 回傳結構（Response）

頂層回傳為 `Result` 物件：([FBS][1])

```text
Result {
  is_success: bool,          # 是否成功
  message: string | None,    # is_success=false 時之錯誤訊息
  data: List[FutOptOrderResult]  # 委託資訊清單
}
```

---

## `FutOptOrderResult` 欄位定義

> **型別**：`List[FutOptOrderResult]`（每筆代表一張委託單）([FBS][1])

| 欄位                  | 型別                 | 說明                                                                                    |
| ------------------- | ------------------ | ------------------------------------------------------------------------------------- |
| `function_type`     | `int`              | **功能別**：`0`新單、`10`新單執行、`15`改價、`20`改量、`30`刪單、`90`失敗（可為 None）。([FBS][1])                |
| `date`              | `string`           | 交易日期。([FBS][1])                                                                       |
| `seq_no`            | `string`           | 委託單流水序號。([FBS][1])                                                                    |
| `branch_no`         | `string`           | 分公司代號。([FBS][1])                                                                      |
| `account`           | `string`           | 帳號。([FBS][1])                                                                         |
| `order_no`          | `string`           | 委託書號。([FBS][1])                                                                       |
| `asset_type`        | `int`              | 資產類別：`1`期貨、`2`選擇權。([FBS][1])                                                          |
| `market`            | `string`           | 市場類型：`TAIMEX`。([FBS][1])                                                              |
| `market_type`       | `FutOptMarketType` | 盤別：`Future`/`FutureNight`/`Option`/`OptionNight`。([FBS][1])                           |
| `unit`              | `int`              | 單位數。([FBS][1])                                                                        |
| `currency`          | `string`           | 幣別（如 `TWD`）。([FBS][1])                                                                |
| `symbol`            | `string`           | 商品代號（如 `FITF`）。([FBS][1])                                                             |
| `expiry_date`       | `string`           | 到期年月（如 `202404`）。([FBS][1])                                                           |
| `strike_price`      | `float`            | 履約價（選擇權；期貨為 None）。([FBS][1])                                                          |
| `call_put`          | `CallPut`          | 買賣權：`Call`/`Put`（期貨為 None）。([FBS][1])                                                 |
| `buy_sell`          | `BSAction`         | 買賣別：`Buy`/`Sell`。([FBS][1])                                                           |
| `symbol_leg2`       | `string`           | 複式第二腳商品代號（如無則 None）。([FBS][1])                                                        |
| `expiry_date_leg2`  | `string`           | 複式第二腳到期日。([FBS][1])                                                                   |
| `strike_price_leg2` | `float`            | 複式第二腳履約價。([FBS][1])                                                                   |
| `call_put_leg2`     | `CallPut`          | 複式第二腳買賣權。([FBS][1])                                                                   |
| `buy_sell_leg2`     | `BSAction`         | 複式第二腳買賣別。([FBS][1])                                                                   |
| `price_type`        | `FutOptPriceType`  | **原始委託價格別**：`Limit`/`Market`/`RangeMarket`/`Reference`。([FBS][1])                     |
| `price`             | `float`            | **原始委託價格**。([FBS][1])                                                                 |
| `lot`               | `int`              | **原始委託口數**。([FBS][1])                                                                 |
| `time_in_force`     | `TimeInforce`      | `ROD`/`FOK`/`IOC`。([FBS][1])                                                          |
| `order_type`        | `FutOptOrderType`  | `New`（新倉）/`Close`（平倉）/`Auto`/`FdayTrade`（當沖）。([FBS][1])                               |
| `is_pre_order`      | `bool`             | 是否為預約單。([FBS][1])                                                                     |
| `status`            | `int`              | **委託狀態**：`0`預約單、`4`中台收到、`8`後台傳送中、`9`後台逾時、`10`委託成功、`30`刪單成功、`50`完全成交、`90`失敗。([FBS][1]) |
| `after_price_type`  | `FutOptPriceType`  | **有效委託價格別**。([FBS][1])                                                                |
| `after_price`       | `float`            | **有效委託價格**。([FBS][1])                                                                 |
| `after_lot`         | `int`              | **有效委託口數**。([FBS][1])                                                                 |
| `filled_lot`        | `int`              | 成交口數。([FBS][1])                                                                       |
| `filled_money`      | `float`            | 成交價金。([FBS][1])                                                                       |
| `before_lot`        | `int`              | 改單前有效口數。([FBS][1])                                                                    |
| `before_price`      | `float`            | 改單前有效價格。([FBS][1])                                                                    |
| `user_def`          | `string`           | 自訂欄位。([FBS][1])                                                                       |
| `last_time`         | `string`           | 最後異動時間（`HH:MM:SS`）。([FBS][1])                                                         |
| `detail`            | `list`             | **委託歷程**（僅於呼叫 *含歷程* 或 *歷史查詢* 介面才有值）。([FBS][1])                                        |
| `error_message`     | `string`           | 錯誤訊息（若有）。([FBS][1])                                                                   |

---

## Python 最小可用範例

```python
# 取得最新委託結果（可選擇盤別）
order_results = sdk.futopt.get_order_results(
    accounts.data[0],               # Account 物件
    FutOptMarketType.Future         # 或 FutureNight / Option / OptionNight；不帶則查全部
)

if order_results.is_success:
    for odr in order_results.data:
        print(odr.order_no, odr.status, odr.filled_lot, odr.after_price)
else:
    print("Error:", order_results.message)
```

> 官方文件中亦提供對應的請求/回傳範例；上例之欄位如 `status=10` 代表「委託成功」，`filled_lot` 為成交口數，`after_price/after_lot` 為目前有效價量。([FBS][1])

---

## 快速對照（常用列舉）

* **FutOptMarketType**：`Future`｜`FutureNight`｜`Option`｜`OptionNight`。
* **FutOptPriceType**：`Limit`｜`Market`｜`RangeMarket`｜`Reference`。
* **TimeInforce**：`ROD`｜`IOC`｜`FOK`。
* **FutOptOrderType**：`New`｜`Close`｜`Auto`｜`FdayTrade`。
* **BSAction**：`Buy`｜`Sell`。
* **CallPut**：`Call`｜`Put`。([FBS][1])

---

## 官方範例回傳（節錄）

```text
Result {
  is_success: True,
  data: [
    FutOptOrderResult{
      date: "2024/03/25",
      order_no: "C0001",
      market_type: Future,
      symbol: "FITF",
      price_type: Limit,
      price: 1822.6,
      lot: 2,
      time_in_force: ROD,
      order_type: Auto,
      status: 10,
      after_price: 1822.6,
      after_lot: 2,
      filled_lot: 0,
      last_time: "10:20:27",
      ...
    }
  ]
}
```

---

# 建立委託單（期貨/選擇權｜Python SDK）

**方法名**：`sdk.futopt.place_order(account, order_object, unblock=False)`
**用途**：以 Python SDK 對期貨/選擇權送出**單式或複式**委託單。`unblock=True` 可採**非阻塞**送單（背景處理，不等待回應）。([FBS][1])

---

## 請求（Request）

### 函式參數

| 參數             | 型別                                | 是否必填 | 說明                                  |
| -------------- | --------------------------------- | ---: | ----------------------------------- |
| `account`      | `Account`                         |   必填 | 交易帳號物件。([FBS][1])                   |
| `order_object` | `OrderObject`（實務上為 `FutOptOrder`） |   必填 | 委託內容（見下方欄位）。([FBS][1])              |
| `unblock`      | `bool`                            |   選填 | 預設 `False`。是否採用**非阻塞**下單。([FBS][1]) |

### `OrderObject / FutOptOrder` 常用欄位

（以下欄位用於新單與複式單；`*leg2` 欄位為複式第二腳）([FBS][1])

| 欄位              | 型別                 | 說明                                                                                       |
| --------------- | ------------------ | ---------------------------------------------------------------------------------------- |
| `buy_sell`      | `BSAction`         | 買賣別：`Buy` / `Sell`。([FBS][1])                                                            |
| `symbol`        | `string`           | 商品代號（如 `TXF` / `TXO20000E4`）。([FBS][1])                                                  |
| `price`         | `float`/`string`   | 委託價格。([FBS][1])                                                                          |
| `lot`           | `int`              | 口數。([FBS][1])                                                                            |
| `market_type`   | `FutOptMarketType` | 盤別：`Future`（期貨日盤）/ `FutureNight`（期貨夜盤）/ `Option`（選擇權日盤）/ `OptionNight`（選擇權夜盤）。([FBS][1]) |
| `price_type`    | `FutOptPriceType`  | 價格別：`Limit` / `Market` / `RangeMarket` / `Reference`。([FBS][1])                          |
| `time_in_force` | `TimeInForce`      | `ROD` / `IOC` / `FOK`。([FBS][1])                                                         |
| `order_type`    | `FutOptOrderType`  | `New`（新倉） / `Close`（平倉） / `Auto`（自動） / `FdayTrade`（當沖）。([FBS][1])                        |
| `user_def`      | `string`           | 自訂欄位（選填）。([FBS][1])                                                                      |
| `buy_sell2`     | `BSAction`         | **複式**第二腳買賣別。([FBS][1])                                                                  |
| `symbol2`       | `string`           | **複式**第二腳商品代號。([FBS][1])                                                                 |

> 註：完整回傳物件中亦包含 `currency`、`expiry_date`、`strike_price`、`call_put` 等資訊（選擇權時適用）。([FBS][1])

---

## 回應（Response）

最外層回傳 `Result` 物件：([FBS][1])

```text
Result {
  is_success: bool,          # 是否成功
  data: FutOptOrderResult,   # 委託資訊（單式/複式皆回 FutOptOrderResult）
  message: string | None     # is_success = False 時之錯誤訊息
}
```

### `FutOptOrderResult` 主要欄位

| 欄位                              | 型別                | 說明                                                                                                 |
| ------------------------------- | ----------------- | -------------------------------------------------------------------------------------------------- |
| `function_type`                 | `int`             | 功能別：`0`新單、`10`新單執行、`15`改價、`20`改量、`30`刪單、`90`失敗。([FBS][1])                                          |
| `status`                        | `int`             | 委託狀態：`0`預約、`4`中台收單、`8`後台傳送、`9`後台逾時、`10`委託成功、`30`刪單成功、`50`完全成交、`90`失敗。([FBS][1])                    |
| `price_type / after_price_type` | `FutOptPriceType` | 原始/有效價格別。([FBS][1])                                                                                |
| `price / after_price`           | `float`           | 原始/有效價格。([FBS][1])                                                                                 |
| `lot / after_lot / filled_lot`  | `int`             | 原始口數／有效口數／成交口數。([FBS][1])                                                                          |
| 其他                              |                   | 如 `order_no`、`symbol`、`expiry_date`、`call_put`、`buy_sell`、`last_time`、`error_message` 等。([FBS][1]) |

---

## 最小可用範例（單式 & 複式）

```python
from fubon_neo.sdk import FubonSDK, FutOptOrder
from fubon_neo.constant import (
    TimeInForce, FutOptOrderType, FutOptPriceType, FutOptMarketType, BSAction
)

sdk = FubonSDK()
accounts = sdk.login("你的身分證", "登入密碼", "憑證路徑", "憑證密碼")

# --- 單式範例：限價買進 1 口選擇權 ---
order = FutOptOrder(
    buy_sell = BSAction.Buy,
    symbol = "TXO20000E4",
    price = "530",
    lot = 1,
    market_type = FutOptMarketType.Option,
    price_type = FutOptPriceType.Limit,
    time_in_force = TimeInForce.ROD,
    order_type = FutOptOrderType.Auto,
    user_def = "From_Py"
)
res = sdk.futopt.place_order(accounts.data[0], order)   # 或 unblock=True
print(res.is_success, getattr(res, "message", None))

# --- 複式範例：Sell TXO20000E4 / Buy TXO19900E4 價差單 ---
spread = FutOptOrder(
    buy_sell  = BSAction.Sell,  symbol  = "TXO20000E4",
    buy_sell2 = BSAction.Buy,   symbol2 = "TXO19900E4",
    price = "90", lot = 1,
    market_type = FutOptMarketType.Option,
    price_type  = FutOptPriceType.Limit,
    time_in_force = TimeInForce.IOC,
    order_type = FutOptOrderType.Auto,
    user_def = "From_Py"
)
res2 = sdk.futopt.place_order(accounts.data[0], spread)
print(res2.is_success, getattr(res2, "message", None))
```

（以上欄位配置與範例結構對應官方頁面）([FBS][1])

---

## 常用列舉（快速對照）

* **FutOptMarketType**：`Future`｜`FutureNight`｜`Option`｜`OptionNight`。([FBS][1])
* **FutOptPriceType**：`Limit`｜`Market`｜`RangeMarket`｜`Reference`。([FBS][1])
* **TimeInForce**：`ROD`｜`IOC`｜`FOK`。([FBS][1])
* **FutOptOrderType**：`New`｜`Close`｜`Auto`｜`FdayTrade`。([FBS][1])
* **BSAction**：`Buy`｜`Sell`；**CallPut**：`Call`｜`Put`（選擇權）。([FBS][1])

---


[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/trade/PlaceOrder/ "建立委託單 | 富邦新一代 API｜程式交易的新武器"

---

# 取得委託單結果（Python｜期貨/選擇權）

**方法**：`sdk.futopt.get_order_results(account, market_type=None)`
**用途**：查詢帳戶在期貨/選擇權的**最新委託單狀態**（含有效價量、成交口數、狀態碼等）。([FBS][1])

---

## 請求參數（Request）

| 參數            | 型別                 |  必填 | 說明                                                                                        |
| ------------- | ------------------ | :-: | ----------------------------------------------------------------------------------------- |
| `account`     | `Account`          |  ✅  | 交易帳號物件。([FBS][1])                                                                         |
| `market_type` | `FutOptMarketType` |  ⛔  | 盤別（不帶＝查全部）：`Future`（日盤期貨）、`FutureNight`（夜盤期貨）、`Option`（日盤選）、`OptionNight`（夜盤選）。([FBS][1]) |

---

## 回傳包裝（Result）

```text
Result {
  is_success: bool,          # 是否成功
  data: List[FutOptOrderResult],  # 委託資訊清單
  message: string | None     # 失敗時之錯誤訊息
}
```

([FBS][1])

---

## `FutOptOrderResult` 欄位對照

| 欄位                  | 型別                 | 說明                                                                              |
| ------------------- | ------------------ | ------------------------------------------------------------------------------- |
| `function_type`     | int                | 功能別：`0`新單、`10`新單執行、`15`改價、`20`改量、`30`刪單、`90`失敗（Optional）。([FBS][1])             |
| `date`              | string             | 交易日期。([FBS][1])                                                                 |
| `seq_no`            | string             | 委託單流水序號。([FBS][1])                                                              |
| `branch_no`         | string             | 分公司代號。([FBS][1])                                                                |
| `account`           | string             | 帳號。([FBS][1])                                                                   |
| `order_no`          | string             | 委託書號。([FBS][1])                                                                 |
| `asset_type`        | int                | 資產：`1`期貨、`2`選擇權。([FBS][1])                                                      |
| `market`            | string             | 市場：`TAIMEX`。([FBS][1])                                                          |
| `market_type`       | `FutOptMarketType` | 盤別：`Future`／`Option`／`FutureNight`／`OptionNight`。([FBS][1])                     |
| `unit`              | int                | 單位數。([FBS][1])                                                                  |
| `currency`          | string             | 幣別。([FBS][1])                                                                   |
| `symbol`            | string             | 商品代號。([FBS][1])                                                                 |
| `expiry_date`       | string             | 到期年月。([FBS][1])                                                                 |
| `strike_price`      | float              | 履約價（期貨為 `None`）。([FBS][1])                                                      |
| `call_put`          | `CallPut`          | 買賣權：`Call`／`Put`（期貨為 `None`）。([FBS][1])                                         |
| `buy_sell`          | `BSAction`         | 買賣別：`Buy`／`Sell`。([FBS][1])                                                     |
| `symbol_leg2`       | string             | 複式第二腳商品。([FBS][1])                                                              |
| `expiry_date_leg2`  | string             | 複式第二腳到期。([FBS][1])                                                              |
| `strike_price_leg2` | float              | 複式第二腳履約價。([FBS][1])                                                             |
| `call_put_leg2`     | `CallPut`          | 複式第二腳買賣權。([FBS][1])                                                             |
| `buy_sell_leg2`     | `BSAction`         | 複式第二腳買賣別。([FBS][1])                                                             |
| `price_type`        | `FutOptPriceType`  | 原始委託價格別：`Limit`/`Market`/`RangeMarket`/`Reference`。([FBS][1])                   |
| `price`             | float              | 原始委託價格。([FBS][1])                                                               |
| `lot`               | int                | 原始委託口數。([FBS][1])                                                               |
| `time_in_force`     | `TimeInforce`      | `ROD`/`FOK`/`IOC`。([FBS][1])                                                    |
| `order_type`        | `FutOptOrderType`  | `New`/`Close`/`Auto`/`FdayTrade`。([FBS][1])                                     |
| `is_pre_order`      | bool               | 是否預約單。([FBS][1])                                                                |
| `status`            | int                | 委託狀態：`0`預約、`4`中台收單、`8`後台傳送、`9`後台逾時、`10`委託成功、`30`刪單成功、`50`完全成交、`90`失敗。([FBS][1]) |
| `after_price_type`  | `FutOptPriceType`  | **有效**價格別。([FBS][1])                                                            |
| `after_price`       | float              | **有效**委託價格。([FBS][1])                                                           |
| `after_lot`         | int                | **有效**委託口數。([FBS][1])                                                           |
| `filled_lot`        | int                | 成交口數。([FBS][1])                                                                 |
| `filled_money`      | float              | 成交價金。([FBS][1])                                                                 |
| `before_lot`        | int                | 改單前有效口數。([FBS][1])                                                              |
| `before_price`      | float              | 改單前有效價格。([FBS][1])                                                              |
| `user_def`          | string             | 自訂欄位。([FBS][1])                                                                 |
| `last_time`         | string             | 最後異動時間（`HH:MM:SS`）。([FBS][1])                                                   |
| `detail`            | list               | 委託歷程（僅在 *含歷程* 或 *歷史查詢* 介面才有值）。([FBS][1])                                        |
| `error_message`     | string             | 錯誤訊息（若有）。([FBS][1])                                                             |

---

## 最小可用範例

```python
# 取得最新委託結果（可透過 market_type 篩選盤別）
res = sdk.futopt.get_order_results(
    accounts.data[0],
    FutOptMarketType.Future  # 或 FutureNight / Option / OptionNight；不帶則全部
)

if res.is_success:
    for odr in res.data:
        print(odr.order_no, odr.status, odr.filled_lot, odr.after_price)
else:
    print("Error:", res.message)
```

> 常見判讀：`status=10` 代表「委託成功」；`50` 代表「完全成交」。`after_price/after_lot` 為目前有效價量；`filled_lot` 為累計成交口數。([FBS][1])

---

## 常用列舉（速查）

* **FutOptMarketType**：`Future`｜`FutureNight`｜`Option`｜`OptionNight`
* **FutOptPriceType**：`Limit`｜`Market`｜`RangeMarket`｜`Reference`
* **TimeInforce**：`ROD`｜`IOC`｜`FOK`
* **FutOptOrderType**：`New`｜`Close`｜`Auto`｜`FdayTrade`
* **BSAction**：`Buy`｜`Sell`；**CallPut**：`Call`｜`Put`（選擇權） ([FBS][1])

---


[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/trade/GetOrderResults "取得委託單結果 | 富邦新一代 API｜程式交易的新武器"

---

# 修改委託單數量（期貨/選擇權｜Python SDK）

**方法名稱**：`sdk.futopt.modify_lot(account, futOptModifyLot, unblock=False)`
**用途**：將既有委託單「改量」（含已成交部位也計入修改後數量）。 ([FBS][1])

---

## 建立改量物件

先以 `make_modify_lot_obj` 產生 `FutOptModifyLot`： ([FBS][1])

| 參數            | 類別                  | 說明                     |
| ------------- | ------------------- | ---------------------- |
| `orderResult` | `FutOptOrderResult` | 欲修改的目標委託單              |
| `lot`         | `int`               | **修改後的委託量**（包含該單已成交口數） |

> 產出之 `FutOptModifyLot` 即為 `modify_lot()` 的第二個參數。 ([FBS][1])

---

## 請求（Request）

| 參數                | 類別                | 是否必填 | 說明                               |
| ----------------- | ----------------- | ---: | -------------------------------- |
| `account`         | `Account`         |    ✅ | 交易帳號物件                           |
| `futOptModifyLot` | `FutOptModifyLot` |    ✅ | 改量物件（由 `make_modify_lot_obj` 產生） |
| `unblock`         | `bool`            |    ⛔ | 預設 `False`；是否採用**非阻塞**改量模式       |
| ([FBS][1])        |                   |      |                                  |

---

## 回傳（Response）

頂層回傳 `Result` 物件： ([FBS][1])

```text
Result {
  is_success: bool,                # 是否成功
  data: FutOptOrderResult,         # 改單後的委託資訊
  message: string | None           # 失敗時之錯誤訊息
}
```

### `FutOptOrderResult`（重點欄位）

| 欄位                               | 類別          | 說明                                                                                          |
| -------------------------------- | ----------- | ------------------------------------------------------------------------------------------- |
| `function_type`                  | `int`       | 功能別：`20`＝改量（另有 `0`新單、`15`改價、`30`刪單…）                                                        |
| `status`                         | `int`       | 委託狀態：`10`委託成功、`50`完全成交、`90`失敗等                                                              |
| `after_lot`                      | `int`       | **有效委託口數（改量後）**                                                                             |
| `before_lot`                     | `int`       | 改單前有效口數                                                                                     |
| `filled_lot`                     | `int`       | 成交口數（累計）                                                                                    |
| `after_price_type / after_price` | 見列舉/`float` | 有效價格別／有效價                                                                                   |
| 其他常見欄位                           |             | 如 `order_no`、`symbol`、`buy_sell`、`time_in_force`、`order_type`、`last_time`、`error_message` 等 |
| （完整欄位清單請見官方頁面。） ([FBS][1])       |             |                                                                                             |

---

## 最小可用範例

```python
# 先從最新委託清單挑選要改量的單
target_order = order_results.data[0]  # FutOptOrderResult

# 例如：將該單改為 2 口（包含已成交口數）
modify_lot_obj = sdk.futopt.make_modify_lot_obj(target_order, 2)

# 送出改量（可選用非阻塞模式 unblock=True）
res = sdk.futopt.modify_lot(accounts.data[0], modify_lot_obj)

if res.is_success:
    odr = res.data
    print("改量成功:", odr.order_no, "after_lot=", odr.after_lot, "filled_lot=", odr.filled_lot)
else:
    print("改量失敗:", res.message)
```

> 官方回傳樣例中，`function_type=20` 代表「改量」；`after_lot` 為改後有效口數；`before_lot` 為改前有效口數。 ([FBS][1])

---

## 相關列舉（速查）

* **FutOptMarketType**：`Future`｜`Option`｜`FutureNight`｜`OptionNight`
* **FutOptPriceType**：`Limit`｜`Market`｜`RangeMarket`｜`Reference`
* **TimeInforce**：`ROD`｜`IOC`｜`FOK`
* **FutOptOrderType**：`New`｜`Close`｜`Auto`｜`FdayTrade`
* **BSAction**：`Buy`｜`Sell`
  （以上皆見該頁與 Reference 導覽列。） ([FBS][1])

---

[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/trade/ModifyQuantity/ "修改委託單數量 | 富邦新一代 API｜程式交易的新武器"

---

# 刪除委託單（期貨/選擇權｜Python SDK）

**方法**：`sdk.futopt.cancel_order(account, order_result, unblock=False)`
**用途**：取消既有委託單。支援**非阻塞**模式（`unblock=True`）。([FBS][1])

---

## 請求參數（Request）

| 參數             | 類別                  |  必填 | 說明                                                 |
| -------------- | ------------------- | :-: | -------------------------------------------------- |
| `account`      | `Account`           |  ✅  | 交易帳號物件。([FBS][1])                                  |
| `order_result` | `FutOptOrderResult` |  ✅  | 欲取消之委託單物件（通常由 `get_order_results()` 取得）。([FBS][1]) |
| `unblock`      | `bool`              |  ⛔  | 預設 `False`；是否採**非阻塞**送單。([FBS][1])                 |

---

## 回傳包裝（Response）

回傳 `Result` 物件：

```text
Result {
  is_success: bool,           # 是否成功
  data: FutOptOrderResult,    # 取消後的最新委託資訊
  message: string | None      # 失敗時之錯誤訊息
}
```

([FBS][1])

---

## `FutOptOrderResult`（重點欄位對照）

> 取消成功時 `function_type=30`、`status` 可能回「30 刪單成功」。其餘欄位結構與下單/查單一致。([FBS][1])

| 欄位                                 | 類別                            | 說明                                                                                 |
| ---------------------------------- | ----------------------------- | ---------------------------------------------------------------------------------- |
| `function_type`                    | `int`                         | 功能別：`0`新單、`10`新單執行、`15`改價、`20`改量、`30`刪單、`90`失敗。([FBS][1])                          |
| `status`                           | `int`                         | 委託狀態：`0`預約、`4`中台收單、`8`後台傳送、`9`逾時、`10`委託成功、`30`刪單成功、`50`完全成交、`90`失敗。([FBS][1])      |
| `order_no` / `seq_no`              | `string`                      | 委託書號／委託單流水序號。([FBS][1])                                                            |
| `symbol` / `market_type`           | `string` / `FutOptMarketType` | 商品代號；盤別：`Future`/`Option`/`FutureNight`/`OptionNight`。([FBS][1])                   |
| `price_type` / `price`             | `FutOptPriceType` / `float`   | 原始委託價格別／價格。([FBS][1])                                                              |
| `after_price_type` / `after_price` | 同左                            | 有效價格別／價格。([FBS][1])                                                                |
| `after_lot` / `filled_lot`         | `int`                         | 有效口數／成交口數。([FBS][1])                                                               |
| 其他                                 |                               | 如 `buy_sell`、`time_in_force`、`order_type`、`last_time`、`error_message` 等。([FBS][1]) |

---

## 最小可用範例

```python
# 先查到要取消的委託單（示例取第一筆）
orders = sdk.futopt.get_order_results(accounts.data[0])
target = orders.data[0]  # FutOptOrderResult

# 取消該筆委託（可改用 unblock=True）
res = sdk.futopt.cancel_order(accounts.data[0], target)

if res.is_success:
    odr = res.data
    print("刪單成功:", odr.order_no, "status=", odr.status, "function_type=", odr.function_type)
else:
    print("刪單失敗:", res.message)
```

> 官方頁面提供的回傳樣例中，`function_type=30`、`status=30` 代表**刪單成功**；其餘欄位（如 `after_lot`、`after_price`、`filled_lot` 等）反映刪單後有效狀態。([FBS][1])

---

## 速查列舉

* **FutOptMarketType**：`Future`｜`Option`｜`FutureNight`｜`OptionNight`
* **FutOptPriceType**：`Limit`｜`Market`｜`RangeMarket`｜`Reference`
* **TimeInforce**：`ROD`｜`IOC`｜`FOK`
* **FutOptOrderType**：`New`｜`Close`｜`Auto`｜`FdayTrade`
* **BSAction**：`Buy`｜`Sell`（選擇權另有 `CallPut` 列舉） ([FBS][1])

---

[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/trade/CancelOrder "刪除委託單 | 富邦新一代 API｜程式交易的新武器"

---

# 查詢歷史委託（期貨/選擇權｜Python SDK）

**方法**：`sdk.futopt.order_history(account, start_date, end_date, market_type=None)`
**用途**：查詢帳戶在指定日期區間內的歷史委託（支援依盤別過濾）。([FBS][1])

> ℹ️ **時效限制**：僅能查詢「最近兩日」的歷史資料。([FBS][1])

---

## 請求參數（Request）

| 參數            | 型別                 |  必填 | 說明                                                                                            |
| ------------- | ------------------ | :-: | --------------------------------------------------------------------------------------------- |
| `account`     | `Account`          |  ✅  | 交易帳號物件。([FBS][1])                                                                             |
| `start_date`  | `string`           |  ✅  | 查詢開始日（格式如 `YYYYMMDD`）。([FBS][1])                                                              |
| `end_date`    | `string`           |  ✅  | 查詢終止日（格式如 `YYYYMMDD`）。([FBS][1])                                                              |
| `market_type` | `FutOptMarketType` |  ⛔  | 盤別（不帶＝查全部）：`Future`（期貨日盤）、`FutureNight`（期貨夜盤）、`Option`（選擇權日盤）、`OptionNight`（選擇權夜盤）。([FBS][1]) |

---

## 回傳包裝（Response）

```text
Result {
  is_success: bool,       # 是否成功
  data: List[FutOptOrderResult],  # 歷史委託清單
  message: string | None  # 失敗時之錯誤訊息
}
```

([FBS][1])

---

## `FutOptOrderResult` 欄位對照（重點）

| 欄位                                 | 型別                        | 說明                                                                                                         |
| ---------------------------------- | ------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `function_type`                    | int (Optional)            | 功能別：`0`新單、`10`新單執行、`15`改價、`20`改量、`30`刪單、`90`失敗。([FBS][1])                                                  |
| `date`                             | string                    | 交易日期。([FBS][1])                                                                                            |
| `seq_no` / `order_no`              | string                    | 委託單流水序號／委託書號。([FBS][1])                                                                                    |
| `branch_no` / `account`            | string                    | 分公司代號／帳號。([FBS][1])                                                                                        |
| `asset_type`                       | int                       | 資產類別：`1`期貨、`2`選擇權。([FBS][1])                                                                               |
| `market`                           | string                    | 市場類型：`TAIMEX`。([FBS][1])                                                                                   |
| `market_type`                      | `FutOptMarketType`        | `Future`／`Option`／`FutureNight`／`OptionNight`。([FBS][1])                                                   |
| `unit` / `currency`                | int / string              | 單位數／幣別。([FBS][1])                                                                                          |
| `symbol` / `expiry_date`           | string                    | 商品代號／到期年月。([FBS][1])                                                                                       |
| `strike_price` / `call_put`        | float / `CallPut`         | 履約價；買賣權 `Call`/`Put`（期貨為 `None`）。([FBS][1])                                                                |
| `buy_sell`                         | `BSAction`                | 買賣別：`Buy`/`Sell`。([FBS][1])                                                                                |
| `*leg2`                            | 同上                        | 複式第二腳對應欄位（`symbol_leg2`、`expiry_date_leg2`、`strike_price_leg2`、`call_put_leg2`、`buy_sell_leg2`）。([FBS][1]) |
| `price_type` / `price`             | `FutOptPriceType` / float | 原始委託價格別／價格。([FBS][1])                                                                                      |
| `lot`                              | int                       | 原始委託口數。([FBS][1])                                                                                          |
| `time_in_force`                    | `TimeInforce`             | `ROD`/`FOK`/`IOC`。([FBS][1])                                                                               |
| `order_type`                       | `FutOptOrderType`         | `New`/`Close`/`Auto`/`FdayTrade`。([FBS][1])                                                                |
| `is_pre_order`                     | bool                      | 是否為預約單。([FBS][1])                                                                                          |
| `status`                           | int                       | 狀態：`0`預約、`4`中台收單、`8`後台傳送、`9`逾時、`10`委託成功、`30`刪單成功、`50`完全成交、`90`失敗。([FBS][1])                                |
| `after_price_type` / `after_price` | `FutOptPriceType` / float | **有效**價格別／價格。([FBS][1])                                                                                    |
| `after_lot`                        | int                       | **有效**委託口數。([FBS][1])                                                                                      |
| `filled_lot` / `filled_money`      | int / float               | 成交口數／成交價金。([FBS][1])                                                                                       |
| `before_lot` / `before_price`      | int / float               | 改單前有效口數／價格。([FBS][1])                                                                                      |
| `user_def`                         | string                    | 自訂欄位。([FBS][1])                                                                                            |
| `last_time`                        | string                    | 最後異動時間。([FBS][1])                                                                                          |
| `detail`                           | list<OrderDetail>         | **委託歷程**（若有）。見下表。([FBS][1])                                                                                |
| `error_message`                    | string                    | 錯誤訊息。([FBS][1])                                                                                            |

### `OrderDetail`（委託歷程）欄位

| 欄位                             | 型別     | 說明                                                   |
| ------------------------------ | ------ | ---------------------------------------------------- |
| `function_type`                | int    | `10`新單、`15`改價、`20`改量、`30`刪單、`50`成交、`90`失敗。([FBS][1]) |
| `modified_time`                | string | 修改時間（`HH:MM:SS`）。([FBS][1])                          |
| `before_lot` / `after_lot`     | int    | 原始口數／有效口數。([FBS][1])                                 |
| `before_price` / `after_price` | float  | 原始價格／有效價格。([FBS][1])                                 |
| `error_message`                | string | 錯誤訊息。([FBS][1])                                      |

---

## 最小可用範例

```python
# 查詢 2024-04-10 ~ 2024-04-11 的歷史委託（不指定盤別＝全盤別）
res = sdk.futopt.order_history(accounts.data[0], "20240410", "20240411")

if res.is_success:
    for odr in res.data:  # 每筆為 FutOptOrderResult
        print(odr.date, odr.order_no, odr.status, odr.after_lot, odr.after_price)
else:
    print("Error:", res.message)
```

（官方頁面亦提供同樣的請求與回傳樣例；上例中的 `status=50` 代表完全成交。）([FBS][1])

---

## 常用列舉速查

* **FutOptMarketType**：`Future`｜`FutureNight`｜`Option`｜`OptionNight`
* **FutOptPriceType**：`Limit`｜`Market`｜`RangeMarket`｜`Reference`
* **TimeInforce**：`ROD`｜`IOC`｜`FOK`
* **FutOptOrderType**：`New`｜`Close`｜`Auto`｜`FdayTrade`
* **BSAction**：`Buy`｜`Sell`；**CallPut**：`Call`｜`Put`（選擇權） ([FBS][1])

---


[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/trade/OrderHistory "查詢歷史委託 | 富邦新一代 API｜程式交易的新武器"

---

# 查詢歷史成交（Python｜期貨 / 選擇權 SDK）

**方法**：`sdk.futopt.filled_history(account, market_type, start_date, end_date=None)`
**用途**：查詢指定期間內的成交紀錄（包含單式與複式）。

> 注意：只能查 **最近兩日** 的成交資料。([FBS][1])

---

## 請求參數（Request）

| 參數            | 型別                 |  必填 | 說明                                                                |
| ------------- | ------------------ | :-: | ----------------------------------------------------------------- |
| `account`     | `Account`          |  ✅  | 交易帳號物件。([FBS][1])                                                 |
| `market_type` | `FutOptMarketType` |  ✅  | 盤別：`Future` / `FutureNight` / `Option` / `OptionNight`。([FBS][1]) |
| `start_date`  | `string`           |  ✅  | 查詢開始日，格式如 `"YYYYMMDD"`。([FBS][1])                                 |
| `end_date`    | `string`           |  ⛔  | 查詢終止日，若不帶預設與 `start_date` 相同。([FBS][1])                           |

---

## 回傳結構（Result）

```text
Result {
  is_success: bool,
  data: List[FutOptFilledData],
  message: string | None
}
```

* `is_success`：查詢是否成功
* `data`：成交紀錄清單，每筆為 `FutOptFilledData`
* `message`：若失敗時的錯誤訊息 ([FBS][1])

---

## 成交資料型別：`FutOptFilledData`

以下是 `FutOptFilledData` 的欄位清單與說明：([FBS][1])
（對於複式單，也會包含第二腳欄位）

| 欄位                  | 型別                 | 說明                                            |
| ------------------- | ------------------ | --------------------------------------------- |
| `date`              | `string`           | 成交日期（如 `"2023/09/15"`）([FBS][1])              |
| `branch_no`         | `string`           | 分公司代號([FBS][1])                               |
| `account`           | `string`           | 帳號([FBS][1])                                  |
| `seq_no`            | `string` or None   | 委託單流水號（主動回報時才會有值）([FBS][1])                   |
| `order_no`          | `string`           | 委託書號([FBS][1])                                |
| `symbol`            | `string`           | 商品代號([FBS][1])                                |
| `expiry_date`       | `string`           | 到期年月（若是期權）([FBS][1])                          |
| `strike_price`      | `float`            | 履約價（若為期權；期貨為 None）([FBS][1])                  |
| `call_put`          | `CallPut` or None  | 買權 / 賣權（期貨則為 None）([FBS][1])                  |
| `buy_sell`          | `BSAction`         | 買賣別：`Buy` / `Sell`([FBS][1])                  |
| `symbol_leg2`       | `string` or None   | 複式單第二腳商品代號（如無則為 None）([FBS][1])               |
| `expiry_date_leg2`  | `string` or None   | 第二腳到期年月([FBS][1])                             |
| `strike_price_leg2` | `float` or None    | 第二腳履約價([FBS][1])                              |
| `call_put_leg2`     | `CallPut` or None  | 第二腳買賣權別([FBS][1])                             |
| `buy_sell_leg2`     | `BSAction` or None | 第二腳買賣別([FBS][1])                              |
| `filled_no`         | `string`           | 成交流水號([FBS][1])                               |
| `filled_avg_price`  | `float`            | 成交均價([FBS][1])                                |
| `filled_lots`       | `int`              | 成交口數([FBS][1])                                |
| `filled_price`      | `float`            | 成交單價([FBS][1])                                |
| `order_type`        | `FutOptOrderType`  | 委託單類型：`New` / `Close` / `FdayTrade`([FBS][1]) |
| `filled_time`       | `string`           | 成交時間（格式可能包含毫秒）([FBS][1])                      |
| `user_def`          | `string` or None   | 使用者自定義欄位（僅主動回報有值）([FBS][1])                   |

---

## 請求 / 回傳範例

* **請求範例**：

  ````python
  sdk.futopt.filled_history(account, FutOptMarketType.Future, "20230921", "20230922")
  ``` :contentReference[oaicite:30]{index=30}

  ````
* **回傳範例（節錄）**：

  ````text
  Result {
    is_success: True,
    message: None,
    data: [
      FutOptFilledData{
        date: "2023/09/15",
        branch_no: "6460",
        account: "26",
        order_no: "bA422",
        seq_no: None,
        symbol: "FITX",
        expiry_date: "202404",
        strike_price: None,
        call_put: None,
        buy_sell: Buy,
        filled_no: "00000000001",
        filled_avg_price: 20890.0,
        filled_lots: 1,
        filled_price: 20890.0,
        order_type: New,
        filled_time: "10:31:00.931",
        user_def: None
      },
      ...
    ]
  }
  ``` :contentReference[oaicite:31]{index=31}
  ````

---



[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/trade/FilledHistory "查詢歷史成交 | 富邦新一代 API｜程式交易的新武器"

---

# 商品保證金查詢 （Python SDK）

**方法**：`sdk.futopt.query_estimate_margin(account, order_object)`
（文檔中稱 `query_estimate_margin`）
**用途**：對一筆委託單（尚未送出）進行保證金估算。([turn0view0])

---

## 請求參數（Request）

| 參數             | 型別                             |  必填 | 說明            |
| -------------- | ------------------------------ | :-: | ------------- |
| `account`      | `Account`                      |  ✅  | 交易帳號物件。       |
| `order_object` | `OrderObject`（如 `FutOptOrder`） |  ✅  | 要估算保證金的委託單物件。 |

---

## 回傳結構（Result）

```text
Result {
  is_success: bool,
  data: EstimateMargin,
  message: string | None
}
```

* `is_success`：是否查詢成功
* `data`：保證金估算資訊，型別為 `EstimateMargin`
* `message`：若失敗，回傳錯誤訊息。([turn0view0])

---

## `EstimateMargin` 欄位定義

| 欄位                | 型別       | 說明                        |
| ----------------- | -------- | ------------------------- |
| `date`            | `string` | 查詢日期（格式例如 `"2024/04/10"`） |
| `currency`        | `string` | 幣別（例如 `"TWD"`）            |
| `estimate_margin` | `float`  | 預估保證金數額                   |

---

## 使用範例（請求 + 回傳）

```python
order = FutOptOrder(
    buy_sell = BSAction.Buy,
    symbol = "TXFE4",
    price = "20890",
    lot = 1,
    market_type = FutOptMarketType.Future,
    price_type = FutOptPriceType.Limit,
    time_in_force = TimeInForce.ROD,
    order_type = FutOptOrderType.New,
    user_def = "From_Py"  # （此欄位為選填）
)

res = sdk.futopt.query_estimate_margin(accounts.data[0], order)

if res.is_success:
    est = res.data
    print("查詢日期：", est.date)
    print("幣別：", est.currency)
    print("保證金：", est.estimate_margin)
else:
    print("估算失敗：", res.message)
```

**回傳範例**：

```text
Result {
  is_success: True,
  message: None,
  data: EstimateMargin {
    date: "2024/04/10",
    currency: "TWD",
    estimate_margin: 179000
  }
}
```

---


---

# 商品代號轉換（Python SDK）

**方法**：`sdk.futopt.convert_symbol(symbol, expiry_date, strike_price=None, call_put=None)`
**用途**：將「帳務商品代號 + 其他屬性」轉為可用於行情查詢或下單的商品代號。([FBS][1])

---

## 請求參數（Request）

| 參數             | 型別                 | 是否必填 | 說明                                                    |
| -------------- | ------------------ | :--: | ----------------------------------------------------- |
| `symbol`       | `string`           |   ✅  | 帳務系統的商品代號，如期貨或選擇權的標的代號。([FBS][1])                     |
| `expiry_date`  | `string`           |   ✅  | 履約／到期年月，以 `YYYYMM` 格式表示。([FBS][1])                    |
| `strike_price` | `float` 或 `None`   |   ⛔  | （選擇權專用）履約價。若為期貨，請留空或傳 `None`。([FBS][1])               |
| `call_put`     | `CallPut` 或 `None` |   ⛔  | （選擇權專用）買權／賣權：`Call` 或 `Put`。若為期貨，請傳 `None`。([FBS][1]) |

---

## 回傳（Result）

| 欄位       | 型別       | 說明                                           |
| -------- | -------- | -------------------------------------------- |
| `symbol` | `string` | 轉換後可用於行情查詢或下單的商品代號，例如期貨或選擇權的完整識別碼。([FBS][1]) |

---

## 範例

```python
# 期貨代號轉換
converted = sdk.futopt.convert_symbol("FITX", "202404")
# 回傳："TXFD4"  （假設）:contentReference[oaicite:7]{index=7}

# 選擇權代號轉換
converted2 = sdk.futopt.convert_symbol("TXO", "202404", 20000, CallPut.Call)
# 回傳："TXO20000D4"  （假設）:contentReference[oaicite:8]{index=8}
```

---


[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/trade/ConvertSymbol "商品代號轉換 | 富邦新一代 API｜程式交易的新武器"


---

# 混合部位查詢（HybridPosition） — Python SDK

**方法**：`sdk.futopt_accounting.query_hybrid_position(account)`
**用途**：查詢帳戶在期貨／選擇權的所有混合部位（單式部位 / 複式部位）資訊。 ([FBS][1])

---

## 請求參數（Request）

| 參數        | 型別        |  必填 | 說明                 |
| --------- | --------- | :-: | ------------------ |
| `account` | `Account` |  ✅  | 交易帳號物件。 ([FBS][1]) |

---

## 回傳結構（Result）

```text
Result {
  is_success: bool,
  data: List[HybridPosition],
  message: string | None
}
```

* `is_success`：查詢是否成功
* `data`：混合部位清單，每筆為 `HybridPosition`
* `message`：若失敗，返回的錯誤訊息 ([FBS][1])

---

## `HybridPosition` 欄位定義

| 欄位                   | 型別                              | 說明                                              |
| -------------------- | ------------------------------- | ----------------------------------------------- |
| `date`               | `string`                        | 部位建立日期（格式例如 `"2024/04/08"`） ([FBS][1])          |
| `branch_no`          | `string`                        | 分公司代號 ([FBS][1])                                |
| `account`            | `string`                        | 帳號 ([FBS][1])                                   |
| `is_spread`          | `bool`                          | 是否為複式部位（spread） ([FBS][1])                      |
| `position_kind`      | `int`                           | 部位種類：`1`＝期貨、`2`＝選擇權 ([FBS][1])                  |
| `symbol`             | `string`                        | 商品代號 ([FBS][1])                                 |
| `expiry_date`        | `string`                        | 履約／到期年月（例如 `"202404"`） ([FBS][1])               |
| `strike_price`       | `float` 或 `None`                | 履約價（對期貨為 None） ([FBS][1])                       |
| `call_put`           | `CallPut` 或 `None`              | 買／賣權：`Call`／`Put`（期貨為 None） ([FBS][1])          |
| `buy_sell`           | `BSAction`                      | 買賣別：`Buy`／`Sell` ([FBS][1])                     |
| `price`              | `float`                         | 成交價 ([FBS][1])                                  |
| `orig_lots`          | `int`                           | 原始口數 ([FBS][1])                                 |
| `tradable_lots`      | `int`                           | 可交易口數（可下單部分） ([FBS][1])                         |
| `order_type`         | `FutOptOrderType`               | 委託類型：`New` / `Close` / `FdayTrade` 等 ([FBS][1]) |
| `currency`           | `string`                        | 幣別（例如 `"TWD"`） ([FBS][1])                       |
| `market_price`       | `string`                        | 即時市場價格（字串） ([FBS][1])                           |
| `initial_margin`     | `float`                         | 原始保證金 ([FBS][1])                                |
| `maintenance_margin` | `float`                         | 維持保證金 ([FBS][1])                                |
| `clearing_margin`    | `float`                         | 結算保證金 ([FBS][1])                                |
| `opt_value`          | `float`                         | 選擇權市值（對選擇權部位） ([FBS][1])                        |
| `opt_long_value`     | `float`                         | 選擇權買進市值 ([FBS][1])                              |
| `opt_short_value`    | `float`                         | 選擇權賣出市值 ([FBS][1])                              |
| `profit_or_loss`     | `float`                         | 部位的損益 ([FBS][1])                                |
| `premium`            | `float`                         | 權利金（對選擇權部位） ([FBS][1])                          |
| `spreads`            | `List[SpreadPosition]` 或 `None` | 若為複式部位，提供其分腳部位解析列表 ([FBS][1])                   |

---

## `SpreadPosition`（複式部位腳位）欄位

在 `HybridPosition.spreads` 若非空，則每個 `SpreadPosition` 的欄位類似於 `HybridPosition`，但用於第二腳拆解：

| 欄位                                                          | 型別         | 說明                          |
| ----------------------------------------------------------- | ---------- | --------------------------- |
| `date`                                                      | `string`   | 部位建立日期 ([FBS][1])           |
| `branch_no`                                                 | `string`   | 分公司代號 ([FBS][1])            |
| `account`                                                   | `string`   | 帳號 ([FBS][1])               |
| `position_kind`                                             | int        | 部位種類（期貨／選擇權） ([FBS][1])     |
| `symbol`                                                    | `string`   | 第二腳商品代號 ([FBS][1])          |
| `expiry_date`                                               | `string`   | 到期年月 ([FBS][1])             |
| `strike_price`                                              | `float`    | 履約價 ([FBS][1])              |
| `call_put`                                                  | `CallPut`  | 權利別（Call／Put） ([FBS][1])    |
| `buy_sell`                                                  | `BSAction` | 買賣別 ([FBS][1])              |
| `price`                                                     | `float`    | 成交價 ([FBS][1])              |
| `orig_lots`                                                 | `int`      | 原始口數 ([FBS][1])             |
| `tradable_lot`                                              | `int`      | 可交易口數 ([FBS][1])            |
| `currency`                                                  | `string`   | 幣別 ([FBS][1])               |
| `market_price`                                              | `string`   | 即時市場價格 ([FBS][1])           |
| `initial_margin` / `maintenance_margin` / `clearing_margin` | `float`    | 各式保證金數值 ([FBS][1])          |
| `opt_value`, `opt_long_value`, `opt_short_value`            | `float`    | 各式選擇權價值（若為選擇權腳位） ([FBS][1]) |
| `profit_or_loss`                                            | `float`    | 腳位損益 ([FBS][1])             |
| `premium`                                                   | `float`    | 權利金 ([FBS][1])              |

---

## 請求範例

```python
res = sdk.futopt_accounting.query_hybrid_position(account)

if res.is_success:
    for hp in res.data:
        print(hp.symbol, hp.orig_lots, hp.tradable_lots, hp.profit_or_loss)
else:
    print("Error:", res.message)
```

（官方文件即有此範例） ([FBS][1])

---


[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/accountManagement/HybridPosition "混合部位查詢 | 富邦新一代 API｜程式交易的新武器"


（來源：富邦 API 文件）([FBS][1])

---

## 單式部位查詢（SinglePosition）

### 方法名稱 / 路徑

* SDK 方法： `sdk.futopt_accounting.query_single_position(account)` ([FBS][1])
* 所屬分類：帳務 / 帳戶部位查詢 ([FBS][1])

---

## 輸入參數（Request）

| 參數        | 類型        | 是否必填 | 說明                             |
| --------- | --------- | :--: | ------------------------------ |
| `account` | `Account` |   ✅  | 交易帳號物件，用以識別該帳號的部位資料。([FBS][1]) |

---

## 回傳結果（Result）

```text
Result {
  is_success: bool,           # 是否成功取得部位資料
  data: List[Position],       # 單式部位列表
  message: string | None      # 若 is_success = False，返回錯誤訊息
}
```

* `is_success` 為布林值，代表查詢是否成功 ([FBS][1])
* `data` 是一列 `Position`（部位資料）物件陣列 ([FBS][1])
* `message` 在失敗時回傳錯誤原因 ([FBS][1])

---

## `Position`（部位資料）欄位定義

下表是 `Position` 物件所包含的欄位，皆為該帳號在單式部位情況下的資料： ([FBS][1])

| 欄位                   | 類型                 | 說明                                                  |
| -------------------- | ------------------ | --------------------------------------------------- |
| `date`               | `string`           | 部位建立日期（格式如 `"2024/04/08"`）。([FBS][1])               |
| `branch_no`          | `string`           | 分公司代號。([FBS][1])                                    |
| `account`            | `string`           | 帳號。([FBS][1])                                       |
| `is_spread`          | `bool`             | 是否為複式部位（若為單式應為 `False`）。([FBS][1])                  |
| `position_kind`      | `int`              | 部位種類：`1` 為期貨、`2` 為選擇權。([FBS][1])                    |
| `symbol`             | `string`           | 商品代號。([FBS][1])                                     |
| `expiry_date`        | `string`           | 履約／到期年月（如 `"202404"`）。([FBS][1])                    |
| `strike_price`       | `float` 或 `None`   | 履約價（若為期貨則為 `None`）。([FBS][1])                       |
| `call_put`           | `CallPut` 或 `None` | 權利別：`Call` / `Put`（若為期貨則為 `None`）。([FBS][1])        |
| `buy_sell`           | `BSAction`         | 買賣別：`Buy` / `Sell`。([FBS][1])                       |
| `price`              | `float`            | 成交價。([FBS][1])                                      |
| `orig_lots`          | `int`              | 原始持倉口數。([FBS][1])                                   |
| `tradable_lots`      | `int`              | 可交易口數（也就是可針對該部位下單的量）。([FBS][1])                     |
| `order_type`         | `FutOptOrderType`  | 部位所屬委託類型：`New` / `Close` / `FdayTrade` 等。([FBS][1]) |
| `currency`           | `string`           | 幣別（如 `"TWD"`）。([FBS][1])                            |
| `market_price`       | `string`           | 即時市場價（字串型態）。([FBS][1])                              |
| `initial_margin`     | `float`            | 原始保證金金額。([FBS][1])                                  |
| `maintenance_margin` | `float`            | 維持保證金金額。([FBS][1])                                  |
| `clearing_margin`    | `float`            | 結算保證金金額。([FBS][1])                                  |
| `opt_value`          | `float`            | 選擇權市值（若為選擇權部位）。([FBS][1])                           |
| `opt_long_value`     | `float`            | 選擇權買進市值。([FBS][1])                                  |
| `opt_short_value`    | `float`            | 選擇權賣出市值。([FBS][1])                                  |
| `profit_or_loss`     | `float`            | 該部位的損益。([FBS][1])                                   |
| `premium`            | `float`            | 權利金（若適用）。([FBS][1])                                 |
| `order_no`           | `string`           | 該部位之訂單號碼（在文件範例中有此欄位）([FBS][1])                      |

---

## 範例

### 請求範例

```python
res = sdk.futopt_accounting.query_single_position(accounts)
```

### 回傳範例

```text
Result {
  is_success: True,
  message: None,
  data: [
    Position {
      date: "2024/04/08",
      branch_no: "15901",
      account: "1234567",
      order_no: "l0001-0000",
      position_kind: 1,
      symbol: "FITX",
      expiry_date: 202404,
      strike_price: None,
      call_put: None,
      buy_sell: Buy,
      price: 20362,
      orig_lots: 2,
      tradable_lot: 2,
      order_type: New,
      currency: "TWD",
      market_price: "20521.0000",
      initial_margin: 358000.0,
      maintenance_margin: 274000.0,
      clearing_margin: 264000.0,
      profit_or_loss: 63600.0,
      premium: 0.0
    },
    Position {
      date: "2024/03/29",
      branch_no: "15901",
      account: "1234567",
      order_no: "l0007-0000",
      position_kind: 2,
      symbol: "TX1",
      expiry_date: 202404,
      strike_price: 20600,
      call_put: Call,
      buy_sell: Buy,
      price: 10,
      orig_lots: 2,
      tradable_lot: 2,
      order_type: New,
      currency: "TWD",
      market_price: "4.6000",
      initial_margin: 52660.0,
      maintenance_margin: 36460.0,
      clearing_margin: 34460.0,
      profit_or_loss: -540.0,
      premium: -1000.0
    }
  ]
}
```

---


[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/accountManagement/SinglePosition "單式部位查詢 | 富邦新一代 API｜程式交易的新武器"


（資料來源：富邦 API 官方文件） ([FBS][1])

---

## 平倉查詢（ClosePositionRecord）

### 方法 / 路徑

SDK 方法通常為 `sdk.futopt_accounting.close_position_record(account, start_date, end_date)`
對應帳務模組下的平倉記錄查詢。 ([FBS][1])

---

## 輸入參數（Request）

| 參數           | 類型        |  必填 | 說明                                                           |
| ------------ | --------- | :-: | ------------------------------------------------------------ |
| `account`    | `Account` |  ✅  | 交易帳號物件，用以查該帳戶的平倉記錄。 ([FBS][1])                               |
| `start_date` | `string`  |  ✅  | 查詢起始日，格式為 `"YYYYMMDD"` 或 `"YYYY/MM/DD"`（視 SDK 支援） ([FBS][1]) |
| `end_date`   | `string`  |  ✅  | 查詢結束日，與起始格式相同。 ([FBS][1])                                    |

---

## 回傳結構（Result）

```text
Result {
  is_success: bool,           # 是否查詢成功
  data: List[CloseRecord],    # 平倉記錄清單
  message: string | None      # 查詢失敗時的錯誤訊息
}
```

* `is_success`：布林值，代表此次查詢是否成功。 ([FBS][1])
* `data`：若成功回傳多筆 `CloseRecord` 物件。 ([FBS][1])
* `message`：若 `is_success = False`，則包含錯誤訊息。 ([FBS][1])

---

## `CloseRecord`（平倉記錄）欄位定義

以下為官方文件列出的欄位與說明： ([FBS][1])

| 欄位                | 類型                 | 說明                                                     |
| ----------------- | ------------------ | ------------------------------------------------------ |
| `date`            | `string`           | 資料日期（平倉成交日） ([FBS][1])                                 |
| `branch_no`       | `string`           | 分公司代號 ([FBS][1])                                       |
| `position_kind`   | `int`              | 部位種類：`1`＝期貨、`2`＝選擇權 ([FBS][1])                         |
| `account`         | `string`           | 帳號 ([FBS][1])                                          |
| `order_no`        | `string`           | 委託書號 ([FBS][1])                                        |
| `market`          | `string`           | 市場別（如 `TAIMEX`） ([FBS][1])                             |
| `symbol`          | `string`           | 商品代號 ([FBS][1])                                        |
| `expiry_date`     | `string`           | 履約 / 到期年月（對期貨可能為 contract expiry） ([FBS][1])           |
| `strike_price`    | `float` 或 `None`   | 履約價（若為選擇權；期貨為 None） ([FBS][1])                         |
| `call_put`        | `CallPut` 或 `None` | 權利別：`Call` / `Put`（期貨為 None） ([FBS][1])                |
| `buy_sell`        | `BSAction`         | 買賣別：`Buy` / `Sell`（平倉方向） ([FBS][1])                    |
| `order_type`      | `FutOptOrderType`  | 委託類型：`New` / `Close` / `Auto` / `FdayTrade` ([FBS][1]) |
| `price`           | `float`            | 成交價格 ([FBS][1])                                        |
| `orig_lots`       | `int`              | 原始成交口數（平倉的口數） ([FBS][1])                               |
| `transaction_fee` | `float`            | 交易手續費 ([FBS][1])                                       |
| `tax`             | `float`            | 交易稅金 ([FBS][1])                                        |

---

## 請求 / 回傳範例

**請求範例**：

```python
res = sdk.futopt_accounting.close_position_record(accounts, "20240310", "20240410")
```

([FBS][1])

**回傳範例**：

```text
Result {
  is_success: True,
  message: None,
  data : [
    CloseRecord {
      date: "2024/04/10",
      branch_no: "15000",
      account: "9974825",
      position_kind: 1,
      order_no: "15001-0000",
      market: "TAIMEX",
      symbol: "FITX",
      expiry_date: "202404",
      strike_price: None,
      call_put: None,
      buy_sell: Buy,
      order_type: Close,
      price: 20847.0,
      orig_lots: 1,
      transaction_fee: 40.0,
      tax: 83.0
    },
    CloseRecord {
      date: "2024/04/10",
      branch_no: "15000",
      account: "9974825",
      position_kind: 1,
      order_no: "C0005-0000",
      market: "TAIMEX",
      symbol: "FITX",
      expiry_date: "202405",
      strike_price: None,
      call_put: None,
      buy_sell: Buy,
      order_type: Close,
      price: 20890.0,
      orig_lots: 1,
      transaction_fee: 40.0,
      tax: 84.0
    }
  ]
}
```

([FBS][1])

---



[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/accountManagement/ClosePositionRecord "平倉查詢 | 富邦新一代 API｜程式交易的新武器"


---

## 權益數查詢（QueryEquity）

### 方法名稱 / 路徑

* 在 SDK 或帳務模組中可能稱為 `sdk.futopt_accounting.query_margin_equity(account)`
* 所屬分類：帳務 / 權益數查詢 ([FBS][1])

---

## 請求參數（Request）

| 參數        | 型別        | 是否必填 | 說明                               |
| --------- | --------- | :--: | -------------------------------- |
| `account` | `Account` |   ✅  | 交易帳號物件，用以查詢該帳戶的權益數資訊。 ([FBS][1]) |

---

## 回傳結構（Result）

```text
Result {
  is_success: bool,           # 是否查詢成功
  data: List[Equity],         # 權益數資訊清單
  message: string | None      # 若 is_success = False，回傳錯誤訊息
}
```

* `is_success`：布林值，代表查詢是否成功 ([FBS][1])
* `data`：若成功回傳一或多筆 `Equity` 物件陣列 ([FBS][1])
* `message`：查詢失敗時之錯誤訊息 ([FBS][1])

---

## `Equity`（權益數資訊）欄位定義

以下為 `Equity` 物件在官方頁面列出的欄位與說明：([FBS][1])

| 欄位                   | 型別       | 說明                                                  |
| -------------------- | -------- | --------------------------------------------------- |
| `date`               | `string` | 查詢日期（格式如 `"2024/04/08"`）([FBS][1])                  |
| `branch_no`          | `string` | 分公司代號 ([FBS][1])                                    |
| `account`            | `string` | 帳號 ([FBS][1])                                       |
| `currency`           | `string` | 幣別：如 `NTD`, `TWD`, `USD`, `CNY`, `JPY` 等 ([FBS][1]) |
| `yesterday_balance`  | `float`  | 昨日餘額 ([FBS][1])                                     |
| `today_balance`      | `float`  | 今日餘額 ([FBS][1])                                     |
| `initial_margin`     | `float`  | 原始保證金 ([FBS][1])                                    |
| `maintenance_margin` | `float`  | 維持保證金 ([FBS][1])                                    |
| `clearing_margin`    | `float`  | 結算保證金 ([FBS][1])                                    |
| `today_equity`       | `float`  | 本日權益（含損益變動） ([FBS][1])                              |
| `today_deposit`      | `float`  | 本日入金額 ([FBS][1])                                    |
| `today_withdrawal`   | `float`  | 本日出金額 ([FBS][1])                                    |
| `today_trading_fee`  | `float`  | 本日交易手續費 ([FBS][1])                                  |
| `today_trading_tax`  | `float`  | 本日交易稅金 ([FBS][1])                                   |
| `receivable_premium` | `float`  | 應收權利金（選擇權相關） ([FBS][1])                             |
| `payable_premium`    | `float`  | 應付權利金 ([FBS][1])                                    |
| `excess_margin`      | `float`  | 超額保證金 （可用於追加下單）([FBS][1])                           |
| `available_margin`   | `float`  | 可動用保證金 ◆ （可用於下新單）([FBS][1])                         |
| `disgorgement`       | `float`  | 追繳金額（若有）([FBS][1])                                  |
| `opt_pnl`            | `float`  | 選擇權未沖銷浮動損益 ([FBS][1])                               |
| `opt_value`          | `float`  | 選擇權總市值 ([FBS][1])                                   |
| `opt_long_value`     | `float`  | 選擇權買方市值 ([FBS][1])                                  |
| `opt_short_value`    | `float`  | 選擇權賣方市值 ([FBS][1])                                  |
| `fut_realized_pnl`   | `float`  | 期貨平倉已實現損益 ([FBS][1])                                |
| `fut_unrealized_pnl` | `float`  | 期貨未平倉浮動損益 ([FBS][1])                                |
| `buy_lot`            | `int`    | 買進口數（今日買入筆數合計）([FBS][1])                            |
| `sell_lot`           | `int`    | 賣出口數（今日賣出筆數合計）([FBS][1])                            |

---

## 請求 / 回傳範例

* **請求範例**：

  ```python
  res = sdk.futopt_accounting.query_margin_equity(accounts)
  ```

  （即呼叫帳務模組對帳號查詢權益數）([FBS][1])

* **回傳範例**：

  ````text
  Result {
    is_success: True,
    message: None,
    data: [
      Equity {
        date: "2024/04/08",
        branch_no: "15901",
        account: "1234567",
        currency: "NTD",
        yesterday_balance: 22435152.4,
        today_balance: 22434910.4,
        initial_margin: 1114946.0,
        maintenance_margin: 939214.0,
        clearing_margin: 915760.0,
        today_equity: 22694910.4,
        today_deposit: 0.0,
        today_withdrawal: 2102.0,
        today_trading_fee: 16.0,
        today_trading_tax: 0.0,
        receivable_premium: 0.0,
        payable_premium: 9250.0,
        excess_margin: 28744525.0,
        available_margin: 21453562.4,
        disgorgement: 0.0,
        opt_pnl: -248600.0,
        opt_value: -193100.0,
        opt_long_value: 311900.0,
        opt_short_value: 505000.0,
        fut_realized_pnl: 0.0,
        fut_unrealized_pnl: 60700.0,
        buy_lot: 22,
        sell_lot: 7
      },
      …
    ]
  }
  ``` :contentReference[oaicite:35]{index=35}  
  ````

---

[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/accountManagement/QueryEquity "權益數查詢 | 富邦新一代 API｜程式交易的新武器"

---

## 一、結構概覽（EnumMatrix）

在 API 文檔中的「參數對照表」頁面（EnumMatrix）列出了以下幾大分類：

* 各種 **類別型參數**（如 `OrderObject`, `FutOptOrderResult` 等），以及它們的欄位與對應型別／說明 ([FBS][1])
* 各項 **常量 / 列舉（Enums / Constants）** 的對應值及說明。([FBS][1])
* 月份代號（用於商品代碼裡期貨／選擇權月份字母化的對照）([FBS][1])

這樣的頁面其實對你設計 client 或文件都非常有幫助：你可以把這些列舉直接當作型別定義 (enum) 放在你的程式碼裡。

---

## 二、主要列舉／常量（一覽）

以下是 EnumMatrix 中整理出的關鍵列舉與對應值：

| 列舉 / 常量                | 成員 / 值                                                                                                                                     | 說明                                           |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------- |
| **BSAction**           | `Buy` / `Sell`                                                                                                                             | 買或賣。([FBS][1])                               |
| **CallPut**            | `Call` / `Put`                                                                                                                             | 買權 / 賣權。([FBS][1])                           |
| **FutOptMarketType**   | `Future`、`FutureNight`、`Option`、`OptionNight`                                                                                              | 期貨日盤、期貨夜盤、選擇權日盤、選擇權夜盤。([FBS][1])             |
| **FutOptPriceType**    | `Limit`、`Market`、`RangeMarket`、`Reference`                                                                                                 | 價格型態。([FBS][1])                              |
| **TimeInForce**        | `ROD`、`FOK`、`IOC`                                                                                                                          | 委託時間條件：當日有效 / 全部成交否則取消 / 立即成交否則取消。([FBS][1]) |
| **FutOptOrderType**    | `New`、`Close`、`Auto`、`FdayTrade`                                                                                                           | 委託單類型（新倉、平倉、自動、當沖）。([FBS][1])                |
| **function_type（功能別）** | `0`（新單）、`10`（新單執行）、`15`（改價）、`20`（改量）、`30`（刪單）、`90`（失敗）                                                                                     | 操作類型對應值。([FBS][1])                           |
| **status（委託狀態）**       | `0` 預約單、`4` 中台收到、`9` 後台逾時、`10` 委託成功、`30` 刪單成功、`50` 完全成交、加上失敗與改價失敗等狀態                                                                       | 委託單目前的狀態代碼對應。([FBS][1])                      |
| **Month（月份代號）**        | 期貨／選擇權月代碼：<br>期貨：1–12 月 → A B C D E F G H I J K L<br>選擇權 Call：1–12 月 → A B C D E F G H I J K L<br>選擇權 Put：1–12 月 → M N O P Q R S T U V W X | 用來將月份轉為代碼字元（尤其在商品代號中常見）([FBS][1])            |

---

## 三、在你的程式碼 / client 中如何應用這些列舉

以下是建議你在 `vnpy_fubon` 或其他 SDK 客戶端裡可採的做法：

* 為每個列舉建立對應的 `Enum` 類別（Python 的 `enum.Enum` 或類型註解），例如：

  ```python
  from enum import Enum

  class BSAction(Enum):
      Buy = "Buy"
      Sell = "Sell"

  class FutOptMarketType(Enum):
      Future = "Future"
      FutureNight = "FutureNight"
      Option = "Option"
      OptionNight = "OptionNight"
  ```

* 當你在方法簽名中接收這類參數時，用 enum 型別，而非單純 `str` 或 `int`，可減少錯誤。

* 在序列化為請求 payload 時，把 enum 轉為該列舉對應的字串（或後端規定格式）。

* 在回傳物件中，把後端返回的整數或字串狀態轉成 enum，方便上層邏輯使用。

* 對於月份代號（Month）轉換，也可以寫一個 helpers，將月份整數 ↔ 字母代碼做轉換。

---

[1]: https://www.fbs.com.tw/TradeAPI/docs/trading-future/library/python/EnumMatrix "參數對照表 | 富邦新一代 API｜程式交易的新武器"

---

# Products List — 期貨/選擇權日內行情（HTTP API）

**路徑**：`intraday/products`
**用途**：依條件取得可交易契約清單（期貨／選擇權；含盤別、狀態、乘數、幣別、是否可報價等）。([FBS][1])

---

## 查詢參數（Query Parameters）

| 參數             | 型別       | 是否必填 | 可選值                                       | 說明                        |
| -------------- | -------- | :--: | ----------------------------------------- | ------------------------- |
| `type`         | `string` |   ✅  | `FUTURE`、`OPTION`                         | 查詢商品大類（期貨／選擇權）。([FBS][1]) |
| `exchange`     | `string` |   ⛔  | `TAIFEX`                                  | 交易所（臺灣期貨交易所）。([FBS][1])   |
| `session`      | `string` |   ⛔  | `REGULAR`、`AFTERHOURS`                    | 交易時段：一般盤／盤後。([FBS][1])    |
| `contractType` | `string` |   ⛔  | `I` 指數、`R` 利率、`B` 債券、`C` 商品、`S` 股票、`E` 匯率 | 契約類別過濾。([FBS][1])         |
| `status`       | `string` |   ⛔  | `N` 正常、`P` 暫停、`U` 即將上市                    | 契約狀態過濾。([FBS][1])         |

> 註：星號（*）為必填欄位（官方頁面標註）。([FBS][1])

---

## 回應結構（Response）

### 頂層

| 欄位             | 型別         | 說明                     |
| -------------- | ---------- | ---------------------- |
| `type`         | `string`   | 回應的期貨/選擇權類型。([FBS][1]) |
| `exchange`     | `string`   | 交易所。([FBS][1])         |
| `session`      | `string`   | 交易時段。([FBS][1])        |
| `contractType` | `string`   | 契約類別。([FBS][1])        |
| `status`       | `string`   | 契約狀態。([FBS][1])        |
| `data`         | `object[]` | 契約列表（見下表）。([FBS][1])   |

### `data[]` 契約物件

| 欄位                 | 型別        | 說明                                        |
| ------------------ | --------- | ----------------------------------------- |
| `symbol`           | `string`  | 契約代號。([FBS][1])                           |
| `type`             | `string`  | 期權類型（含盤別，例：`FUTURE_AH` 代表期貨盤後）。([FBS][1]) |
| `name`             | `string`  | 契約中文名稱。([FBS][1])                         |
| `underlyingSymbol` | `string`  | 標的現貨代號。([FBS][1])                         |
| `contractType`     | `string`  | 契約類別（同上）。([FBS][1])                       |
| `contractSize`     | `string`  | 契約乘數。([FBS][1])                           |
| `statusCode`       | `string`  | 狀態碼（`N` 正常等）。([FBS][1])                   |
| `tradingCurrency`  | `string`  | 交易幣別。([FBS][1])                           |
| `quoteAcceptable`  | `boolean` | 是否可報價。([FBS][1])                          |
| `startDate`        | `string`  | 上市日期。([FBS][1])                           |
| `canBlockTrade`    | `boolean` | 是否可鉅額交易。([FBS][1])                        |
| `expiryType`       | `string`  | 到期別：`S` 標準、`W` 週。([FBS][1])               |
| `underlyingType`   | `string`  | 標的類別：`E` ETF、`S` 個股。([FBS][1])            |
| `marketCloseGroup` | `string`  | 收盤時間群組（整數編號）。([FBS][1])                   |
| `endSession`       | `string`  | 交易時段碼：`0` 一般、`1` 盤後。([FBS][1])            |

---

## 範例（官方示例擷取）

**Python（SDK）**

```python
from fubon_neo.sdk import FubonSDK

sdk = FubonSDK()
accounts = sdk.login("Your ID", "Your password", "Your cert path", "Your cert password")
sdk.init_realtime()  # 建立行情連線

restfutopt = sdk.marketdata.rest_client.futopt
restfutopt.intraday.products(type='FUTURE', exchange='TAIFEX', session='REGULAR', contractType='E')
```

（Node.js / C# 範例同頁可得）([FBS][1])

**Response Body（節錄）**

```json
{
  "type": "FUTURE",
  "exchange": "TAIFEX",
  "session": "AFTERHOURS",
  "contractType": "E",
  "data": [
    {
      "symbol": "RHF",
      "type": "FUTURE_AH",
      "canBlockTrade": true,
      "contractSize": 100000,
      "contractType": "E",
      "endSession": "0",
      "expiryType": "S",
      "marketCloseGroup": 10,
      "name": "美元兌人民幣期貨",
      "quoteAcceptable": true,
      "startDate": "",
      "statusCode": "N",
      "tradingCurrency": "CNY",
      "underlyingSymbol": "",
      "underlyingType": ""
    },
    { "symbol": "RTF", "type": "FUTURE_AH", "...": "..." },
    { "symbol": "XAF", "type": "FUTURE_AH", "...": "..." }
  ]
}
```

（完整內容見官方頁面）([FBS][1])

---

## 快速備忘（前後端整合小貼士）✨

* `type` 與 `session` 的組合會影響 `data[].type`（如 `FUTURE_AH`）。建議在前端以 **badge** 呈現盤別與狀態。([FBS][1])
* `marketCloseGroup` 可用來對應不同收盤時間段，利於撮合排程或風控關閘（例如：夜盤提前關閉報價）。([FBS][1])
* 若你要串接到下游（如 `intraday/tickers`, `quote`, `candles`），可直接以 `symbol` 為鍵串接。([FBS][1])

---

[1]: https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/products "Products List | 富邦新一代 API｜程式交易的新武器"

---

# 期貨/選擇權行情 Rate Limit（HTTP/WebSocket）

## 一、Web API（行情）

* **Intraday（日內行情）**：`300 / min`
* **Snapshot（行情快照）**：`300 / min`
* **Historical（歷史行情）**：`60 / min`
* 超限回應：HTTP **`429`**，訊息：

  ```json
  {"statusCode":429,"message":"Rate limit exceeded"}
  ```

  官方建議：**等待 1 分鐘**再重試。([FBS][1])

## 二、WebSocket（行情）

* **最大訂閱數**：`200` 檔
* **最大連線數**：`5` 條
* 超限訊息（訂閱/連線）：

  ```json
  {
    "event": "error",
    "data": { "code": 1001, "message": "Maximum number of connections reached" }
  }
  ```

  以上限制同樣見官方中文頁。([FBS][2])

> 導覽位置：FutOpt MarketData → **Rate Limit**（期貨/選擇權行情文件）。多處頁面亦引用同一組限制說明（如 Quick Start 與各 API 子頁）。([FBS][3])

---

## 三、（補充）交易與帳務 API 的頻率限制（非行情）

* **同一應用程式同時連線**：`10`
* **每秒上限**：下單 `50`、**批次下單** `10`、**帳務查詢** `5`
* 超限時常見回傳：

  ```text
  Result {
    is_success: False,
    message: "Login Error, 業務系統流量控管",
    data: None
  }
  ```

  詳見交易端 Rate Limit 說明。([FBS][4])

---

## 四、實務建議（速查）

* **節流策略**：對 REST 以「**每 200ms** 一次」為上限節流；大量抓取歷史資料時以 **60/min** 計算批次。
* **退避策略**：遇到 `429` 先 **sleep 65–70 秒** 再恢復；或實作 **指數退避**。
* **WebSocket 管理**：集中一條連線、動態增減訂閱，確保「訂閱數 ≤ 200、連線 ≤ 5」。
* **分流**：把「掃庫（Historical）」工作與「即時（Intraday/WS）」分開執行，以免互相佔用額度。
  （以上依官方限制整理與常見做法。）

---

## 五、Python：簡易重試/節流範例（可貼用）

```python
import time
import requests

RATE_LIMIT_WINDOW = 60        # 秒
INTRADAY_MAX_PER_MIN = 300
HIST_MAX_PER_MIN = 60

def backoff_retry(func, max_retries=3, base_wait=5):
    for i in range(max_retries + 1):
        r = func()
        if r.status_code != 429:
            return r
        time.sleep(base_wait * (2 ** i))   # 指數退避
    return r  # 仍 429 時將最後一次回傳交由上層判斷

# 例：日內行情節流（簡單配額桶）
last_reset = time.time()
used = 0

def intraday_get(url, params):
    global last_reset, used
    now = time.time()
    if now - last_reset >= RATE_LIMIT_WINDOW:
        used, last_reset = 0, now
    if used >= INTRADAY_MAX_PER_MIN:
        time.sleep(RATE_LIMIT_WINDOW - (now - last_reset) + 0.1)
        used, last_reset = 0, time.time()
    def do_req():
        return requests.get(url, params=params, timeout=10)
    resp = backoff_retry(do_req)
    used += 1
    return resp
```

---

需要我把這份 **Rate Limit** 規格再做成一頁式 Markdown（中/英對照）或加入你專案的 **使用者指引/風控守則** 範本嗎？我可以直接輸出可貼用的段落與程式片段 📦✨。

[1]: https://www.fbs.com.tw/TradeAPI/en/docs/market-data/rate-limit/?utm_source=chatgpt.com "Rate Limit | 富邦新一代API｜程式交易的新武器"
[2]: https://www.fbs.com.tw/TradeAPI/docs/market-data/rate-limit/?utm_source=chatgpt.com "速率限制| 富邦新一代API｜程式交易的新武器"
[3]: https://www.fbs.com.tw/TradeAPI/en/docs/market-data-future/intro?utm_source=chatgpt.com "Fubon RealTime MarketData API"
[4]: https://www.fbs.com.tw/TradeAPI/en/docs/trading/trade-rate-limit?utm_source=chatgpt.com "Rate Limit | 富邦新一代API｜程式交易的新武器"

---

## 開始使用（Getting Started）

### 功能簡介

富邦行情 Web API 提供給開發者便利接口，用於查詢以下類型的數據：

* **日內行情**（Intraday）
* **行情快照**（Snapshot）
* **歷史行情**（Historical）
  ([FBS][1])

### 速率限制（Rate Limit）

若你的 API 請求超過限制，伺服器會回應 HTTP 狀態碼 **429**。([FBS][1])

### 支援 SDK / 範例代碼

富邦為不同平台提供 SDK 支援，包含 **Python / Node.js / C#**。([FBS][1])

以下為 Python 範例流程：

```python
from fubon_neo.sdk import FubonSDK

sdk = FubonSDK()
accounts = sdk.login("Your ID", "Your password", "Your cert path", "Your cert password")
sdk.init_realtime()  # 建立行情連線

restfutopt = sdk.marketdata.rest_client.futopt
# 之後可以呼叫 restfutopt 的各個 HTTP API 方法，例如 intraday.products(...)
```

Node.js / C# 範例在官方文件也有對應。([FBS][1])

### 可用的 HTTP API 路徑（行情類）

以下是行情服務所提供的幾個主要端點／功能：([FBS][1])

| 路徑                           | 功能             |
| ---------------------------- | -------------- |
| `/intraday/products`         | 查詢契約 / 商品清單    |
| `/intraday/tickers`          | 根據條件查合約或標的報價列表 |
| `/intraday/quote/{symbol}`   | 依合約代碼取得即時報價    |
| `/intraday/candles/{symbol}` | 取得 K 線資料       |
| `/intraday/trades/{symbol}`  | 查成交明細          |
| `/intraday/volumes/{symbol}` | 查分價量表          |

---

[1]: https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/getting-started "開始使用 | 富邦新一代 API｜程式交易的新武器"



---

# Intraday Tickers API — 合約報價清單

**Endpoint 路徑**：`/intraday/tickers`
**用途**：根據過濾條件，取得多筆合約的即時報價資料（含買賣五檔、成交、指標等）

---

## 查詢參數（Query Parameters）

| 參數             | 類型       | 是否必填 | 說明                             |
| -------------- | -------- | :--: | ------------------------------ |
| `type`         | `string` |   ✅  | 類型：`FUTURE` 或 `OPTION`         |
| `exchange`     | `string` |   ✅  | 交易所（如 `TAIFEX`）                |
| `session`      | `string` |   ⛔  | 交易時段：`REGULAR`, `AFTERHOURS` 等 |
| `contractType` | `string` |   ⛔  | 契約類型（例如某些類型過濾）                 |
| `status`       | `string` |   ⛔  | 合約狀態：正常、暫停、未上市等                |
| `symbol`       | `string` |   ⛔  | 合約代號（可篩特定合約）                   |
| `page`         | `int`    |   ⛔  | 分頁號碼（若合約數量多）                   |
| `limit`        | `int`    |   ⛔  | 每頁筆數上限                         |

> 註：以上部分參數是依一般 API 設計習慣與其他富邦 API 類似端點推測，實際必要參數以官網文件為主。

---

## 回傳結構（Response）

### 頂層

```json
{
  "type": "FUTURE",
  "exchange": "TAIFEX",
  "session": "REGULAR",
  "contractType": "E",
  "status": "N",
  "data": [
    {
      /* 合約報價物件 */
    },
    ...
  ]
}
```

| 欄位             | 型別         | 說明            |
| -------------- | ---------- | ------------- |
| `type`         | `string`   | 類型：期貨 / 選擇權   |
| `exchange`     | `string`   | 交易所代碼         |
| `session`      | `string`   | 時段            |
| `contractType` | `string`   | 契約類別          |
| `status`       | `string`   | 合約狀態          |
| `data`         | `object[]` | 合約報價清單，每筆物件如下 |

### `data[]` 合約報價物件欄位

下列欄位為常見報價 API 所涵蓋欄位，依實際 API 文件可能略有出入：

| 欄位                                                    | 型別        | 說明                                                       |        |
| ----------------------------------------------------- | --------- | -------------------------------------------------------- | ------ |
| `symbol`                                              | `string`  | 合約代號                                                     |        |
| `type`                                                | `string`  | 報價類型（例如含時段標記）                                            |        |
| `name`                                                | `string`  | 合約中文名稱或說明                                                |        |
| `underlyingSymbol`                                    | `string`  | 標的代號                                                     |        |
| `contractSize`                                        | `number`  | 合約乘數                                                     |        |
| `statusCode`                                          | `string`  | 合約狀態代碼                                                   |        |
| `tradingCurrency`                                     | `string`  | 交易幣別                                                     |        |
| `quoteAcceptable`                                     | `boolean` | 是否可報價                                                    |        |
| `expiryType`                                          | `string`  | 到期別（如標準、週別等）                                             |        |
| `underlyingType`                                      | `string`  | 標的類型                                                     |        |
| `marketCloseGroup`                                    | `string   | number`                                                  | 收盤時間群組 |
| `endSession`                                          | `string   | number`                                                  | 時段結束標記 |
| **報價 / 交易資料**                                         |           | 下列是報價與交易細節                                               |        |
| `lastPrice`                                           | `number`  | 最新成交價                                                    |        |
| `lastSize`                                            | `number`  | 最新成交量                                                    |        |
| `bids`                                                | `array`   | 買方五檔陣列，每項：`{ price, size }`                              |        |
| `asks`                                                | `array`   | 賣方五檔陣列                                                   |        |
| `total`                                               | `object`  | 總交易累計統計，如 `tradeValue, tradeVolume, transaction, time` 等 |        |
| `openPrice` / `highPrice` / `lowPrice` / `closePrice` | `number`  | 今開、高、低、收盤等                                               |        |
| `change` / `changePercent`                            | `number`  | 價格變動與百分比                                                 |        |
| `avgPrice`                                            | `number`  | 今日成交平均價                                                  |        |
| `lastTrade`                                           | `object`  | 最新成交資訊（含時間、價格、量）                                         |        |
| `lastTrial`                                           | `object`  | 最新試撮資訊（試盤資料）                                             |        |
| 其他狀態標誌                                                | `boolean` | 如 `isLimitUpPrice`, `isLimitDownPrice`, `isHalted` 等     |        |

---

## 使用範例（Python SDK）

```python
from fubon_neo.sdk import FubonSDK

sdk = FubonSDK()
accounts = sdk.login("YourID", "Password", "CertPath", "CertPwd")
sdk.init_realtime()

restfutopt = sdk.marketdata.rest_client.futopt
res = restfutopt.intraday.tickers(
    type="FUTURE",
    exchange="TAIFEX",
    session="REGULAR",
    contractType="E",
    status="N"
)

if res:
    for rec in res.data:
        print(rec.symbol, rec.lastPrice, rec.bids, rec.asks)
```

---

以下是 **Intraday Ticker（單一合約即時行情）** 的 API 規格整理，根據富邦官方文件。([FBS][1])

---

## 路徑 / 定義

* **路徑**：`intraday/ticker/`
* **用途**：查詢某一個合約的即時基本行情資料（與 `tickers` 類似，但專注單一商品）。([FBS][1])

---

## 查詢參數（Parameters）

| 參數        | 類型       |  必填 | 說明                                                      |
| --------- | -------- | :-: | ------------------------------------------------------- |
| `symbol`  | `string` |  ✅  | 商品代號（必填）([FBS][1])                                      |
| `session` | `string` |  ⛔  | 交易時段，可選 `"REGULAR"`（一般交易）或 `"AFTERHOURS"`（盤後）([FBS][1]) |

---

## 回應結構（Response）

以下是該 API 會回傳的欄位與說明：([FBS][1])

| 欄位               | 類型       | 說明          |
| ---------------- | -------- | ----------- |
| `date`           | `string` | 日期          |
| `type`           | `string` | 類型（如期貨或選擇權） |
| `exchange`       | `string` | 交易所         |
| `symbol`         | `string` | 商品代號        |
| `name`           | `string` | 商品名稱        |
| `referencePrice` | `string` | 參考價         |
| `settlementDate` | `string` | 結算日期        |
| `startDate`      | `string` | 上市日期        |
| `endDate`        | `string` | 下市日期        |

---

## 範例（官方示例擷取）

* **請求（Python SDK）**

  ```python
  restfut = sdk.marketdata.rest_client.futopt
  restfut.intraday.ticker(symbol='TXFI4')
  ```

  （同時也支援帶 `session`）([FBS][1])

* **回傳範例**

  ```json
  {
    "date": "2024-09-18",
    "type": "FUTURE",
    "exchange": "TAIFEX",
    "symbol": "TXFI4",
    "name": "臺股期貨094",
    "referencePrice": 21703,
    "settlementDate": "2024-09-18",
    "startDate": "2023-09-21",
    "endDate": "2024-09-18"
  }
  ```

  （上述為官方範例）([FBS][1])

---


[1]: https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/ticker "Intraday Ticker | 富邦新一代 API｜程式交易的新武器"
以下是我幫你整理的 **Intraday Quote（單一商品即時報價）** API 規格，方便拿來當 client 介面設計或文件貼用 🧾
（資料來源：富邦官方文件）([FBS][1])

---

# Intraday Quote API — 單一合約即時報價

## 路徑 / 方法

* **路徑**：`intraday/quote/{symbol}`
* **用途**：依商品代碼查詢該合約的即時基本行情資料（含成交、漲跌、統計資料等）([FBS][1])

---

## 查詢參數（Parameters）

| 參數        | 類型       |  必填 | 說明                                   |
| --------- | -------- | :-: | ------------------------------------ |
| `symbol`  | `string` |  ✅  | 商品代碼（必填）([FBS][1])                   |
| `session` | `string` |  ⛔  | 交易時段。可選 `afterhours`（盤後交易）([FBS][1]) |

---

## 回應結構（Response）

以下是 API 回傳 JSON 的欄位與說明：([FBS][1])

| 欄位              | 類型       | 說明                                                                                                             |
| --------------- | -------- | -------------------------------------------------------------------------------------------------------------- |
| `date`          | `string` | 日期                                                                                                             |
| `type`          | `string` | 類型（期貨 / 選擇權）                                                                                                   |
| `exchange`      | `string` | 交易所                                                                                                            |
| `symbol`        | `string` | 商品代號                                                                                                           |
| `name`          | `string` | 合約名稱                                                                                                           |
| `previousClose` | `number` | 昨日收盤價                                                                                                          |
| `openPrice`     | `number` | 今日開盤價                                                                                                          |
| `openTime`      | `number` | 開盤成交時間（Timestamp）                                                                                              |
| `highPrice`     | `number` | 今日最高價                                                                                                          |
| `highTime`      | `number` | 最高價成交時間                                                                                                        |
| `lowPrice`      | `number` | 今日最低價                                                                                                          |
| `lowTime`       | `number` | 最低價成交時間                                                                                                        |
| `closePrice`    | `number` | 收盤價（最後成交價）                                                                                                     |
| `closeTime`     | `number` | 收盤價成交時間                                                                                                        |
| `avgPrice`      | `number` | 當日成交平均價                                                                                                        |
| `change`        | `number` | 最後成交價漲跌                                                                                                        |
| `changePercent` | `number` | 漲跌幅（百分比）                                                                                                       |
| `amplitude`     | `number` | 當日振幅                                                                                                           |
| `lastPrice`     | `number` | 最新成交價（含試撮）                                                                                                     |
| `lastSize`      | `number` | 最新成交數量（含試撮）                                                                                                    |
| `total`         | `object` | 累計統計數據<br>• `tradeVolume`：總成交量<br>• `totalBidMatch`：委買成交筆數<br>• `totalAskMatch`：委賣成交筆數                         |
| `lastTrade`     | `object` | 最後一筆成交詳細資訊：<br>• `bid`：買價<br>• `ask`：賣價<br>• `price`：成交價格<br>• `size`：成交量<br>• `time`：成交時間<br>• `serial`：成交流水號 |
| `serial`        | `number` | 最新流水號                                                                                                          |
| `lastUpdated`   | `number` | 最後異動時間（Timestamp）                                                                                              |

---

## 範例請求與回應

### Python SDK 範例

```python
from fubon_neo.sdk import FubonSDK

sdk = FubonSDK()
accounts = sdk.login("Your ID", "Your Password", "Your Cert Path", "Your Cert Password")
sdk.init_realtime()

restfut = sdk.marketdata.rest_client.futopt
res = restfut.intraday.quote(symbol='TXFA4')
print(res)
```

### 回傳範例（節錄）

```json
{
  "date": "2023-12-12",
  "type": "FUTURE",
  "exchange": "TAIFEX",
  "symbol": "TXFA4",
  "name": "臺股期貨014",
  "previousClose": 17416,
  "openPrice": 17514,
  "openTime": 1702341900070000,
  "highPrice": 17540,
  "highTime": 1702342491330000,
  "lowPrice": 17427,
  "lowTime": 1702355400574000,
  "closePrice": 17460,
  "closeTime": 1702359886936000,
  "avgPrice": 17478.89,
  "change": 44,
  "changePercent": 0.25,
  "amplitude": 0.65,
  "lastPrice": 17460,
  "lastSize": 1,
  "total": {
    "tradeVolume": 1626,
    "totalBidMatch": 0,
    "totalAskMatch": 0
  },
  "lastTrade": {
    "bid": 17459,
    "ask": 17460,
    "price": 17460,
    "size": 1,
    "time": 1702359886936000,
    "serial": "00165753"
  },
  "serial": 165753,
  "lastUpdated": 1702359886936000
}
```

---

若你願意，我可以幫你把這份 **Intraday Quote API** 規格直接轉成 OpenAPI（YAML/JSON），或幫你設計出你 `MarketDataClient.quote()` 的型別註解與回傳資料類別，你要哪一個版本？

[1]: https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/quote "Intraday Quote | 富邦新一代 API｜程式交易的新武器"
以下是 **Intraday Candles（K 線資料）** HTTP API 的完整規格整理，方便你整合到 client 或文件中使用：

---

## Intraday Candles API 規格

### 路徑 / 說明

* API 路徑：`intraday/candles/{symbol}`
* 功能：取得某個商品的 **即時 K 線（分時 K 線／多時框間隔）** 資料。 ([FBS][1])

---

### 查詢參數（Parameters）

| 參數          | 類型       | 是否必填 | 可選值 / 備註                                          | 說明                            |
| ----------- | -------- | :--: | ------------------------------------------------- | ----------------------------- |
| `symbol`    | `string` |   ✅  | —                                                 | 該商品的合約代碼。 ([FBS][1])          |
| `session`   | `string` |   ⛔  | `"afterhours"`                                    | 若為夜盤時段，指定 session。 ([FBS][1]) |
| `timeframe` | `string` |   ⛔  | `"1"` / `"5"` / `"10"` / `"15"` / `"30"` / `"60"` | K 線週期（單位：分鐘） ([FBS][1])       |

> 標示 `*` 的欄位為必揭示欄位。 ([FBS][1])

---

### 回應結構（Response）

回傳 JSON 結構如下：

* 頂層欄位：

  | 欄位          | 類型         | 說明           |
  | ----------- | ---------- | ------------ |
  | `date`      | `string`   | 查詢日期         |
  | `type`      | `string`   | 類型（例如期貨／選擇權） |
  | `exchange`  | `string`   | 交易所          |
  | `market`    | `string`   | 市場別          |
  | `symbol`    | `string`   | 合約代碼         |
  | `timeframe` | `string`   | K 線週期        |
  | `data`      | `object[]` | K 線資料陣列      |

* `data[]`（每筆 K 線）欄位：

  | 欄位        | 類型       | 說明                                      |
  | --------- | -------- | --------------------------------------- |
  | `time`    | `number` | K 線所代表時間（Timestamp 或 ISO 格式） ([FBS][1]) |
  | `open`    | `number` | 該時間段開盤價                                 |
  | `high`    | `number` | 該時間段最高價                                 |
  | `low`     | `number` | 該時間段最低價                                 |
  | `close`   | `number` | 該時間段收盤價                                 |
  | `volume`  | `number` | 該時間段成交量                                 |
  | `average` | `number` | 該時間段的成交平均價 ([FBS][1])                   |

---

### 範例（Example）

**Python SDK 呼叫範例**

```python
from fubon_neo.sdk import FubonSDK

sdk = FubonSDK()
accounts = sdk.login("Your ID", "Your password", "Your cert path", "Your cert password")
sdk.init_realtime()

restfutopt = sdk.marketdata.rest_client.futopt
res = restfutopt.intraday.candles(symbol='TXFA4')
print(res)
```

**Response 範例（節錄）**

```json
{
  "date": "2023-12-15",
  "type": "FUTURE",
  "exchange": "TAIFEX",
  "symbol": "TXFA4",
  "timeframe": "1",
  "data": [
    {
      "date": "2023-12-15T08:45:00.000+08:00",
      "open": 17744,
      "high": 17756,
      "low": 17744,
      "close": 17756,
      "volume": 98,
      "average": 17749.97
    },
    {
      "date": "2023-12-15T08:46:00.000+08:00",
      "open": 17755,
      "high": 17756,
      "low": 17742,
      "close": 17747,
      "volume": 48,
      "average": 17749.1
    },
    {
      "date": "2023-12-15T08:47:00.000+08:00",
      "open": 17746,
      "high": 17746,
      "low": 17731,
      "close": 17731,
      "volume": 26,
      "average": 17747.8
    },
    ...
  ]
}
```

> 範例顯示 `time` 欄位採 ISO 字串帶時區格式。 ([FBS][1])

---

如果你願意，我可以幫你把這份 **Intraday Candles** API 規格轉為 OpenAPI YAML/JSON，或幫你寫出 `MarketDataClient.get_candles(...)` 的 interface + 型別註解 & 測試樣板，要哪一個版本給你？

[1]: https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/candles "Intraday Candles | 富邦新一代 API｜程式交易的新武器"
以下是我為你整理好的 **Intraday Trades（成交明細）** HTTP API 規格，方便你整合到 client 或文件中使用：

（來源：富邦官方 API 文件） ([FBS][1])

---

## Intraday Trades API — 成交明細

### 路徑 / 用途

* **路徑**：`intraday/trades/{symbol}`
* **用途**：查詢指定合約的成交紀錄清單（依時間排序）([FBS][1])

---

## 查詢參數（Parameters）

| 參數        | 類型       | 是否必填 | 說明                                  |
| --------- | -------- | :--: | ----------------------------------- |
| `symbol`  | `string` |   ✅  | 商品合約代號。([FBS][1])                   |
| `session` | `string` |   ⛔  | 交易時段，可選 `afterhours`（夜盤）。([FBS][1]) |
| `offset`  | `number` |   ⛔  | 用來分頁的偏移量（從哪筆開始）。([FBS][1])          |
| `limit`   | `number` |   ⛔  | 限制回傳筆數（上限多少筆）。([FBS][1])            |

> 標示 `*` 的參數為必揭示欄位（`symbol`）。([FBS][1])

---

## 回應結構（Response）

### 頂層欄位

| 欄位         | 類型         | 說明                        |
| ---------- | ---------- | ------------------------- |
| `date`     | `string`   | 成交日期。([FBS][1])           |
| `type`     | `string`   | 合約類型（期貨 / 選擇權）。([FBS][1]) |
| `exchange` | `string`   | 交易所代號。([FBS][1])          |
| `symbol`   | `string`   | 商品合約代號。([FBS][1])         |
| `data`     | `object[]` | 成交紀錄清單。([FBS][1])         |

### `data[]`（每筆成交記錄）欄位

對應每筆成交明細，以下欄位可能會出現：([FBS][1])

| 欄位       | 類型       | 說明                      |
| -------- | -------- | ----------------------- |
| `price`  | `number` | 成交價格。([FBS][1])         |
| `size`   | `number` | 成交量（單量）。([FBS][1])      |
| `time`   | `number` | 成交時間（時間戳記格式）。([FBS][1]) |
| `serial` | `number` | 成交流水號。([FBS][1])        |

---

## 範例請求與回應

### Python SDK 示例

```python
from fubon_neo.sdk import FubonSDK

sdk = FubonSDK()
accounts = sdk.login("Your ID", "Your Password", "Cert Path", "Cert Password")
sdk.init_realtime()

restfutopt = sdk.marketdata.rest_client.futopt
res = restfutopt.intraday.trades(symbol='TXFA4')
print(res)
```

### 回傳範例（節錄）

```json
{
  "date": "2023-12-20",
  "type": "FUTURE",
  "exchange": "TAIFEX",
  "symbol": "TXFA4",
  "data": [
    {
      "price": 17660,
      "size": 3,
      "time": 1703051099834000,
      "serial": 218307
    },
    {
      "price": 17661,
      "size": 2,
      "time": 1703051099779000,
      "serial": 218304
    },
    {
      "price": 17661,
      "size": 1,
      "time": 1703051099778000,
      "serial": 218303
    },
    {
      "price": 17661,
      "size": 1,
      "time": 1703051099778000,
      "serial": 218301
    }
    /* …更多筆 */
  ]
}
```

---

如果你願意，我可以幫你把這份 **Intraday Trades API** 規格轉成 OpenAPI（YAML/JSON）格式，或幫你生成對應的 `MarketDataClient.get_trades(...)` 方法（含型別註解與測試範例），要哪一種給你？

[1]: https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/trades "Intraday Trades | 富邦新一代 API｜程式交易的新武器"
下面是 **Intraday Volumes（分價量表）** API 的整理規格，方便你整合進 client / 文件中：

---

## Intraday Volumes API 規格（分價量表）

### 路徑 / 描述

* 路徑：`intraday/volumes/{symbol}`
* 功能：依合約代號取得該合約的**分價量表**—即各成交價格對應的累積成交量。 ([FBS][1])
* 對象通常為期貨或選擇權契約。 ([FBS][1])

---

### 查詢參數（Parameters）

| 參數        | 類型       |  必填 | 說明                                    |
| --------- | -------- | :-: | ------------------------------------- |
| `symbol`  | `string` |  ✅  | 合約代號（必填） ([FBS][1])                   |
| `session` | `string` |  ⛔  | 若屬夜盤交易可傳 `afterhours` 指定盤別 ([FBS][1]) |

---

### 回傳結構（Response）

回傳 JSON 物件格式如下：

* 頂層欄位：

  | 欄位         | 類型         | 說明                                      |
  | ---------- | ---------- | --------------------------------------- |
  | `date`     | `string`   | 查詢日期 ([FBS][1])                         |
  | `type`     | `string`   | 合約類型（例如 “FUTURE” / “OPTION”） ([FBS][1]) |
  | `exchange` | `string`   | 交易所（如 TAIFEX） ([FBS][1])                |
  | `market`   | `string`   | 市場別 ([FBS][1])                          |
  | `symbol`   | `string`   | 合約代號 ([FBS][1])                         |
  | `data`     | `object[]` | 分價量清單（各價位的成交量） ([FBS][1])               |

* `data[]` 中每個物件欄位：

  | 欄位       | 類型       | 說明                   |
  | -------- | -------- | -------------------- |
  | `price`  | `number` | 價格（成交價） ([FBS][1])   |
  | `volume` | `number` | 該價格的累積成交量 ([FBS][1]) |

---

### 範例

**Python SDK 呼叫範例**

```python
from fubon_neo.sdk import FubonSDK

sdk = FubonSDK()
accounts = sdk.login("Your ID", "Your password", "Your cert path", "Your cert password")
sdk.init_realtime()

restfutopt = sdk.marketdata.rest_client.futopt
res = restfutopt.intraday.volumes(symbol='TXFA4')
print(res)
```

**回傳範例（節錄）**

```json
{
  "date": "2023-12-20",
  "type": "FUTURE",
  "exchange": "TAIFEX",
  "symbol": "TXFA4",
  "data": [
    {
      "price": 17676,
      "volume": 68
    },
    {
      "price": 17675,
      "volume": 392
    },
    {
      "price": 17674,
      "volume": 265
    },
    {
      "price": 17673,
      "volume": 396
    },
    {
      "price": 17672,
      "volume": 430
    },
    {
      "price": 17671,
      "volume": 518
    },
    {
      "price": 17670,
      "volume": 681
    },
    {
      "price": 17669,
      "volume": 338
    },
    {
      "price": 17668,
      "volume": 395
    },
    {
      "price": 17667,
      "volume": 568
    },
    {
      "price": 17666,
      "volume": 670
    },
    {
      "price": 17665,
      "volume": 641
    },
    {
      "price": 17664,
      "volume": 542
    },
    {
      "price": 17663,
      "volume": 898
    },
    {
      "price": 17662,
      "volume": 870
    },
    {
      "price": 17661,
      "volume": 656
    },
    {
      "price": 17660,
      "volume": 876
    },
    {
      "price": 17659,
      "volume": 402
    },
    {
      "price": 17658,
      "volume": 352
    },
    {
      "price": 17657,
      "volume": 190
    },
    {
      "price": 17656,
      "volume": 470
    }
    // … （以下略）  
  ]
}
```

---

若你願意，我可以幫你把這份 **Volumes API** 規格轉成 OpenAPI（YAML/JSON），或是幫你在你的 `MarketDataClient` 裡生成對應的 `get_volumes(...)` 方法 interface + 型別註解、測試樣板，你要哪一種版本？

[1]: https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/intraday/volumes "Intraday Volumes | 富邦新一代 API｜程式交易的新武器"
以下是我幫你「擷取＋結構化」的 **期貨/選擇權 WebSocket 行情：Getting Started（開始使用）** 規格與可貼用範例 ✅
（來源：富邦新一代 API 官方文件）([FBS][1])

---

# WebSocket 行情 — 開始使用（FutOpt）

## 核心概念

* 提供 **即時行情** 推播，支援 **Python / Node.js / C#** SDK。可切換兩種模式：**Speed（低延遲）** 與 **Normal（資訊較完整）**。([FBS][1])

---

## 基本連線流程（以 Python 為例）

```python
from fubon_neo.sdk import FubonSDK, Mode

def handle_message(message):
    print(message)

sdk = FubonSDK()
accounts = sdk.login("Your ID", "Your password", "Your cert path", "Your cert password")  # 需登入才有行情權限
sdk.init_realtime()              # 預設 Speed；可改為 sdk.init_realtime(Mode.Normal)

futopt = sdk.marketdata.websocket_client.futopt
futopt.on('message', handle_message)
futopt.connect()
```

上述流程、Mode 切換與事件綁定，均見官方示例。([FBS][1])

---

## 身分驗證（連上後的第一則訊息）

* 成功：

  ```json
  {"event":"authenticated","data":{"message":"Authenticated successfully"}}
  ```
* 失敗：

  ```json
  {"event":"error","data":{"message":"Invalid authentication credentials"}}
  ```

([FBS][1])

---

## Heartbeat 與 Ping/Pong

* **Heartbeat**：伺服器每 **30 秒** 發送

  ```json
  {"event":"heartbeat","data":{"time":"<Timestamp>"}}
  ```
* **Ping/Pong**：SDK 每 **5 秒** 自動送 `ping`；亦可手動並攜帶自訂 `state`，伺服器回 `pong`（會帶回 `state` 若你有送）。
  Python：

  ```python
  futopt.ping({'state': '<ANY>'})
  ```

  伺服器回覆：

  ```json
  {"event":"pong","data":{"time":"<TIMESTAMP>","state":"<ANY>"}}
  ```

([FBS][1])

---

## 可訂閱頻道（Channels）

目前提供：

* `trades`：最新**成交**（逐筆）。
* `books`：最新**最佳五檔**委買/委賣。
  訂閱單檔（Python）：

```python
futopt.subscribe({
  "channel": "trades",          # 或 "books"
  "symbol":  "<SYMBOL_ID>",
  # "afterHours": True          # 夜盤行情可加此參數
})
```

訂閱多檔（Python）：

```python
futopt.subscribe({
  "channel": "books",
  "symbols": ["<SYMBOL_1>", "<SYMBOL_2>"],
  # "afterHours": True
})
```

成功會收到：

```json
{"event":"subscribed","data":{"id":"<CHANNEL_ID>","channel":"<CHANNEL_NAME>","symbol":"<SYMBOL_ID>"}}
```

（多檔會回傳 `data` 陣列）([FBS][1])

---

## 取消訂閱（Unsubscribe）

* 取消單一頻道：

  ```python
  futopt.unsubscribe({'id': '<CHANNEL_ID>'})
  ```

  成功回：

  ```json
  {"event":"unsubscribed","data":{"id":"<CHANNEL_ID>","channel":"<CHANNEL_NAME>","symbol":"<SYMBOL_ID>"}}
  ```
* 取消多個頻道：

  ```python
  futopt.unsubscribe({'ids': ['<CHANNEL_ID>','<CHANNEL_ID>']})
  ```

  成功回 `data` 陣列。([FBS][1])

---

## 事件處理（錯誤 / 連線狀態）

Python 綁定建議：

```python
def handle_connect():
    print('market data connected')

def handle_disconnect(code, message):
    print(f'market data disconnect: {code}, {message}')

def handle_error(error):
    print(f'market data error: {error}')

futopt.on("connect", handle_connect)
futopt.on("disconnect", handle_disconnect)
futopt.on("error", handle_error)
```

([FBS][1])

---

## 斷線重連（含自動重新訂閱）

官方示例做法（Python）：

```python
def handle_disconnect(code, message):
    print(f'market data disconnect: {code}, {message}')
    futopt.connect()
    print("Reconnected Success")
    print("Resubscribe")
    futopt.subscribe({
        'channel': '<CHANNEL_NAME>',
        'symbol':  '<SYMBOL_ID>'
    })

futopt.on("disconnect", handle_disconnect)
```

此處示範：**重連後自動補發原有的 `subscribe`**，恢復資料流。([FBS][1])

---

## 小抄（實務建議）🛠️

* **模式選擇**：高頻策略取 **Speed**；需要更完整欄位時用 **Normal**。([FBS][1])
* **夜盤**：訂閱時加 `afterHours: true`，或於多檔訂閱一併指定。([FBS][1])
* **健康檢查**：以 Heartbeat 與 Pong 的時間差監控延遲/斷線；逾時觸發重連。([FBS][1])
* **狀態事件**：務必監聽 `connect / disconnect / error / message`，並記錄 `CHANNEL_ID` 以便 Unsubscribe/Resubscribe。([FBS][1])

---

需要我把這頁再**轉成 README 片段（中英雙語）**、或直接產出一個 **`FutOptWsClient`**（含自動重連、訂閱管理、夜盤切換、回放測試）給你嗎？我可以一次產出可貼用的 Python 類別與測試樣板。

[1]: https://www.fbs.com.tw/TradeAPI/docs/market-data-future/websocket-api/getting-started "開始使用 | 富邦新一代 API｜程式交易的新武器"
以下是我整理的 **WebSocket 行情頻道：`books`（最佳五檔）** 規格，方便你直接拿去整合或做為 client 參考：

（資料來源：富邦 API 官方文件） ([FBS][1])

---

# WebSocket：`books`（期貨 / 選擇權｜最佳五檔）

**用途**：訂閱指定合約的即時委買 / 委賣最佳五檔（即五檔深度）。 ([FBS][1])

---

## 訂閱參數（Subscribe Payload）

| 參數           | 類型       |  必填 | 說明                                                   |
| ------------ | -------- | :-: | ---------------------------------------------------- |
| `channel`    | `string` |  ✅  | 頻道名稱：**`books`** ([FBS][1])                          |
| `symbol`     | `string` |  ✅  | 合約代碼，例如 `TXFA4` ([FBS][1])                           |
| `afterHours` | `bool`   |  ⛔  | 是否訂閱夜盤行情（`true` 夜盤，`false` 日盤，預設 `false`） ([FBS][1]) |

---

## 回傳格式（Message Payload）

當有新的五檔變動時，伺服器會推送如下格式的資料：

| 欄位         | 類型         | 說明                                     |
| ---------- | ---------- | -------------------------------------- |
| `symbol`   | `string`   | 商品代碼 ([FBS][1])                        |
| `type`     | `string`   | Ticker 類型（如期貨 / 選擇權） ([FBS][1])        |
| `exchange` | `string`   | 交易所（例如 `TAIFEX`） ([FBS][1])            |
| `market`   | `string`   | 市場別 ([FBS][1])                         |
| `time`     | `number`   | 時間戳記（Timestamp） ([FBS][1])             |
| `bids`     | `object[]` | 委買五檔陣列，每項含 `price` / `size` ([FBS][1]) |
| `asks`     | `object[]` | 委賣五檔陣列，每項含 `price` / `size` ([FBS][1]) |

其中，`bids[i]` 表示第 i 檔買方價格與數量，`asks[i]` 表示賣方第 i 檔價格與數量。 ([FBS][1])

### 範例推播訊息

```json
{
  "event": "data",
  "data": {
    "symbol": "TXFA4",
    "type": "FUTURE",
    "exchange": "TAIFEX",
    "bids": [
      { "price": 17539, "size": 2 },
      { "price": 17538, "size": 4 },
      { "price": 17537, "size": 3 },
      { "price": 17536, "size": 10 },
      { "price": 17535, "size": 10 }
    ],
    "asks": [
      { "price": 17541, "size": 2 },
      { "price": 17542, "size": 15 },
      { "price": 17543, "size": 3 },
      { "price": 17544, "size": 5 },
      { "price": 17545, "size": 4 }
    ],
    "time": 1702956500113000
  },
  "id": "<CHANNEL_ID>",
  "channel": "books"
}
```

→ 此為官方文件示例中的格式。 ([FBS][1])

---

## Python（可貼用）示例

以下是簡易示範如何在 Python 中訂閱 `books` 頻道並接收五檔資料：

```python
from fubon_neo.sdk import FubonSDK

def on_msg(msg):
    print("Received books:", msg)

sdk = FubonSDK()
accounts = sdk.login("YourID", "YourPwd", "CertPath", "CertPwd")
sdk.init_realtime()

ws = sdk.marketdata.websocket_client.futopt
ws.on("message", on_msg)
ws.connect()

ws.subscribe({
    "channel": "books",
    "symbol": "TXFA4"
    # "afterHours": True  # 若要夜盤
})
```

---

如果你願意，我可以把 `books`、`trades`、`aggregates` 等頻道的訊息格式與訂閱方式，整合為一份完整的 WebSocket 行情規範文檔（中英對照），或做成 `FutOptWsClient` 類別含自動重連、訂閱管理、夜盤切換給你用，要嗎？

[1]: https://www.fbs.com.tw/TradeAPI/docs/market-data-future/websocket-api/market-data-channels/books "Books | 富邦新一代 API｜程式交易的新武器"
以下是 **WebSocket 行情頻道：`aggregates`（聚合統計／總攬數據）** 的規格與範例，整理給你直接放到文件或作為 client 參考 ✅
（來源：富邦 API 官方文件）([fbs.com.tw][1])

---

## WebSocket：`aggregates`（聚合數據頻道）

> 注意：此頻道僅適用於 **Normal 模式**。([fbs.com.tw][1])

**用途**：推送合約的聚合行情資料，例如開高低收、成交量、漲跌、最新交易統計等。相較於 `trades` 或 `books`，這是一種多指標彙總訊息。([fbs.com.tw][1])

---

## 訂閱參數（Subscribe Payload）

| 參數           | 型別       |  必填 | 說明                                                          |
| ------------ | -------- | :-: | ----------------------------------------------------------- |
| `channel`    | `string` |  ✅  | `"aggregates"`                                              |
| `symbol`     | `string` |  ✅  | 合約代碼                                                        |
| `afterHours` | `bool`   |  ⛔  | 是否訂閱夜盤行情（`true`：夜盤，`false`：日盤，預設為 `false`）([fbs.com.tw][1]) |

---

## 回傳資料（Message Payload）

當有新的聚合數據時（例如某檔合約當日行情、成交累計有變動）會推送如下格式資料：([fbs.com.tw][1])

### 頂層欄位

| 欄位              | 型別       | 說明               |
| --------------- | -------- | ---------------- |
| `date`          | `string` | 該筆資料所屬日期         |
| `type`          | `string` | 合約類型（如期貨 / 選擇權）  |
| `exchange`      | `string` | 交易所（例如 “TAIFEX”） |
| `symbol`        | `string` | 合約代碼             |
| `name`          | `string` | 合約名稱             |
| `previousClose` | `number` | 昨日收盤價            |
| `openPrice`     | `number` | 今開盤價             |
| `openTime`      | `number` | 開盤成交時間戳記         |
| `highPrice`     | `number` | 今日最高價            |
| `highTime`      | `number` | 最高價成交時間          |
| `lowPrice`      | `number` | 今日最低價            |
| `lowTime`       | `number` | 最低價成交時間          |
| `closePrice`    | `number` | 今日收盤價（即最後成交價）    |
| `closeTime`     | `number` | 收盤價成交時間          |
| `avgPrice`      | `number` | 今日平均成交價          |
| `change`        | `number` | 最後成交價漲跌數值        |
| `changePercent` | `number` | 漲跌幅（百分比）         |
| `amplitude`     | `number` | 當日振幅             |
| `lastPrice`     | `number` | 最新成交價（含試撮）       |
| `lastSize`      | `number` | 最新成交數量（含試撮）      |
| `total`         | `object` | 成交累計統計（見下表）      |
| `lastTrade`     | `object` | 最後一筆成交詳情（見下表）    |
| `serial`        | `number` | 當前流水號            |
| `lastUpdated`   | `number` | 最後異動時間戳記         |

### `total` 統計物件欄位

| 欄位              | 型別       | 說明       |
| --------------- | -------- | -------- |
| `tradeVolume`   | `number` | 累計成交量    |
| `totalBidMatch` | `number` | 累計內盤成交數量 |
| `totalAskMatch` | `number` | 累計外盤成交數量 |

### `lastTrade` 物件欄位

| 欄位       | 型別       | 說明       |
| -------- | -------- | -------- |
| `price`  | `number` | 最後成交價格   |
| `size`   | `number` | 最後成交數量   |
| `time`   | `number` | 最後成交時間戳記 |
| `serial` | `number` | 最後成交的流水號 |

---

## 範例：訂閱與接收資料

**Python 訂閱範例**

```python
from fubon_neo.sdk import FubonSDK, Mode

def handle_message(msg):
    print("Aggregates message:", msg)

sdk = FubonSDK()
accounts = sdk.login("YourID", "YourPW", "CertPath", "CertPW")
# 必須用 Normal 模式才能使用 aggregates
sdk.init_realtime(Mode.Normal)

ws = sdk.marketdata.websocket_client.futopt
ws.on("message", handle_message)
ws.connect()

ws.subscribe({
    "channel": "aggregates",
    "symbol": "TXFA4"
    # optional: "afterHours": True
})
```

成功後若市場有新的聚合變更，就會收到含上述欄位的 JSON 消息。([fbs.com.tw][1])

---

如果你願意，我可以幫你把 `aggregates`、`trades`、`books`、`candles` 等 WebSocket 頻道統整成一份 **Python WebSocket 行情客戶端 (`FutOptWsClient`)** 的完整類別（內含自動重連、訂閱管理、夜盤切換），並包含型別註解和單元測試樣板，你要嗎？

[1]: https://www.fbs.com.tw/TradeAPI/docs/market-data-future/websocket-api/market-data-channels/aggregates "Aggregates | 富邦新一代 API"
下面是我為你「擷取＋結構化」好的 **WebSocket 行情頻道：`candles`（分鐘 K 線）** 規格，方便直接複製到文件或程式註解 🧾✨
（資料來源：富邦新一代 API 官方文件）([fbs.com.tw][1])

---

# WebSocket：`candles`（期貨／選擇權｜分鐘 K 線）

* **適用模式**：僅支援 **Normal 模式**（`Mode.Normal`）。
* **用途**：訂閱指定合約的**最新分鐘 K 線**資料。([fbs.com.tw][1])

---

## 訂閱參數（Subscribe Payload）

| 參數           | 型別       |  必填 | 說明                                                                                |
| ------------ | -------- | :-: | --------------------------------------------------------------------------------- |
| `channel`    | `string` |  ✅  | 固定填 **`candles`**（可用頻道：`trades`、`candles`、`books`、`aggregates`）。([fbs.com.tw][1]) |
| `symbol`     | `string` |  ✅  | 合約代碼（例如：`TXFA4`）。([fbs.com.tw][1])                                                |
| `afterHours` | `bool`   |  ⛔  | 是否訂閱**夜盤**：`true`＝夜盤、`false`＝日盤，預設 `false`。([fbs.com.tw][1])                      |

---

## 回傳資料（Message Payload）

### 頂層欄位

| 欄位                                | 型別       | 說明                                   |
| --------------------------------- | -------- | ------------------------------------ |
| `date`                            | `string` | 資料時間（ISO-8601，含時區）。([fbs.com.tw][1]) |
| `type`                            | `string` | 類型：如 `FUTURE`。([fbs.com.tw][1])      |
| `exchange`                        | `string` | 交易所（例：`TAIFEX`）。([fbs.com.tw][1])    |
| `market`                          | `string` | 市場別。([fbs.com.tw][1])                |
| `symbol`                          | `string` | 合約代碼。([fbs.com.tw][1])               |
| `timeframe`                       | `number` | K 線週期（分鐘）。([fbs.com.tw][1])          |
| `open` / `high` / `low` / `close` | `number` | 開／高／低／收。([fbs.com.tw][1])            |
| `volume`                          | `number` | 成交量。([fbs.com.tw][1])                |
| `average`                         | `number` | 平均價。([fbs.com.tw][1])                |

---

## 訂閱／接收範例

**Python（Normal 模式）**

```python
from fubon_neo.sdk import FubonSDK, Mode

def handle_message(message):
    print("market data message:", message)

sdk = FubonSDK()
accounts = sdk.login("Your ID", "Your password", "Your cert path", "Your cert password")

sdk.init_realtime(Mode.Normal)     # candles 僅支援 Normal
futopt = sdk.marketdata.websocket_client.futopt
futopt.on('message', handle_message)
futopt.connect()

# 日盤分鐘K
futopt.subscribe({'channel': 'candles', 'symbol': 'TXFA4'})

# 夜盤分鐘K（若需要）
# futopt.subscribe({'channel': 'candles', 'symbol': 'TXFA4', 'afterHours': True})
```

（Node.js / C# 官方頁面亦提供對應示例。）([fbs.com.tw][1])

**推播資料範例（官方節錄）**

```json
{
  "event": "data",
  "data": {
    "symbol": "TXFA4",
    "type": "FUTURE",
    "exchange": "TAIFEX",
    "date": "2023-12-28T12:01:00.000+08:00",
    "open": 17861,
    "high": 17862,
    "low": 17859,
    "close": 17862,
    "volume": 22,
    "average": 17820.19
  },
  "id": "<CHANNEL_ID>",
  "channel": "candles"
}
```

([fbs.com.tw][1])

---

## 實務建議 🛠️

* **模式切換**：若你既要 `trades` 的低延遲、又要 `candles` 的彙總，需在 **Speed 與 Normal** 模式間設計協同（或開兩條連線），因 `candles` 只在 **Normal**。([fbs.com.tw][1])
* **夜盤處理**：夜盤需帶 `afterHours: true`；若策略跨盤別，建議分開維護訂閱與聚合狀態。([fbs.com.tw][1])
* **時間序列**：以 `date` 作為 K 線時間戳記鍵；收到多筆同分鐘資料時，依最新推播覆寫該分鐘的 OHLCV。([fbs.com.tw][1])

---

需要我把四個 WS 頻道（`trades`／`books`／`aggregates`／`candles`）整理成一個 **`FutOptWsClient`**（含自動重連、重訂閱、日夜盤切換、型別註解與測試樣板）嗎？可以直接產出可貼用的程式碼 📦🚀。

[1]: https://www.fbs.com.tw/TradeAPI/en/docs/market-data-future/websocket-api/market-data-channels/candles/ "Candles | 富邦新一代 API｜程式交易的新武器"
