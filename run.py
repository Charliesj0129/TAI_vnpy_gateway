
# --- VnPy 及 Qt 核心導入 ---
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp
from vnpy.trader.constant import Exchange      # 用於 extend_enum
from aenum import extend_enum                  # 用於擴展 Exchange 枚舉
# from qtpy import API_NAME # 移除調試用的 API_NAME 打印
# from qtpy.QtWidgets import QApplication # 移除調試用的 QApplication 打印

# --- 您的 Gateway 導入 ---
from shioaji_session_manager import ShioajiSessionManager # 假設檔案名和類名正確

# --- VnPy 應用模塊導入 (根據您的列表) ---
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
# 根據您的原始列表，以下是被註解掉的，您可以根據需要取消註解
# from vnpy_rpcservice import RpcServiceApp
# from vnpy_excelrtd import ExcelRtdApp
# from vnpy_webtrader import WebTraderApp


def main():
    """啟動 VeighNa Trader"""

    qapp = create_qapp() # QApplication 實例由 create_qapp() 創建和管理

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    main_engine.write_log("VeighNa 主引擎 MainEngine 初始化完畢。")

    # --- 全局擴展 Exchange 枚舉 ---
    # 這一步對於您的自訂交易所是必要的
    try:
        custom_exchanges = {
            "TWSE": "TWSE", "TOTC": "TOTC", "TAIFEX": "TAIFEX", "TOES": "TOES"
        }
        for name, value in custom_exchanges.items():
            if not hasattr(Exchange, name): # 避免重複擴展
                extend_enum(Exchange, name, value)
        # 簡化日誌，只在主引擎中記錄一次開始和結束（可選）
        # main_engine.write_log("Exchange 枚舉擴展完成。")
    except Exception as e:
        main_engine.write_log(f"!!! 擴展 Exchange 枚舉時發生錯誤: {e}", level="error")
    # --- 擴展結束 ---

    # 添加您的 ShioajiSessionManager Gateway
    main_engine.add_gateway(ShioajiSessionManager)
    
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