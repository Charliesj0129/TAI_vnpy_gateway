# 落地與維運 Runbook

本文件說明如何啟動富邦行情落地流程、監控狀態、處理斷線與補洞，並在驗收期間快速核對資料品質。

## 1. 事前準備

- 於專案根目錄複製 `.env.template` → `.env`，填入 API 金鑰、帳號與 PostgreSQL 連線字串。
- 驗證資料庫已套用 `docs/schema.sql` 中的表結構（可透過 `psql -f storage/migrations/001_init.sql`）。
- 更新 `config/symbols.toml`（如需特別週選、履約價範圍）與 `config/pipeline.toml`（批次大小、重試策略）。
- 確保系統時鐘同步 NTP（預設監控 `pool.ntp.org`）。

## 2. 啟動訂閱與入庫

```powershell
python tools/fubon_subscribe.py --symbols TXF=front,TXF=next,TXO=weekly_all --channels trades,orderbook,quotes --l2-depth 5
```

- 系統會自動：
  - 透過 REST 取得 Token，啟動 WebSocket 並訂閱指定商品。
  - 將資料轉換為 vn.py `TickData` / `TradeData` 事件並送入 EventEngine。
  - 同步寫入 `market_raw` / `market_l2` / `market_trades` / `market_quotes`，具備去重與退避重試。
- LOG 預設輸出為 JSON，可調整 `.env` 中 `LOG_LEVEL`、`PROMETHEUS_PORT` 等參數。

### 2.1 自動重連

- 心跳預設 15 秒；若連續兩個心跳收不到資料，會啟動退避重連（1, 2, 5, 10, 15 秒）。
- 重連後 `_restore_subscriptions()` 會帶入上一筆 `seq`，確保資料接續。
- 若 REST Token 即將過期會自動刷新，並在該輪刷新後重新 handshake+訂閱。

## 3. 資料品質檢查

### 3.1 序號與成交量檢核

```powershell
python tools/verify_gap.py --symbol TXF% --from-ts 2025-10-16T08:30:00 --to-ts 2025-10-16T08:45:00
```

- 檢查 `market_trades.event_seq`、`market_l2.book_seq` 是否連續。
- 將成交量加總與 `market_quotes.volume` 差異控制在預設容忍度（1%）。
- 若 `reconcile_log` 仍有 `pending` 條目會直接在結果中提示。

### 3.2 回放與重建 L2

```powershell
python tools/replay_ws_raw.py --symbol TXF202510 --from-ts 2025-10-16T08:30:00 --to-ts 2025-10-16T08:35:00 --depth 5
```

- 從 `market_raw` 取出 orderbook 封包 → 使用 adapter 重建 L2 → 與 `market_l2` 比對。
- `--apply` 可在比對後將結果寫回資料庫，適合針對單一時段做校正。

### 3.3 補洞與對帳

- `tools/backfill_gap.py --reconcile-id <ID>`：
  - 讀取 `reconcile_log` 指定條目。
  - 依頻道切換至 `replay_ws_raw`（orderbook）或直接轉換 raw → trades/quotes，寫回資料後更新狀態為 `backfilled`。
- 亦可手動指定範圍：

  ```powershell
  python tools/backfill_gap.py --symbol TXF202510 --channel trades --from-ts 2025-10-16T08:00:00 --to-ts 2025-10-16T08:05:00
  ```

## 4. 驗收流程建議

1. **暖身**：啟動 `fubon_subscribe.py` 至少 5 分鐘，確認 `market_raw` 持續累積。
2. **資料品質**：使用 `verify_gap.py` 對關鍵時段三次抽樣（開盤、午盤、收盤）。
3. **回放驗證**：隨機挑選 5 個時間點，執行 `replay_ws_raw.py`，確保 L2 與回放結果一致。
4. **補洞演練**：手動標記 `reconcile_log` 一筆測試資料，執行 `backfill_gap.py` 並確認狀態由 `pending` → `backfilled`。
5. **測試**：

   ```powershell
   pytest tests/test_ingest.py tests/test_reconnect.py tests/test_dedup.py
   ```

   - `test_ingest.py`：驗證 adapter + writer 產生的 SQL/欄位。
   - `test_reconnect.py`：檢查訂閱在重連後是否帶入最新 `seq`。
   - `test_dedup.py`：確保 dedup token 穩定，避免重覆寫入。

## 5. 常見問題與排除

- **REST 登入失敗**：檢查 `.env` 是否正確填入 `FUBON_USER_ID/FUBON_USER_PASSWORD/FUBON_CA_PATH/FUBON_CA_PASSWORD`；若使用憑證登入需確保檔案存在且密碼無誤。
- **WS 無資料**：確認防火牆/VPN；可先運行 `python run.py` 內建示例確認連線品質。
- **資料庫寫入緩慢**：調整 `config/pipeline.toml` 中 `batch_size`、`max_workers`，或將 `copy_enabled` 改為 `true`（需 PostgreSQL 14+）。
- **延遲過高**：`RAW_BACKPRESSURE_MS` 會在延遲超過閾值時優先寫 `market_raw`，請檢視 `receive_latency_ms` 欄位判斷是否落地過慢。
- **序號缺口持續存在**：確認對應 `reconcile_log` 是否已標為 `pending`，再透過 `backfill_gap.py` 或調整尺幅重新回補。

## 6. 日常維運建議

- 將 `fubon_subscribe.py` 透過 systemd / NSSM 以服務形式常駐，並監控 LOG 中 `gateway_state`、`latency_ms` 指標。
- 每日收盤後使用 `verify_gap.py` 批次檢查主要商品；於 Grafana / Superset 建立簡易 dashboard 顯示 `market_l2` 筆數與序號分布。
- 定期（每週）回放 `market_raw` 隨機區間，確保 schema 變動時 adapter 尚能正確解析。
- 若 API 版本升級或欄位變動，記得同步更新 `docs/mapping.md` 及 `adapters/fubon_to_vnpy.py` 對應欄位清單。
