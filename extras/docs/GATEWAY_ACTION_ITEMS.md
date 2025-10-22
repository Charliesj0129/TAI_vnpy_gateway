# Gateway Integration Action Items

## 1. Align with vn.py BaseGateway contract
- Rework method signatures to match BaseGateway: `send_order(OrderRequest) -> str`, `cancel_order(CancelRequest)`, `query_account()`, `query_position()`.
- Dispatch account/position/order/trade results via `self.put_event` (`EVENT_ACCOUNT`, `EVENT_POSITION`, `EVENT_ORDER`, `EVENT_TRADE`) instead of returning values directly.
- Ensure tick/order/trade events are emitted with proper vn.py data classes and EventEngine interface.
  - Contract metadata now loads via REST `intraday.products`/`intraday.tickers`, publishing `EVENT_CONTRACT` as part of the initial connection handshake.

## 2. Strengthen websocket lifecycle handling
- Hook into Fubon SDK callbacks such as `set_on_event` / `set_on_error` to surface authentication errors or disconnects as log events.
- On heartbeat/token refresh failure, trigger defensive reconnect (re-run `init_realtime()` / websocket connect) rather than silent logging.
- Improve subscribe error handling: detect non-retriable user errors (e.g. invalid channel) and surface clear exceptions while avoiding infinite reconnect loops.
  - Gateway now registers websocket lifecycle callbacks, pushes `EVENT_LOG` entries for errors, and restarts realtime/token refresh when heartbeats fail.

## 3. Multi-account strategy
- Investigate whether switching accounts requires SDK context change (e.g., re-login, switching internal modules) rather than only setting `account_id`.
- Provide a public API to list available accounts alongside metadata (e.g., account type) to help users choose correctly.
  - Added `FubonGateway.get_account_metadata()` exposing account ids, names, and flags.
- Validate order placement after `switch_account()` to confirm the new account is effective; surface warning if the SDK still targets the previous account.
  - `switch_account` now applies SDK context setters and emits warnings when validation detects mismatched accounts.

## 4. Market data semantics
- Distinguish between orderbook (`books`) and trade (`trades`) websocket payloads; avoid mapping incompatible messages to `TickData` without validation.
- Add channel-aware parsing so downstream modules can differentiate trade prints, order book levels, etc., or expose raw data through a separate event type.
  - Websocket payloads are tagged per channel; order books emit both `EVENT_TICK` and raw `EVENT_FUBON_MARKET_RAW` messages while trades stay raw.

## 5. Testing & CI coverage
- Expand unit tests to cover websocket reconnect logic, authentication failures, and order rejection paths.
- Mark live tests with explicit `pytest.mark.live` (or similar) and update CI workflow to skip them unless explicitly enabled.
- Introduce regression scenarios covering multiple account switches, invalid subscription parameters, and heartbeat/token refresh failures.
  - Expanded unit tests to cover subscription errors, market-event dispatch, and account-switch validation.

## 6. Documentation updates
- Document standardized BaseGateway usage (e.g., `send_order`, `CancelRequest`) and how events propagate to the EventEngine.
- Provide troubleshooting guidance for new failure modes (heartbeat errors, invalid channel responses, account switch problems).
- Keep READMEs and project overview synchronized with the updated architecture and testing strategy.
