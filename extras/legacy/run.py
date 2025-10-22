"""
Local launcher for vn.py Trader pre-configured with the Fubon gateway.

This mirrors the official vn.py ``run.py`` entry point while wiring in
``vnpy_fubon.gateway.FubonGateway`` so the team can start the GUI with a single command.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional, Type

from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.setting import SETTINGS
from vnpy.trader.ui import MainWindow, create_qapp

from vnpy_fubon.gateway import FubonGateway


# Optional vn.py apps (loaded on demand via FUBON_TRADER_APPS env var).
def _optional_import(path: str, attr: str) -> Optional[Type]:
    try:
        module = __import__(path, fromlist=[attr])
        return getattr(module, attr)
    except Exception:  # pragma: no cover - depends on local installation
        return None


OPTIONAL_APPS: dict[str, tuple[str, str]] = {
    "ctastrategy": ("vnpy_ctastrategy", "CtaStrategyApp"),
    "ctabacktester": ("vnpy_ctabacktester", "CtaBacktesterApp"),
    "spread_trading": ("vnpy_spreadtrading", "SpreadTradingApp"),
    "option_master": ("vnpy_optionmaster", "OptionMasterApp"),
    "portfolio_strategy": ("vnpy_portfoliostrategy", "PortfolioStrategyApp"),
    "algo_trading": ("vnpy_algotrading", "AlgoTradingApp"),
    "script_trader": ("vnpy_scripttrader", "ScriptTraderApp"),
    "paper_account": ("vnpy_paperaccount", "PaperAccountApp"),
    "chart_wizard": ("vnpy_chartwizard", "ChartWizardApp"),
    "portfolio_manager": ("vnpy_portfoliomanager", "PortfolioManagerApp"),
    "rpc_service": ("vnpy_rpcservice", "RpcServiceApp"),
    "data_manager": ("vnpy_datamanager", "DataManagerApp"),
    "data_recorder": ("vnpy_datarecorder", "DataRecorderApp"),
    "excel_rtd": ("vnpy_excelrtd", "ExcelRtdApp"),
    "risk_manager": ("vnpy_riskmanager", "RiskManagerApp"),
    "web_trader": ("vnpy_webtrader", "WebTraderApp"),
}


def _seed_fubon_settings() -> None:
    """
    Populate SETTINGS with the Fubon gateway and optional connection defaults.
    """

    project_root = Path(__file__).resolve().parent
    SETTINGS.setdefault("gateways", [])
    if "Fubon" not in SETTINGS["gateways"]:
        SETTINGS["gateways"].append("Fubon")

    SETTINGS.setdefault("connects", {})
    SETTINGS["connects"]["Fubon"] = {
        "gateway_name": "Fubon",
        "settings": {
            "user_id": os.getenv("FUBON_USER_ID", ""),
            "password": os.getenv("FUBON_USER_PASSWORD", ""),
            "ca_path": os.getenv("FUBON_CA_PATH", ""),
            "ca_password": os.getenv("FUBON_CA_PASSWORD", ""),
        },
    }

    fubon_settings = SETTINGS["connects"]["Fubon"]["settings"]
    fubon_settings.setdefault("config_path", str((project_root / "config" / "fubon_credentials.toml").resolve()))
    fubon_settings.setdefault("dotenv_path", str((project_root / ".env").resolve()))
    fubon_settings["use_env_only"] = True


def main() -> None:
    """
    Boot the vn.py Trader GUI with the Fubon gateway registered.
    """

    logging.basicConfig(
        level=getattr(logging, os.getenv("FUBON_TRADER_LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    _seed_fubon_settings()

    qapp = create_qapp("vnpy-trader-fubon")
    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)

    class FubonGatewayAdapter(FubonGateway):
        exchanges = getattr(FubonGateway, "exchanges", [])
        default_name = getattr(FubonGateway, "default_name", "Fubon")

        def __init__(self, event_engine, gateway_name="Fubon", **kwargs):
            super().__init__(event_engine, gateway_name=gateway_name, **kwargs)

        def get_default_setting(self):
            return super().get_default_setting()

    main_engine.add_gateway(FubonGatewayAdapter)

    requested = os.getenv("FUBON_TRADER_APPS", "all").strip().lower()
    if not requested or requested == "all":
        desired_apps = set(OPTIONAL_APPS.keys())
    else:
        desired_apps = {
            name.strip()
            for name in requested.split(",")
            if name.strip()
        }
    for key, (module_path, class_name) in OPTIONAL_APPS.items():
        if key not in desired_apps:
            continue
        app_class = _optional_import(module_path, class_name)
        if app_class is None:
            logging.getLogger("run.py").warning(
                "Requested app '%s' (%s.%s) not available in this environment.",
                key,
                module_path,
                class_name,
            )
            continue
        main_engine.add_app(app_class)  # type: ignore[arg-type]

    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    qapp.exec()


if __name__ == "__main__":
    main()
