# 富邦 API 欄位標準化對照

以下對照表涵蓋富邦新一代期權 API 針對台指期（TXF）與台指選（TXO）主要頻道（trades／orderbook／quotes）。所有欄位皆轉換為 vn.py 事件模型及 PostgreSQL 落地欄位，並維持資料可回放、可去重與可補洞。

## 通用欄位規則

- **symbol**：統一使用交易所代碼（例：`TXF202510`、`TXO202510C15500`、`TXO202510W1P15500`），不含交易所後綴；寫入資料庫後再搭配 `Exchange.TAIFEX`。
- **event_ts_utc / event_ts_local**：原始時間若為毫秒整數，先轉換為 UTC `TIMESTAMP WITH TIME ZONE`，再以 `Asia/Taipei` 轉換。若只提供字串（例：`2025-10-16T08:30:15.123+08:00`），需解析並歸一為 UTC。
- **book_seq / event_seq**：若 WebSocket 提供 `seq` 或 `checksum`，先存入 `market_raw`，再映射至細表；缺值時以 `NULL` 表示並依 price/qty/time 建立去重鍵。
- **checksum**：如 API 提供 `md5`、`crc` 或 `checksum` 欄位即沿用；若無則於應用層以 `sha256(json.dumps(payload, sort_keys=True))` 產生。
- **event_origin**：在 vn.py 事件中以 `extras` 附帶原始 `channel`、`ingest_latency_ms` 等資訊；資料庫則記錄於 `channel`、`receive_latency_ms`。

## Trades（成交）

| Fubon 原始欄位 | 資料型別 | 單位 | 正規化欄位 | PostgreSQL 欄位 | 可空 | 去重鍵 | 備註 |
| -------------- | -------- | ---- | ---------- | ---------------- | ---- | ------ | ---- |
| `contractId` / `code` | string | - | `symbol` | `symbol` | 否 | `symbol + trade_id` | 需轉換成 TAIFEX 代號 |
| `exchangeTime` / `matchTime` | string / int | 毫秒 | `datetime` (vn.py `TradeData.datetime`) | `event_ts_utc` / `event_ts_local` | 否 | 與 `trade_id` 組合 | 若為本地時間需轉換 |
| `matchNo` / `seq` / `tradeId` | string / int | - | `tradeid` | `trade_id` | 否 | 主鍵 | 取最細緻欄位；不足時 fallback `symbol+ts+price+qty` |
| `side` / `bsFlag` | string | ENUM(buy/sell) | `direction` | `side` | 是 | - | 轉換為 `LONG`/`SHORT` 或保留原字串 |
| `price` / `matchPrice` | number | 新台幣 | `price` | `price` | 否 | - | 轉換 Decimal |
| `volume` / `matchQty` | number | 數量 | `volume` | `quantity` | 否 | - | 以口數為單位；小數以 0.01 |
| `accVolume` | number | 數量 | `turnover` | `turnover` | 是 | - | 若為累計成交量，用於檢核 quotes |
| `channel` | string | - | `extra["channel"]` | `channel` | 否 | - | 預設 `trades` |
| `checksum` | string | - | `extra["checksum"]` | `checksum` | 是 | `dedup_token` | 缺值時由應用層計算 |

## Level-2（Order Book）

| Fubon 原始欄位 | 資料型別 | 單位 | 正規化欄位 | PostgreSQL 欄位 | 可空 | 去重鍵 | 備註 |
| -------------- | -------- | ---- | ---------- | ---------------- | ---- | ------ | ---- |
| `contractId` / `code` | string | - | `tick.symbol` | `symbol` | 否 | `(symbol, book_seq, level)` | |
| `exchangeTime` / `updateTime` | string / int | 毫秒 | `tick.datetime` | `event_ts_utc` / `event_ts_local` | 否 | 與 `book_seq` 組合 | |
| `seq` / `orderSeq` / `bookSeq` | int | - | `tick.extra["book_seq"]` | `book_seq` | 是 | 主要去重鍵 | 重連後以該欄位校正缺口 |
| `bidPx1` ~ `bidPx10` | number | 新台幣 | `tick.bid_price_N` | `bid_px`（對應 level） | 否 | - | 僅展開至設定深度 |
| `bidSz1` ~ `bidSz10` | number | 口數 | `tick.bid_volume_N` | `bid_sz` | 否 | - | |
| `askPx1` ~ `askPx10` | number | 新台幣 | `tick.ask_price_N` | `ask_px` | 否 | - | |
| `askSz1` ~ `askSz10` | number | 口數 | `tick.ask_volume_N` | `ask_sz` | 否 | - | |
| `midPrice` / `referencePrice` | number | 新台幣 | `extra["mid_price"]` | `mid_px` | 是 | - | 方便量化檢核 |
| `isSnapshot` | bool | - | `extra["is_snapshot"]` | `is_snapshot` | 否（預設 False） | 區分快照/增量 | 首次訂閱應收到快照 |
| `checksum` / `crc` | string | - | `extra["checksum"]` | `checksum` | 是 | `(symbol, checksum)` | |
| `channel` | string | - | `extra["channel"]` | `channel` | 否 | - | 預設 `orderbook` |

## Quotes（即時彙總行情）

| Fubon 原始欄位 | 資料型別 | 單位 | 正規化欄位 | PostgreSQL 欄位 | 可空 | 去重鍵 | 備註 |
| -------------- | -------- | ---- | ---------- | ---------------- | ---- | ------ | ---- |
| `contractId` / `code` | string | - | `tick.symbol` | `symbol` | 否 | `(symbol, event_ts_utc)` | |
| `exchangeTime` / `quoteTime` | string / int | 毫秒 | `tick.datetime` | `event_ts_utc` / `event_ts_local` | 否 | 與 book_seq | |
| `seq` / `quoteSeq` | int | - | `extra["quote_seq"]` | `book_seq` | 是 | 唯一索引 | |
| `lastPrice` | number | 新台幣 | `tick.last_price` | `last_px` | 是 | - | |
| `openPrice` | number | 新台幣 | `tick.open_price` | `open_px` | 是 | - | |
| `highPrice` / `lowPrice` | number | 新台幣 | `tick.high_price` / `tick.low_price` | `high_px` / `low_px` | 是 | - | |
| `volume` / `accVolume` | number | 口數 | `tick.volume` | `volume` | 是 | - | 與 trades 彙總對帳 |
| `askPx1` / `bidPx1` | number | 新台幣 | `tick.ask_price_1` / `tick.bid_price_1` | `ask_px_1` / `bid_px_1` | 是 | - | |
| `askVol1` / `bidVol1` | number | 口數 | `tick.ask_volume_1` / `tick.bid_volume_1` | `ask_sz_1` / `bid_sz_1` | 是 | - | |
| `openInterest` | number | 口數 | `extra["open_interest"]` | `open_interest` | 是 | - | |
| `settlementPrice` / `theoreticalPrice` | number | 新台幣 | `extra["est_settlement"]` | `est_settlement` | 是 | - | |
| `impliedVol` | number | % | `extra["implied_vol"]` | `implied_vol` | 是 | - | 以小數表示（例 0.1523） |
| `checksum` | string | - | `extra["checksum"]` | `checksum` | 是 | `(symbol, checksum)` | |

## vn.py 事件映射

- **Trades**：轉為 `TradeData`，其中 `gateway_name='FubonIngest'`，`direction` 依 `side` / `bsFlag` 判斷為 `Direction.LONG` 或 `Direction.SHORT`，若為撮合結果無方向則保持 `Direction.NET` 並註記於 `extra["net_side"]=True`。
- **OrderBook**：轉為 `TickData` 並附上 `extra["book"]` (list[LevelRow]) 以利回放；同時送出自定事件 `EVENT_FUBON_MARKET_RAW` 以便寫入 `market_raw`。
- **Quotes**：亦使用 `TickData`，但於 `extra["quote"]=True` 標示為彙總行情，避免與 L2 增量混淆。

## 去重策略摘要

| 頻道 | 主去重鍵 | 次去重鍵 | 備註 |
| ---- | -------- | -------- | ---- |
| trades | `(symbol, trade_id)` | `(symbol, event_ts_utc, price, quantity)` | trade_id 缺失時使用時間 + 價量備援 |
| orderbook | `(symbol, book_seq, level)` | `(symbol, event_ts_utc, level, checksum)` | 快照 `is_snapshot=True` 時與增量拆開主鍵 |
| quotes | `(symbol, book_seq)` | `(symbol, event_ts_utc)` | 若無序號時以時間為主鍵 |
| market_raw | `dedup_token` | `(symbol, channel, event_seq)` | 由應用層產生穩定字串 |

## 補洞與一致性檢查

- **序號檢查**：當收到的 `event_seq` 或 `book_seq` 不連續時，立即記錄 `reconcile_log`，並將缺口範圍（seq/timestamp）同步寫入 raw 表等待回放。
- **L2 重建**：利用 `tools/replay_ws_raw.py` 以 `market_raw` 中同一 `symbol`、`channel='orderbook'` 進行時間區間回放，重建完整深度並比對 `market_l2`。
- **成交量對帳**：`tools/verify_gap.py` 會將 `market_trades` 於指定區間的累計數量與 `market_quotes.volume` 差異控制在 1% 以內；超出則寫入 `reconcile_log`。

## 單位與型別速查

| 欄位 | 單位 | PostgreSQL 型別 | 說明 |
| ---- | ---- | ---------------- | ---- |
| price | 新台幣 | `NUMERIC(18,4)` | 4 位小數足以涵蓋期權 tick size |
| quantity / volume | 口數 | `NUMERIC(18,2)` | 期權量常以整數表示，保留 2 位小數供特殊商品使用 |
| implied_vol | 比例 | `NUMERIC(10,6)` | 以 0.123456 代表 12.3456% |
| seq / trade_id | 無 | `BIGINT` / `TEXT` | 優先使用官方序號，缺值時由應用層組裝 |
| checksum | - | `TEXT` | 建議為十六進位或 Base64 |
| payload | - | `JSONB` | 原始封包 |

此對照表為後續 adapter / writer / 測試的基準，若富邦正式版 API 另有命名差異，請於 `docs/mapping.md` 同步更新並調整 `adapters/fubon_to_vnpy.py` 對應邏輯。
