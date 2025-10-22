# Gateway Redesign Plan

## 1. Align with vn.py BaseGateway
- [x] Adopt `BaseGateway` inheritance and implement required hooks (`connect`, `close`, `subscribe`, `query_account`, etc.).
- [x] Replace custom event wrappers with vn.py `Event` objects routed through the event engine interface.
- [x] Standardise logging via `write_log` for vn.py UI integration.

## 2. Responsibility Split
- [x] Move websocket connection, reconnect, and subscription orchestration from `MarketAPI` into `FubonGateway`.
- [x] Keep `MarketAPI` focused on data normalisation and helper conversions for REST / websocket payloads.
- [x] Expose public conversion helpers in `OrderAPI` (避免 gateway 呼叫私有方法)。

## 3. Session & Reconnect
- [x] Establish heartbeat / token refresh policy (`exchange_realtime_token`, websocket ping) with automatic scheduling。
- [x] Implement retry / backoff when websocket disconnects, with hooks for the event engine to trigger UI warnings。
- [x] Ensure `close()` / `stop()` gracefully unsubscribe and reset state for reconnection。

## 4. Account & Order Handling
- [x] Support multiple accounts or dynamic account switching（`switch_account` / `account_id` 設定）。
- [x] Validate / document mapping coverage for order、trade 狀態並新增 fallback 記錄。
- [x] Emit vn.py standard events for order / trade updates (`EVENT_ORDER`, `EVENT_TRADE`)。

## 5. Market Data Workflow
- [x] Gateway 管理 `init_realtime`、websocket connect/disconnect、訂閱與 after-hours flag。
- [x] 提供訂閱層級的重連策略：斷線後自動重送訂閱。
- [x] 將 `MarketAPI` 定位為 stateless mapper，並提供 websocket 訊息解析輔助。

## 6. Testing Strategy
- [x] 擴充自動化測試：conversion 單元測試、訂閱失敗的錯誤情境測試。
- [x] Live 測試以環境變數控制，缺憾時自動 skip。
- [ ] 增加更多錯誤情境測試（訂單拒絕回傳細節等）與 CI 整合。

## 7. Documentation & Examples
- [x] 更新 `README.md` / `PROJECT_OVERVIEW.md` 說明 gateway lifecycle 與事件流程。
- [x] 提供示例程式展示 gateway + vn.py EventEngine 整合（`examples/fubon_event_engine_demo.py`）。
- [x] 補充常見問題（websocket reconnect、登入失敗、callback 註冊失敗等）。

## 8. Roadmap & Delivery
- Phase work by dependency：介面對齊 → 資料層重構 → 重連策略 → 測試 → 文件。
- Track open questions（如 SDK 是否支援多路 websocket、官方 heartbeat 要求等）。
- Success metrics：vn.py compatibility confirmed、tests green、documentation current。
