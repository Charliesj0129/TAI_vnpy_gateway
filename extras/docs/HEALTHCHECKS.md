
## Health Check Guide

### Script Overview
- Entry point: `python scripts/healthcheck.py --mode {readiness|liveness}`.
- Output: single-line JSON payload with `status` (`ok` / `fail`), `checks`, and `details`.
- Exit code: `0` on success, `1` when *any* check fails.

### Readiness (`--mode readiness`)
| Check | Description | Failure Signal |
| --- | --- | --- |
| `python_version` | Verifies runtime >= 3.10 | Exit if mismatch |
| `vnpy_import` | Ensures `vnpy.event` and `MainEngine` import cleanly | Missing dependency |
| `config_files` | Confirms `.env`/`config/fubon_credentials.toml` readable | Misconfigured secrets |
| `credentials` | Validates required env vars populated | `FUBON_USER_ID`/password absent |
| `gateway_smoke` | Instantiates `FubonGateway` with dummy event engine | Constructor regressions |

Invocation:
```powershell
python scripts/healthcheck.py --mode readiness --config config/fubon_credentials.toml
```

### Liveness (`--mode liveness`)
| Check | Description | Failure Signal |
| --- | --- | --- |
| `log_dir` | Confirms log directory reachable & writable | Filesystem saturated |
| `event_loop` | Pings optional PID file / heartbeat socket when provided | Hung gateway process |
| `network` | Optional TCP reachability test to Fubon REST/WS endpoints | Network outage |

Invocation:
```bash
python scripts/healthcheck.py --mode liveness --log-dir log/
```

### Integration Tips
- Kubernetes: wire script into `exec` `readinessProbe` / `livenessProbe` (`timeoutSeconds=10`).
- Systemd: use `ExecStartPre=python scripts/healthcheck.py --mode readiness`.
- CI: run readiness at start of smoke job to fail fast when secrets missing.

### JSON Sample
```json
{
  "status": "ok",
  "mode": "readiness",
  "checks": {
    "python_version": "3.12.4",
    "vnpy_import": "ok",
    "config_files": "ok",
    "credentials": "env",
    "gateway_smoke": "ok"
  },
  "timestamp": "2025-10-15T12:45:00Z"
}
```

### Extending Checks
- Add bespoke validations in `scripts/healthcheck.py::READINESS_CHECKS` / `LIVENESS_CHECKS`.
- For external dependencies (DB, MQ), add a `socket.create_connection` probe and record latency in `details`.
