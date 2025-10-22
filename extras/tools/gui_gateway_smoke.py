"""
Automated GUI smoke test to verify that the Fubon gateway can be
connected and disconnected via the vn.py Trader UI stack.

Usage
-----
python tools/gui_gateway_smoke.py

Environment variables FUBON_USER_ID, FUBON_USER_PASSWORD, FUBON_CA_PATH,
and FUBON_CA_PASSWORD must be populated (or persisted in vn.py's config)
for the gateway connection to succeed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QTimer

# Ensure repository root on sys.path for local execution.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vnpy.event import Event, EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp
from vnpy.trader.locale import _
from vnpy.trader.object import LogData

from vnpy_fubon.gateway import FubonGateway
from vnpy_fubon.vnpy_compat import EVENT_LOG


@dataclass
class SmokeResult:
    connected: bool = False
    error: Optional[str] = None
    log_messages: list[str] = None

    def __post_init__(self) -> None:
        if self.log_messages is None:
            self.log_messages = []


class FubonGatewayAdapter(FubonGateway):
    exchanges = getattr(FubonGateway, "exchanges", [])
    default_name = getattr(FubonGateway, "default_name", "FUBON")

    def __init__(self, event_engine, gateway_name="Fubon", **kwargs):
        super().__init__(event_engine, gateway_name=gateway_name, **kwargs)


def ensure_credentials() -> None:
    missing = [
        name
        for name in ("FUBON_USER_ID", "FUBON_USER_PASSWORD", "FUBON_CA_PATH", "FUBON_CA_PASSWORD")
        if not os.getenv(name)
    ]
    if missing:
        raise RuntimeError(
            f"Missing credential environment variables: {', '.join(missing)}. "
            "Populate them before running the smoke test."
        )


def main(timeout_ms: int = 30_000) -> None:
    ensure_credentials()

    qapp = create_qapp("vnpy-fubon-gui-smoke")
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)

    result = SmokeResult()

    def handle_log(event: Event) -> None:
        data = event.data
        if isinstance(data, LogData):
            message = data.msg or ""
        elif isinstance(data, str):
            message = data
        else:
            message = str(data or "")

        result.log_messages.append(message)
        lower_message = message.lower()
        if "fubon gateway connected" in lower_message or (
            "gateway connected" in lower_message and "fubon" in lower_message
        ):
            result.connected = True
            QTimer.singleShot(500, shutdown)
        elif "failed" in lower_message or "error" in lower_message:
            result.error = message

    event_engine.register(EVENT_LOG, handle_log)

    gateway = main_engine.add_gateway(FubonGatewayAdapter)
    main_window = MainWindow(main_engine, event_engine)
    main_window.hide()

    def connect_gateway() -> None:
        try:
            setting = gateway.get_default_setting()
            if not setting:
                result.error = "Gateway did not return default settings."
                shutdown()
                return
            main_engine.connect(setting, gateway.gateway_name)
        except Exception as exc:
            result.error = str(exc)
            shutdown()

    def poll_connection() -> None:
        if result.connected or result.error:
            return

        if getattr(gateway, "client", None) and getattr(gateway, "_ws_connected", False):
            result.connected = True
            QTimer.singleShot(500, shutdown)
            return

        QTimer.singleShot(500, poll_connection)

    def shutdown() -> None:
        try:
            main_engine.close()  # type: ignore[attr-defined]
        except Exception:
            pass
        if main_window.isVisible():
            main_window.close()
        qapp.quit()

    QTimer.singleShot(0, connect_gateway)
    QTimer.singleShot(500, poll_connection)
    QTimer.singleShot(timeout_ms, shutdown)

    qapp.exec()
    event_engine.stop()

    if not result.connected:
        diagnostics = "\n".join(result.log_messages[-10:]) or "No log messages captured."
        raise SystemExit(
            _("Fubon GUI smoke test failed. Last log messages:\n{}").format(diagnostics)
            if result.error is None
            else f"Fubon GUI smoke test failed: {result.error}"
        )

    print("Fubon GUI smoke test passed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - manual execution helper
        sys.stderr.write(f"{exc}\n")
        sys.exit(1)
