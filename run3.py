# -*- coding: utf-8 -*-

# --- VnPy 及 Qt 核心導入 ---
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp

# --- 核心：導入您的 FubonGateway ---
# 這一行會自動執行 constant.py 中的枚舉擴展
from fubon_gateway import FubonGateway 

# --- VnPy 功能模塊導入 ---
from vnpy_ctastrategy import CtaStrategyApp
from vnpy_ctabacktester import CtaBacktesterApp
from vnpy_datarecorder import DataRecorderApp
from vnpy_spreadtrading import SpreadTradingApp
from vnpy_algotrading import AlgoTradingApp
from vnpy_chartwizard import ChartWizardApp
from vnpy_riskmanager import RiskManagerApp
from vnpy_datamanager import DataManagerApp
from vnpy_optionmaster import OptionMasterApp
from vnpy_portfoliomanager import PortfolioManagerApp
from vnpy_portfoliostrategy import PortfolioStrategyApp

def main():
    """啟動 VeighNa Trader 主程式"""

    # 建立 QApplication 實例
    qapp = create_qapp()

    # 建立事件驅動引擎
    event_engine = EventEngine()

    # 建立主引擎
    main_engine = MainEngine(event_engine)
    main_engine.write_log("VeighNa 主引擎初始化完畢。")

    # --- 添加 FubonGateway ---
    main_engine.add_gateway(FubonGateway)
    main_engine.write_log("FubonGateway 載入成功。")
    
    # --- 添加所有需要的功能模塊 ---
    apps_to_add = [
        CtaStrategyApp, CtaBacktesterApp, DataRecorderApp, SpreadTradingApp,
        AlgoTradingApp, ChartWizardApp, RiskManagerApp, DataManagerApp,
        OptionMasterApp, PortfolioManagerApp, PortfolioStrategyApp
    ]
    for app_class in apps_to_add:
        main_engine.add_app(app_class)
    
    # 建立主視窗
    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    # 啟動事件循環
    qapp.exec()

if __name__ == "__main__":
    main()