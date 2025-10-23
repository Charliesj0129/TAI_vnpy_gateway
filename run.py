"""
GUI launcher for the vn.py main engine configured with the Fubon gateway.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Tuple

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp

from vnpy_fubon.gateway import FubonGateway

from vnpy_ctabacktester import CtaBacktesterApp
from vnpy_ctastrategy import CtaStrategyApp
from vnpy_datamanager import DataManagerApp


def _parse_env_file(lines: Iterable[str]) -> Iterable[Tuple[str, str]]:
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] in {"'", '"'} and value[-1] == value[0]:
            value = value[1:-1]
        yield key, value


def _load_env_variables(path: Path) -> None:
    if not path.exists():
        return
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return
    for key, value in _parse_env_file(content.splitlines()):
        os.environ.setdefault(key, value)


def main() -> None:
    """Start the vn.py GUI with the Fubon gateway and core CTA apps."""
    project_root = Path(__file__).resolve().parent
    _load_env_variables(project_root / ".env")

    qapp = create_qapp()

    event_engine = EventEngine()

    main_engine = MainEngine(event_engine)
    main_engine.add_gateway(FubonGateway)

    main_engine.add_app(CtaStrategyApp)
    main_engine.add_app(CtaBacktesterApp)
    main_engine.add_app(DataManagerApp)

    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    qapp.exec()


if __name__ == "__main__":
    main()
