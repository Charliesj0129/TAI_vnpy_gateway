# --- VnPy 及 Qt 核心導入 ---
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp
from vnpy.trader.constant import Exchange
from aenum import extend_enum

# --- 您的 Gateway 導入 ---
# 變更：從 shioaji_gateway 導入 ShioajiGateway
from shioaji_gateway import ShioajiGateway

# --- VnPy 應用模塊導入 ---
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
    """啟動 VeighNa Trader"""

    qapp = create_qapp()

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.write_log("VeighNa 主引擎 MainEngine 初始化完畢。")

    try:
        custom_exchanges = {
            "TWSE": "TWSE", "TOTC": "TOTC", "TAIFEX": "TAIFEX", "TOES": "TOES"
        }
        for name, value in custom_exchanges.items():
            # extend_enum 確保如果枚舉已存在，不會重複添加
            if not hasattr(Exchange, name):
                extend_enum(Exchange, name, value)
    except Exception as e:
        # 在模組加載時，使用 print 而不是 write_log，因為日誌引擎尚未初始化
        print(f"擴展 Exchange 枚舉時發生錯誤: {e}")

    # --- 添加您的 ShioajiGateway ---
    # 變更：直接添加 ShioajiGateway
    main_engine.add_gateway(ShioajiGateway)
    
    # 添加所有需要的應用模塊
    apps_to_add = [
        CtaStrategyApp, CtaBacktesterApp, DataRecorderApp, SpreadTradingApp,
        AlgoTradingApp, ChartWizardApp, RiskManagerApp, DataManagerApp,
        OptionMasterApp, PortfolioManagerApp, PortfolioStrategyApp
    ]
    for app_class in apps_to_add:
        main_engine.add_app(app_class)
    
    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    qapp.exec()

if __name__ == "__main__":
    main()
