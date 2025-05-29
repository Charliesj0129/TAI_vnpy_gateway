from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.ui import MainWindow, create_qapp
from vnpy.trader.constant import Exchange
from aenum import extend_enum
import sys, os
# --- 修改 Gateway 導入 ---
# from shioaji_gateway import ShioajiGateway # 註解掉或刪除這一行
from shioaji_session_manager import ShioajiSessionManager # <<< 新增這一行，假設您的檔案名為 shioaji_session_manager.py

#from fubon_gateway import FubonGateway

from vnpy_ctastrategy import CtaStrategyApp
from vnpy_ctabacktester import CtaBacktesterApp
from vnpy_datarecorder import DataRecorderApp
from vnpy_spreadtrading import SpreadTradingApp
from vnpy_algotrading import AlgoTradingApp
from vnpy_chartwizard import ChartWizardApp
from vnpy_riskmanager import RiskManagerApp
from vnpy_datamanager import DataManagerApp
from vnpy_portfoliomanager import PortfolioManagerApp
from vnpy_portfoliostrategy import PortfolioStrategyApp
#from vnpy_excelrtd import ExcelRtdApp
#from vnpy_rpcservice import RpcServiceApp
from vnpy_optionmaster import OptionMasterApp



def main():
    """Start VeighNa Trader"""
    qapp = create_qapp()

    event_engine = EventEngine()
    main_engine = MainEngine(event_engine)
    try:
        print("Extending Exchange enum with custom values: TWSE, TOTC, TAIFEX, TOES")
        custom_exchanges = {
            "TWSE": "TWSE", # value can be same as name, or more descriptive if needed
            "TOTC": "TOTC",
            "TAIFEX": "TAIFEX",
            "TOES": "TOES" 
        }
        for name, value in custom_exchanges.items():
            if not hasattr(Exchange, name): # 避免重複擴展 (雖然 extend_enum 可能會處理)
                extend_enum(Exchange, name, value)
                print(f"  Extended Exchange with {name} -> {getattr(Exchange, name).value}")
            else:
                print(f"  Exchange.{name} already exists.")
        print(f"Exchange enum extended. Current members: {[member.name for member in Exchange]}")
    except Exception as e:
        print(f"!!! Critical error during Exchange enum extension: {e}")
        print("!!! Gateway functionality for custom exchanges might be affected.")
    # --- <<< 擴展結束 >>> ---


    main_engine.add_gateway(ShioajiSessionManager) 

    main_engine.add_app(CtaStrategyApp)
    main_engine.add_app(CtaBacktesterApp)
    main_engine.add_app(DataRecorderApp)
    main_engine.add_app(SpreadTradingApp)
    main_engine.add_app(AlgoTradingApp)
    main_engine.add_app(ChartWizardApp)
    main_engine.add_app(RiskManagerApp)
    main_engine.add_app(DataManagerApp)
    #main_engine.add_app(ExcelRtdApp)
    #main_engine.add_app(RpcServiceApp)
    main_engine.add_app(OptionMasterApp)
    main_engine.add_app(PortfolioManagerApp)
    main_engine.add_app(PortfolioStrategyApp)
    # main_engine.add_app(RiskManagerApp) # RiskManagerApp 已經在上面添加過了，避免重複


    main_window = MainWindow(main_engine, event_engine)
    main_window.showMaximized()

    qapp.exec()


if __name__ == "__main__":
    main()