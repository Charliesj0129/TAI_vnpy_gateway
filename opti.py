# -*- coding: utf-8 -*-
"""
CTA 策略參數優化腳本 (無 UI)

功能：
1. 擴展 Exchange 枚舉以支持台灣市場。
2. 使用指定的 CTA 策略 (例如 MultiTimeframeStrategy) 進行參數優化。
3. 針對指定的合約 (例如 TXFR1.TAIFEX) 和時間段。
4. 支持遺傳算法或暴力窮舉優化。
5. 處理多進程優化時 Exchange 枚舉可能未擴展的問題。
6. 假設回測數據已存在於 VnPy 數據庫中，或提供從 CSV 導入的輔助函數。
"""
import multiprocessing
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.setting import SETTINGS          # 用於獲取數據庫配置等
from vnpy.trader.database import get_database     # 用於數據庫操作
from vnpy.trader.object import BarData            # K線數據對象

from vnpy_ctastrategy import CtaStrategyApp
from vnpy_ctastrategy.backtesting import OptimizationSetting, BacktestingEngine # 導入 BacktestingEngine

# 策略導入
try:
    from vnpy_ctastrategy.strategies.multi_signal_strategy import MultiSignalStrategy
except ImportError:
    try:
        from multi_signal_strategy  import MultiSignalStrategy
        print("從當前目錄成功導入 MultiTimeframeStrategy。")
    except ImportError:
        print("錯誤：找不到 MultiTimeframeStrategy。")
        sys.exit(1)

# 優化函數導入
try:
    from vnpy.trader.optimize import run_ga_optimization, run_bf_optimization
    print("從 vnpy.trader.optimize 成功導入優化函數。")
except ImportError:
    try:
        from vnpy_ctastrategy.backtesting.optimize import run_ga_optimization, run_optimization as run_bf_optimization
        print("從 vnpy_ctastrategy.backtesting.optimize 成功導入優化函數 (備選路徑)。")
    except ImportError:
        print("錯誤：在 vnpy.trader.optimize 和 vnpy_ctastrategy.backtesting.optimize 中均未找到優化函數。")
        sys.exit(1)

from vnpy_datamanager import DataManagerApp
from datetime import datetime, timedelta
import logging
import sys
import os
from typing import List, Dict, Any
import pandas as pd
import traceback # 導入 traceback
import numpy as np # 用於設置隨機種子
import random      # 用於設置隨機種子

try:
    from aenum import extend_enum
except ImportError:
    print("錯誤：請先安裝 aenum 庫 (pip install aenum) 以支持 Exchange 枚舉擴展。")
    sys.exit(1)

# --- 全局配置 ---
VT_SYMBOL: str = "TXFR1.TAIFEX"
INTERVAL: Interval = Interval.MINUTE
START_DATE: datetime = datetime(2020, 1, 1)
END_DATE: datetime = datetime(2024, 12, 31)
CAPITAL: int = 1_000_000_000_000_000_000_000
SLIPPAGE: float = 0.1
RATE: float = 0.00145
# --- 重要：合約乘數和最小跳動價位 ---
# 以下為台指期貨 (TXF) 的常用值，如果回測其他合約，務必修改！
SIZE_FOR_ENGINE: int = 1 # 傳遞給BacktestingEngine的size參數，代表交易單位（例如1口期貨）
CONTRACT_MULTIPLIER: int = 200 # 真實的合約價值乘數 (例如台指期一點200元)，用於策略內部計算或更精確的績效分析
PRICETICK: float = 0.5  # 最小價格變動 (例如台指期為0.5點)
                        # 請務必根據您回測的具體合約和您使用的VnPy版本中BacktestingEngine對此參數的期望來設置。

STRATEGY_CLASS: type = MultiSignalStrategy  # 使用的策略類型
OPTIMIZATION_TARGET: str = "sharpe_ratio"
CSV_DATA_FILE_PATH: str = "data/txf_continuous_1min_2018_2024.csv" # 示例路徑
OPTIMIZATION_ALGORITHM: str = "GA"
GA_POPULATION_SIZE: int = 50
GA_NGEN_SIZE: int = 10
OPTIMIZATION_PROCESSES: int = 8
RANDOM_SEED: int = 2025 # 用於遺傳算法的隨機種子

# --- 枚舉擴展函數 ---
def extend_exchange_enum_if_needed():
    """
    擴展 vnpy.trader.constant.Exchange 枚舉以包含台灣市場常用的交易所。
    """
    custom_exchanges = {
        "TAIFEX": "臺灣期貨交易所", "TFE": "臺灣期貨交易所",
        "TWSE": "臺灣證券交易所", "TOTC": "證券櫃檯買賣中心",
    }
    # 檢查是否已經在當前進程擴展過，避免不必要的重複操作和打印
    # setattr 用於給函數對象添加一個屬性作為標記
    if getattr(extend_exchange_enum_if_needed, "extended_in_current_process", False) and multiprocessing.current_process().name == "MainProcess":
        # print("Exchange 枚舉已在主進程中處理過。") # 可選：如果想看這個信息
        return

    # print(f"進程 {multiprocessing.current_process().name} ({os.getpid()})：正在檢查並擴展 Exchange 枚舉...")
    all_newly_extended_in_this_call = False
    for name, description in custom_exchanges.items():
        if not hasattr(Exchange, name):
            try:
                extend_enum(Exchange, name, name)
                if multiprocessing.current_process().name == "MainProcess": # 只在主進程打印擴展信息
                    print(f"  主進程: 已擴展 Exchange: {name} (描述: {description})")
                all_newly_extended_in_this_call = True
            except Exception: # 更具體地捕獲可能的異常，例如 aenum 的 ValueError
                # print(f"  進程 {os.getpid()}: 擴展 Exchange.{name} 失敗: {e}")
                pass # 容忍可能的並發擴展嘗試或已擴展的情況
    
    if multiprocessing.current_process().name == "MainProcess":
        if all_newly_extended_in_this_call:
            print("Exchange 枚舉已在主進程中完成擴展檢查。")
        else:
            print("所有自定義交易所已存在於主進程的 Exchange 枚舉中或已處理。")
        setattr(extend_exchange_enum_if_needed, "extended_in_current_process", True)


# --- 數據準備輔助函數 ---
def prepare_data_from_csv_to_db(
    vt_symbol: str, interval: Interval, start_date_filter: datetime,
    end_date_filter: datetime, csv_file_path: str
):
    print(f"\n[數據準備] 正在檢查數據庫中 {vt_symbol} ({interval.value}) 的數據...")
    db = get_database()
    symbol, exchange_value = vt_symbol.split(".")
    try:
        exchange_enum = Exchange(exchange_value) 
    except ValueError:
        print(f"錯誤：交易所 '{exchange_value}' 在 Exchange 枚舉中未定義。")
        return False
    existing_bars = db.load_bar_data(
        symbol=symbol, exchange=exchange_enum, interval=interval,
        start=start_date_filter, end=end_date_filter
    )
    if existing_bars:
        print(f"  數據庫中已找到 {len(existing_bars)} 條 {vt_symbol} 的 K 線數據。")
        return True
    print(f"  數據庫中數據不足。嘗試從 CSV '{csv_file_path}' 加載...")
    if not os.path.exists(csv_file_path):
        print(f"  錯誤：CSV 文件 '{csv_file_path}' 未找到。")
        return False
    try:
        df = pd.read_csv(csv_file_path, parse_dates=['datetime'])
        df.rename(columns={"open": "open_price", "high": "high_price",
                           "low": "low_price", "close": "close_price"}, inplace=True)
        bars_to_save: List[BarData] = []
        for _, row in df.iterrows():
            bar_datetime_naive = row["datetime"].to_pydatetime()
            if not (start_date_filter <= bar_datetime_naive < end_date_filter + timedelta(days=1)):
                continue
            bar = BarData(
                symbol=symbol, exchange=exchange_enum, datetime=bar_datetime_naive,
                interval=interval, volume=float(row["volume"]),
                open_price=float(row["open_price"]), high_price=float(row["high_price"]),
                low_price=float(row["low_price"]), close_price=float(row["close_price"]),
                turnover=float(row.get("turnover", 0.0)),
                open_interest=float(row.get("open_interest", 0.0)), gateway_name="DB",
            )
            bars_to_save.append(bar)
        if bars_to_save:
            print(f"  從 CSV 讀取並篩選出 {len(bars_to_save)} 條數據。正在導入數據庫...")
            db.save_bar_data(bars_to_save)
            print("  數據已導入數據庫。")
            return True
        else:
            print("  CSV 文件中無符合時段的數據。")
            return False
    except Exception as e:
        print(f"  從 CSV 加載或導入數據時出錯: {e}")
        traceback.print_exc()
        return False

# --- 優化評估相關函數 ---
def evaluate_strategy_parameters(strategy_setting: Dict[str, Any]) -> Dict[str, Any]:
    """
    評估函數，用於單次回測。
    接收策略參數字典，返回回測結果字典。
    """
    if multiprocessing.current_process().name != "MainProcess": 
        extend_exchange_enum_if_needed() # 確保子進程枚舉已擴展

    # 每次評估都創建新的引擎實例，避免狀態污染
    engine = BacktestingEngine()
    
    # 策略特定參數的合理性檢查 (示例)
    # if "fast_window" in strategy_setting and "slow_window" in strategy_setting:
    #     if strategy_setting["fast_window"] >= strategy_setting["slow_window"]:
    #         # print(f"  參數無效 (fast_window >= slow_window): {strategy_setting}，跳過此組。")
    #         return {OPTIMIZATION_TARGET: -float("inf")}

    engine.set_parameters(
        vt_symbol=VT_SYMBOL,
        interval=INTERVAL, 
        start=START_DATE,  
        end=END_DATE,    
        rate=RATE,
        slippage=SLIPPAGE,
        capital=CAPITAL,
        size=SIZE_FOR_ENGINE,       
        pricetick=PRICETICK 
    )
    
    engine.add_strategy(STRATEGY_CLASS, strategy_setting)
    
    statistics = {OPTIMIZATION_TARGET: -float("inf")} # 預設為極差績效
    try:
        engine.load_data()
        engine.run_backtesting()
        
        engine.calculate_result() 
        
        if engine.daily_df is None or engine.daily_df.empty or len(engine.daily_df) < 2:
            # print(f"  參數 {strategy_setting}: daily_df 為空或數據不足，無法計算統計指標。")
            # statistics 保持為預設的極差值
            pass # statistics will remain as default poor performance
        else:
            # 在計算統計數據前，可以選擇性地清理 daily_df
            # df_cleaned = engine.daily_df.copy()
            # df_cleaned.dropna(subset=["daily_return", "equity_curve"], inplace=True) # 移除有NaN的行
            # if not df_cleaned.index.is_unique: # 檢查並移除重複索引
            #     df_cleaned = df_cleaned[~df_cleaned.index.duplicated(keep="first")]
            # if len(df_cleaned) < 2:
            #     pass # 數據不足
            # else:
            #     engine.daily_df = df_cleaned # 將清理後的數據賦回 (注意：這可能會修改引擎內部狀態，需謹慎)
            #     current_statistics = engine.calculate_statistics()
            #     statistics.update(current_statistics)
            
            # 簡化：直接嘗試計算，如果內部有錯誤，由外層 try-except 捕獲
            current_statistics = engine.calculate_statistics()
            statistics.update(current_statistics)

    except ZeroDivisionError: # 特定的、預期可能發生的錯誤
        # print(f"  評估參數 {strategy_setting} 時發生 ZeroDivisionError (可能由於無交易或波動為零)。")
        pass # statistics 保持為極差值
    except KeyError: # 特定的鍵錯誤
        # print(f"  評估參數 {strategy_setting} 時發生 KeyError: {e_key} (可能由於daily_df缺少列)。")
        pass
    except Exception:
        # print(f"  評估參數 {strategy_setting} 時發生其他錯誤: {e_eval}")
        # traceback.print_exc(file=sys.stderr) # 詳細錯誤輸出到stderr，避免干擾stdout的優化進度條
        pass 
    
    if OPTIMIZATION_TARGET not in statistics: # 再次確保目標字段存在
        statistics[OPTIMIZATION_TARGET] = -float("inf") 

    return statistics

def get_optimization_target_value(result: Dict[str, Any]) -> float:
    """
    從回測結果字典中提取優化目標的值。
    """
    value = result.get(OPTIMIZATION_TARGET, None) 
    try:
        return float(value) if value is not None else -float("inf")
    except (ValueError, TypeError):
        return -float("inf")


# --- 主優化腳本函數 ---
def main_optimization_script():
    print("======================================================================")
    print("                VnPy CTA 策略參數優化腳本 (無 UI)                     ")
    print("======================================================================")
    print(f"當前時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    print("\n[步驟 0] 擴展 Exchange 枚舉 (主進程)...") 
    extend_exchange_enum_if_needed()

    if OPTIMIZATION_ALGORITHM.upper() == "GA":
        print(f"\n為遺傳算法設置隨機種子: {RANDOM_SEED}")
        np.random.seed(RANDOM_SEED)
        random.seed(RANDOM_SEED)

    print("\n[步驟 1] 初始化 VnPy 引擎...")
    event_engine = EventEngine() 
    main_engine = MainEngine(event_engine)
    main_engine.write_log("腳本：VnPy 主引擎初始化完畢。")

    try:
        main_engine.add_app(CtaStrategyApp)
        main_engine.add_app(DataManagerApp)
    except Exception as e:
        print(f"錯誤：添加 VnPy App 時發生問題: {e}。")
        return

    print("\n[步驟 2] 準備回測數據...")
    data_ready = prepare_data_from_csv_to_db(
        vt_symbol=VT_SYMBOL, interval=INTERVAL,
        start_date_filter=START_DATE, end_date_filter=END_DATE,
        csv_file_path=CSV_DATA_FILE_PATH
    )
    if not data_ready:
        print("警告：回測數據準備可能未成功。優化可能失敗。")
    else:
        print("數據準備檢查完畢。")

    print("\n[步驟 3] 配置優化參數...")
    optimization_setting = OptimizationSetting()
    optimization_setting.set_target(OPTIMIZATION_TARGET) 

    print(f"為策略 {STRATEGY_CLASS.__name__} 配置優化參數:")
    optimization_setting.add_parameter(name="rsi_window", start=2.0, end=40.0, step=2.0)
    optimization_setting.add_parameter(name="rsi_level", start=5.0, end=30.0, step=5.0)
    optimization_setting.add_parameter(name="cci_window", start=5.0, end=50.0, step=5.0)
    optimization_setting.add_parameter(name="cci_level", start=-200.0, end=200.0, step=20.0)
    optimization_setting.add_parameter(name="fast_window", start=3.0, end=30.0, step=1.0)
    optimization_setting.add_parameter(name="slow_window", start=5.0, end=80.0, step=5.0)

    print(f"  優化目標 (用於排序): {OPTIMIZATION_TARGET}") 
    print("  待優化參數:")
    for param_name, value_list in optimization_setting.params.items():
        if isinstance(value_list, list) and value_list:
            print(f"    - {param_name}: values={value_list}")
        else:
            print(f"    - {param_name}: {value_list} (格式非預期列表)")

    print("\n[步驟 4] 回測引擎參數將在評估函數內部設置。")

    print("\n[步驟 5] 開始執行參數優化...")
    print(f"  策略: {STRATEGY_CLASS.__name__}")
    print(f"  合約: {VT_SYMBOL}, 週期: {INTERVAL.value}")
    print(f"  回測時段: {START_DATE.strftime('%Y-%m-%d')} 至 {END_DATE.strftime('%Y-%m-%d')}")
    print(f"  優化算法: {OPTIMIZATION_ALGORITHM}")

    current_processes_count = OPTIMIZATION_PROCESSES
    actual_max_workers = current_processes_count 
    if current_processes_count <= 0: 
        actual_max_workers = 1
    
    if actual_max_workers > 1: 
        print(f"  將使用 {actual_max_workers} 個進程進行並行優化。")
    elif actual_max_workers == 1 :
        print(f"  將使用 {actual_max_workers} 個進程 (效果上類似單進程，但仍通過Pool)。")


    results_output_list = None 
    try:
        common_optimize_params = {
            "evaluate_func": evaluate_strategy_parameters,
            "optimization_setting": optimization_setting,
            "key_func": get_optimization_target_value,
            "max_workers": actual_max_workers, 
            "output": print 
        }

        if OPTIMIZATION_ALGORITHM.upper() == "GA":
            print(f"  遺傳算法參數: 種群大小={GA_POPULATION_SIZE}, 迭代次數={GA_NGEN_SIZE}")
            results_output_list = run_ga_optimization(
                **common_optimize_params,
                population_size=GA_POPULATION_SIZE,
                ngen_size=GA_NGEN_SIZE
            )
        elif OPTIMIZATION_ALGORITHM.upper() == "BF":
            print("  執行暴力窮舉優化...")
            results_output_list = run_bf_optimization(
                **common_optimize_params
            )
        else:
            print(f"錯誤：未知的優化算法 '{OPTIMIZATION_ALGORITHM}'。請選擇 'GA' 或 'BF'。")
            return

        print(f"\n{OPTIMIZATION_ALGORITHM} 優化完成。")

        if results_output_list:
            processed_results = []
            for item in results_output_list:
                params_dict = {}
                stats_dict = {}

                if isinstance(item, dict): 
                    stats_dict = item
                    for p_name in optimization_setting.params.keys():
                        if p_name in stats_dict:
                             params_dict[p_name] = stats_dict[p_name]
                elif isinstance(item, tuple) and len(item) >= 2: 
                    if isinstance(item[1], dict):
                        params_dict = item[1]
                    if len(item) >= 3 and isinstance(item[2], dict):
                        stats_dict = item[2]
                    elif isinstance(item[1], dict): 
                        if OPTIMIZATION_TARGET in item[1]:
                            stats_dict = item[1] 
                        else: 
                            params_dict = item[1]
                            stats_dict = item[1] 
                else: 
                    print(f"警告：結果項格式未知，已跳過: {item}")
                    continue
                
                row = {}
                row.update(params_dict) 
                row.update(stats_dict)  
                processed_results.append(row)

            if not processed_results:
                print("未能從優化結果中解析出有效數據行。")
                return

            results_df = pd.DataFrame(processed_results)
            
            print("優化結果 (前10條，按優化目標降序排列):")
            if OPTIMIZATION_TARGET not in results_df.columns:
                print(f"警告：優化目標 '{OPTIMIZATION_TARGET}' 不在結果DataFrame的列中。可用列: {results_df.columns.tolist()}")
                print("將打印原始結果的前10條（如果DataFrame非空）。")
                if not results_df.empty: print(results_df.head(10))
                sorted_results = results_df 
            else:
                results_df[OPTIMIZATION_TARGET] = pd.to_numeric(results_df[OPTIMIZATION_TARGET], errors='coerce')
                results_df.dropna(subset=[OPTIMIZATION_TARGET], inplace=True) 
                if not results_df.empty:
                    sorted_results = results_df.sort_values(by=OPTIMIZATION_TARGET, ascending=False)
                    print(sorted_results.head(10))
                else:
                    print("在轉換優化目標為數值後，沒有有效的結果數據。")
                    sorted_results = results_df

            if not sorted_results.empty:
                result_filename = (
                    f"{STRATEGY_CLASS.__name__}_{OPTIMIZATION_ALGORITHM}_results_"
                    f"{VT_SYMBOL.replace('.', '_')}_{INTERVAL.value}_"
                    f"{START_DATE.strftime('%Y%m%d')}_{END_DATE.strftime('%Y%m%d')}.csv"
                )
                sorted_results.to_csv(result_filename, index=False) 
                print(f"\n完整優化結果已保存到文件: {os.path.abspath(result_filename)}")
            else:
                print("沒有可保存的優化結果。")
        else:
            print("優化未返回任何結果，或結果為空。請檢查相關配置和數據。")

    except Exception as e:
        print(f"參數優化過程中發生嚴重錯誤: {e}")
        traceback.print_exc()

    finally:
        print("\n[步驟 6] 清理並關閉 VnPy 主引擎...")
        try:
            main_engine.close()
            print("VnPy 主引擎已關閉。")
        except Exception as e_close:
            print(f"關閉主引擎時發生錯誤: {e_close}")

    print("\n======================================================================")
    print("                       優化腳本執行完畢。                             ")
    print("======================================================================")

if __name__ == "__main__":
    if sys.platform.startswith('win'):
        multiprocessing.set_start_method('spawn', force=True)

    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    print("正在檢查 VnPy 數據庫配置...")
    try:
        db_driver = SETTINGS.get("database.driver", "未配置")
        db_name = SETTINGS.get("database.database", "未配置")
        print(f"  VnPy SETTINGS 中的數據庫驅動: {db_driver}, 數據庫名稱/路徑: {db_name}")
        if db_driver == "sqlite" and not os.path.isabs(db_name):
            user_home = os.path.expanduser("~")
            sqlite_path = os.path.join(user_home, ".vntrader", db_name)
            print(f"  SQLite 數據庫預期路徑: {sqlite_path}")
            if not os.path.exists(sqlite_path):
                print(f"  警告：SQLite 數據庫文件 '{sqlite_path}' 可能不存在。")
        elif db_driver == "未配置":
            print("  警告：未在 VnPy SETTINGS 中找到數據庫配置。")

        db_instance = get_database()
        if db_instance:
            print(f"  成功獲取數據庫實例 (類型: {type(db_instance).__name__})。")
            if db_driver == "mongodb":
                try:
                    db_instance.client.list_database_names()
                    print("  MongoDB 連接測試成功 (可列出數據庫)。")
                except Exception as mongo_e:
                    print(f"  MongoDB 連接測試失敗: {mongo_e}")
        else:
            print("  未能獲取數據庫實例。")
    except Exception as db_init_e:
        print(f"  初始化或檢查數據庫配置時發生錯誤: {db_init_e}")

    main_optimization_script()
