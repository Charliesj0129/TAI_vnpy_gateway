
## Observability Playbook

### Structured Logging
- All gateway logs emit JSON via `logging_config.StructuredJsonFormatter`.
- Default fields: `timestamp`, `level`, `logger`, `message`, `gateway` (`Fubon`), `seq`.
- Domain extras:
  - `gateway_state`: lifecycle breadcrumbs (`connecting`, `connected`, `ws_connected`, `ws_error`, `account_warning`, etc.).
  - `channel` / `symbol`: populated on subscribe/unsubscribe/resubscribe operations.
  - `latency_ms`: used for reconnect backoff timing.
- Example log entry:
```json
{
  "timestamp": "2025-10-15T12:40:10.113421Z",
  "level": "INFO",
  "logger": "vnpy_fubon.gateway",
  "message": "Websocket reconnect successful.",
  "gateway": "Fubon",
  "seq": 4821,
  "gateway_state": "ws_reconnected"
}
```
- Ingest into ELK / Loki with JSON parser; index on `gateway_state`, `symbol`.

### Metrics (proposed Prometheus names)
| Metric | Type | Source | Description |
| --- | --- | --- | --- |
| `fubon_gateway_contract_total` | Gauge | contract loader | Number of contracts published to MainEngine |
| `fubon_gateway_ws_reconnect_total` | Counter | `_perform_ws_reconnect` | Successful websocket reconnect attempts |
| `fubon_gateway_ws_reconnect_fail_total` | Counter | `_perform_ws_reconnect` | Failed reconnects (permanent errors) |
| `fubon_gateway_subscription_active` | Gauge | `_active_subscriptions` size | Active WS subscriptions |
| `fubon_gateway_account_switch_fail_total` | Counter | `switch_account` warnings | Account switch validation failures |
| `fubon_gateway_heartbeat_latency_ms` | Histogram | `_refresh_token` | Heartbeat execution timing |

Implementation sketch: instrument via `prometheus_client` in gateway init (not yet wired).

### Alerts
- **ContractDepleted**: `fubon_gateway_contract_total < 8000` for 5 min.
- **WSReconnectStorm**: `rate(fubon_gateway_ws_reconnect_total[5m]) > 3`.
- **AccountErrors**: `increase(fubon_gateway_account_switch_fail_total[15m]) > 0`.

### Dashboards (Grafana suggestions)
1. **Gateway Overview**
   - Contracts gauge vs expectation.
   - Active subscriptions (books/trades) stacked.
   - Reconnect attempts (success/fail) per hour.
2. **Latency & Heartbeat**
   - Histogram or percentile panel for `heartbeat_latency_ms`.
   - Panel for reconnect backoff durations (`latency_ms` from logs).
3. **Account Diagnostics**
   - Table of `account_warning` log messages filtered by account id.
   - Count of successful `switch_account` operations vs failures.

### Log-based SLOs
- **Availability**: 99.5% of minutes with `gateway_state=connected` and no `ws_error`.
- **Recovery Time**: 95% of `ws_reconnect_scheduled` events should see `ws_reconnected` within 90 seconds (derive from log timestamps).

### Tracing Hooks
- For end-to-end order tracing, attach `trace_id` via `logging.LoggerAdapter` when integrating with vn.py apps (hooks ready).
- Use `EVENT_FUBON_MARKET_RAW` to export raw WS payloads for debugging (already emitted).
