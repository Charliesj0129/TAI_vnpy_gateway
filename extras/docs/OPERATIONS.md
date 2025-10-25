# Fubon Gateway Operations Runbook

## Overview
- `vnpy_fubon` ships a production-grade Fubon futures/options gateway for vn.py 2.x.
- All orchestration is CLI-first; GUI startup mirrors `examples/run_fubon_gui.py`.
- Live connectivity mixes REST (contracts/queries) and websocket (market data) flows.

## Environment & Secrets
- **Primary config**: `config/fubon_credentials.toml` (user id/password/CA path). Template lives in `config/api_test_cases.example.toml`.
- **Overrides**: `.env` supports `FUBON_USER_ID`, `FUBON_USER_PASSWORD`, `FUBON_CA_PATH`, `FUBON_CA_PASSWORD`, `FUBON_PRIMARY_ACCOUNT`, `FUBON_EXCHANGE`, `FUBON_ENABLE_LIVE_TESTS`.
- **Logging**: defaults to JSON via `vnpy_fubon/logging_config.py`. Set `FUBON_LOG_DIR` to rotate logs into a persistent directory if needed.
- Export credentials for scripts:
  ```powershell
  setx FUBON_USER_ID "<id>"
  setx FUBON_USER_PASSWORD "<pwd>"
  ```

## Pre-flight Checklist
- Python 3.10+ with vn.py 2.x installed (`pip install -r requirements-dev.txt`).
- Verify CA certificate path and permissions.
- Run quick readiness check: `python scripts/healthcheck.py --mode readiness`.
- Ensure network whitelists allow Fubon API endpoints (REST + WS).
- Confirm log directory disk usage < 80%.

## Boot & Shutdown
- **GUI**: `python examples/run_fubon_gui.py` (loads gateway + optional vn.py apps). Set `FUBON_TRADER_APPS` to comma separated app ids or `all`.
- **Programmatic**: instantiate `FubonGateway` via `MainEngine.add_gateway`.
- **Shutdown**: call `gateway.close()` or exit GUI. The gateway ensures websocket disconnect, cancels token refresh timers, and clears contract caches.

## Lifecycle Runbook
- **Connect**
  1. Run `scripts/healthcheck.py --mode readiness`.
  2. Launch gateway; observe log entry with `gateway_state=connected`.
  3. Ensure contract load count matches expectation (>= 8642).
- **Reconnect (alert fired)**
  1. Inspect logs for `ws_error` / `ws_reconnect_failed`.
  2. If backoff loops > 5 attempts, restart gateway (`close` + `connect`) and verify credentials are valid.
  3. Run `scripts/healthcheck.py --mode liveness` during recovery.
- **Planned maintenance**
  1. Disable trading flows, flush outstanding orders.
  2. `gateway.close()`; archive logs.
  3. Apply upgrades; run `pytest` (unit, replay) before reconnecting.

## Incident Patterns
- **Missing contracts**: run `pytest tests/contract_flow_test.py -q` to validate pipeline. Check logs for `contracts_missing`.
- **Websocket stall**: look for `heartbeat_failed` and ensure `_refresh_token` succeeded; trigger manual reconnect by calling `_schedule_ws_reconnect`.
- **Account mismatch**: warnings emit with `gateway_state=account_warning`; confirm `FUBON_PRIMARY_ACCOUNT` matches login response.

## Test Matrix Execution
- **Fast unit + replay**: `pytest tests/test_gateway_unit.py tests/replay/test_replay_contracts.py -q`.
- **Contract/MainEngine flow**: `pytest tests/contract_flow_test.py`.
- **Subscription stress**: `pytest tests/integration/test_gateway_subscriptions.py`.
- **Full (CI)**: `pytest -m "not live"` (live smoke covered separately).

## Live Smoke Drill
- Prepare credentials + environment.
- Run `python scripts/smoketest_live.py --duration 60 --symbols TXFQ4,TXO13800L5`.
- Inspect artifacts under `artifacts/api_tests/` for websocket captures.

## Housekeeping
- Log rotation handled by `RotatingFileHandler` (1 MB x5). Adjust via `FUBON_LOG_DIR`.
- Replay fixtures live in `tests/replay/data/`; refresh monthly by executing `scripts/smoketest_live.py --record`.
- Artifacts for API tests stored under `artifacts/api_tests/`; prune after audits.
