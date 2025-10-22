# Fubon API Behaviour Analysis

This document tracks verified behaviour of the Fubon Securities Next Generation API while integrating it with the vn.py gateway.

## Connection & Session Lifecycle

- **Authentication flow**  
  - Pending verification: confirm whether the SDK performs implicit session renewal or requires explicit heartbeat.
- **Certificate handling**  
  - Ensure the `.pfx` file is accessible and the SDK supports password-protected certificates.
- **Error surface**  
  - Catalog common error codes/messages returned on login failure.

## Execution APIs

| API | Request shape | Response shape | Blocking / Async | Notes |
| --- | --- | --- | --- | --- |
| place_order | `{symbol, price, quantity, side, order_type, account?}` | `{order_id, status, message}` | TBD | Document required enum values (e.g. side, order_type). |
| cancel_order | `{order_id}` | `{result, message}` | TBD | Confirm if cancel requests are idempotent. |

## Account & Portfolio APIs

| API | Purpose | Response fields | Data types | Remarks |
| --- | --- | --- | --- | --- |
| query_account | Retrieve account summary | `cash`, `available`, `margin`, `buying_power`, ... | Decimal represented as string? | Verify rounding and currency. |
| query_positions | Fetch open positions | `symbol`, `volume`, `avg_price`, `unrealized_pnl`, ... | TBD | Confirm whether multi-market positions require separate calls. |

## Market Data APIs

- **Subscription model**  
  - Determine whether real-time quotes arrive via callbacks, queues, or polling.
- **Supported channels**  
  - Level 1 quotes, depth, time & sales. Clarify naming of instruments (e.g., `2330.TW` vs `2330`).
- **Throughput**  
  - Document rate limits and throttling behaviour observed during load testing.

## Error Codes & Handling

| Code | Message | Interpretation | Recommended handling |
| --- | --- | --- | --- |
| TBD | TBD | TBD | TBD |

## Rate Limits

- Document any SDK or backend limitations encountered (requests per second/minute, subscription caps).
- Capture auto-throttle responses and retry windows.

## Asynchronous Callbacks

- List callback interfaces (e.g., order updates, fills, quote updates).
- Provide signature and threading model assumptions.

## Test Harness Notes

- Test definitions live in `config/api_test_cases.toml`. Update that file to expand coverage.
- Raw request/response artifacts are stored under `artifacts/api_tests/`.
- Use `pytest --enable-live-tests` for manual validation sessions.

## Open Questions

- [ ] How are session timeouts communicated by the SDK?
- [ ] Is there a paper-trading environment separate from production?
- [ ] Does the SDK expose websocket endpoints directly?

