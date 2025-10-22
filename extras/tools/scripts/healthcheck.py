from __future__ import annotations

import argparse
import importlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Tuple

MIN_PYTHON = (3, 10)


class CheckResult:
    def __init__(self, ok: bool, detail: Any) -> None:
        self.ok = ok
        self.detail = detail


def _check_python_version(_: Dict[str, Any]) -> CheckResult:
    version = sys.version_info
    ok = version >= MIN_PYTHON
    return CheckResult(ok, ".".join(map(str, version[:3])))


def _check_vnpy_import(_: Dict[str, Any]) -> CheckResult:
    try:
        importlib.import_module("vnpy.event")
        importlib.import_module("vnpy.trader.engine")
    except Exception as exc:  # pragma: no cover - environment specific
        return CheckResult(False, f"import error: {exc}")
    return CheckResult(True, "ok")


def _check_config_files(ctx: Dict[str, Any]) -> CheckResult:
    paths = [ctx.get("config_path"), ctx.get("dotenv_path")]
    missing = [str(p) for p in paths if p and not p.exists()]
    if missing:
        return CheckResult(False, {"missing": missing})
    return CheckResult(True, "ok")


def _check_credentials(ctx: Dict[str, Any]) -> CheckResult:
    required_env = ["FUBON_USER_ID", "FUBON_USER_PASSWORD", "FUBON_CA_PATH", "FUBON_CA_PASSWORD"]
    provided = [env for env in required_env if os.getenv(env)]
    if len(provided) == len(required_env):
        return CheckResult(True, "env")

    config_path: Path | None = ctx.get("config_path")
    if not config_path or not config_path.exists():
        return CheckResult(False, "env+config missing")

    try:
        from vnpy_fubon.config import load_configuration

        credentials, _ = load_configuration(config_path=config_path, dotenv_path=ctx.get("dotenv_path"))
        ok = all([credentials.user_id, credentials.user_password, credentials.ca_path, credentials.ca_password])
        return CheckResult(ok, "config")
    except Exception as exc:  # pragma: no cover
        return CheckResult(False, f"config error: {exc}")


def _check_gateway_smoke(_: Dict[str, Any]) -> CheckResult:
    try:
        from vnpy_fubon.gateway import FubonGateway

        class _DummyEngine:
            def put(self, _event: Any) -> None:
                pass

        FubonGateway(_DummyEngine())
        return CheckResult(True, "ok")
    except Exception as exc:
        return CheckResult(False, f"gateway init failed: {exc}")


def _check_log_dir(ctx: Dict[str, Any]) -> CheckResult:
    log_dir: Path = ctx.get("log_dir") or Path("log")
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        test_file = log_dir / ".healthcheck"
        test_file.write_text(str(time.time()))
        test_file.unlink(missing_ok=True)
        return CheckResult(True, str(log_dir))
    except Exception as exc:  # pragma: no cover - fs issues
        return CheckResult(False, f"log dir error: {exc}")


def _check_pid(ctx: Dict[str, Any]) -> CheckResult:
    pid_file: Path | None = ctx.get("pid_file")
    if not pid_file:
        return CheckResult(True, "skipped")
    if not pid_file.exists():
        return CheckResult(False, f"pid file {pid_file} missing")
    try:
        pid = int(pid_file.read_text().strip())
    except Exception as exc:
        return CheckResult(False, f"pid parse error: {exc}")
    if pid <= 0:
        return CheckResult(False, f"pid invalid: {pid}")
    try:
        os.kill(pid, 0)
    except OSError as exc:  # pragma: no cover - platform specific
        return CheckResult(False, f"process unreachable: {exc}")
    return CheckResult(True, pid)


def _check_network(ctx: Dict[str, Any]) -> CheckResult:
    targets: Iterable[str] = ctx.get("network_targets") or []
    results: Dict[str, str] = {}
    ok = True
    for target in targets:
        host, _, port = target.partition(":")
        port_num = int(port or 443)
        addr = (host, port_num)
        try:
            start = time.monotonic()
            with socket.create_connection(addr, timeout=ctx.get("timeout", 3)):
                latency = int((time.monotonic() - start) * 1000)
                results[target] = f"ok ({latency} ms)"
        except Exception as exc:  # pragma: no cover - network specific
            results[target] = f"fail ({exc})"
            ok = False
    return CheckResult(ok, results or "skipped")


READINESS_CHECKS: Tuple[Tuple[str, Callable[[Dict[str, Any]], CheckResult]], ...] = (
    ("python_version", _check_python_version),
    ("vnpy_import", _check_vnpy_import),
    ("config_files", _check_config_files),
    ("credentials", _check_credentials),
    ("gateway_smoke", _check_gateway_smoke),
)

LIVENESS_CHECKS: Tuple[Tuple[str, Callable[[Dict[str, Any]], CheckResult]], ...] = (
    ("log_dir", _check_log_dir),
    ("pid", _check_pid),
    ("network", _check_network),
)


def run(mode: str, context: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    checks = READINESS_CHECKS if mode == "readiness" else LIVENESS_CHECKS
    result_payload: Dict[str, Any] = {}
    ok = True
    for name, check in checks:
        try:
            result = check(context)
        except Exception as exc:  # pragma: no cover - defensive
            result = CheckResult(False, f"exception: {exc}")
        result_payload[name] = result.detail
        ok = ok and result.ok
    return ok, result_payload


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fubon gateway health checks")
    parser.add_argument("--mode", choices=["readiness", "liveness"], default="readiness")
    parser.add_argument("--config", dest="config_path", type=Path, default=Path("config") / "fubon_credentials.toml")
    parser.add_argument("--dotenv", dest="dotenv_path", type=Path, default=Path(".env"))
    parser.add_argument("--log-dir", dest="log_dir", type=Path, default=None)
    parser.add_argument("--pid-file", dest="pid_file", type=Path, default=None)
    parser.add_argument("--network", nargs="*", dest="network_targets", default=None, help="host[:port] targets to test")
    parser.add_argument("--timeout", type=int, default=3)
    parser.add_argument("--output", choices=["json", "pretty"], default="json")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    context = {
        "config_path": args.config_path,
        "dotenv_path": args.dotenv_path,
        "log_dir": args.log_dir,
        "pid_file": args.pid_file,
        "network_targets": args.network_targets,
        "timeout": args.timeout,
    }

    start = time.time()
    ok, details = run(args.mode, context)
    payload = {
        "status": "ok" if ok else "fail",
        "mode": args.mode,
        "checks": details,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_ms": int((time.time() - start) * 1000),
    }

    if args.output == "json":
        print(json.dumps(payload, ensure_ascii=False))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")

    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
