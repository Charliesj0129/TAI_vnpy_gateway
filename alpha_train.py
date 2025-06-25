# coding: utf-8
# 台指期 (TXFR1.TAIFEX) 使用 VnPy Alpha 模塊進行模型訓練與優化的完整腳本 (支持多模型)

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)  # 過濾Alphalens的warning
warnings.filterwarnings("ignore", category=UserWarning) # 過濾一些其他可能的UserWarning

import polars as pl
from vnpy.trader.constant import Interval, Exchange
from vnpy.alpha import AlphaLab, Segment # 使用者指定的 Segment 導入
from vnpy.trader.object import BarData
from datetime import datetime,timedelta

from vnpy.alpha.dataset import AlphaDataset # AlphaDataset 仍然需要，因為 Alpha158 繼承自它
from functools import partial
from vnpy.alpha.dataset.processor import process_drop_na, process_fill_na # 使用者指定的 processor 路徑

from vnpy.alpha.model.models.lasso_model import LassoModel
from vnpy.alpha.model.models.lgb_model import LgbModel
from vnpy.alpha.model.models.mlp_model import MlpModel
from vnpy.alpha.model import AlphaModel # AlphaModel 基類
import numpy as np
import itertools # 用於生成參數組合
from sklearn.metrics import mean_squared_error # 用於計算RMSE
from aenum import extend_enum # 用戶添加的 import
from vnpy.alpha.dataset.datasets.alpha_158 import Alpha158 # 導入 Alpha158 類

import joblib # 用於保存模型
import json # 用於保存結果
import os # 用於路徑操作


# 引入繪圖相關庫 (如果需要繪製特徵重要性)
try:
    import matplotlib.pyplot as plt
    import pandas as pd
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False
    print("未找到 Matplotlib 或 Pandas，特徵重要性圖表將無法顯示。")

# 確保VnPy的數據庫已正確配置
def main():
    print("開始執行 VnPy Alpha 模塊台指期多模型訓練與優化腳本...")
    if not hasattr(Exchange, "TAIFEX"): # 用戶添加的 Exchange 枚舉擴展
        extend_enum(Exchange, "TAIFEX", "TAIFEX")
        print("已動態添加 Exchange.TAIFEX")

    # 步驟一：數據獲取與 AlphaLab 初始化
    # ----------------------------------------------------------------------
    print("\n[步驟一：數據獲取與 AlphaLab 初始化]")
    
    output_folder = "./alpha_model_outputs" # 定義統一的輸出文件夾
    os.makedirs(output_folder, exist_ok=True) # 創建輸出文件夾
    lab_path = os.path.join(output_folder, "lab_data") # AlphaLab 數據路徑

    lab: AlphaLab = AlphaLab(lab_path=lab_path)
    print(f"AlphaLab 初始化完成，數據將存儲在: {lab.lab_path}") 

    name: str = "taifex_multi_model_training_run"
    vt_symbol: str = "TXFR1.TAIFEX"
    symbol, exchange_str = vt_symbol.split(".")
    exchange: Exchange = Exchange(exchange_str)
    
    start_date_str: str = "2017-06-01" 
    end_date_str: str = "2025-05-01"  
    interval: Interval = Interval.MINUTE 
    extended_days: int = 200 
    print(f"任務參數設定：合約={vt_symbol}, 區間=[{start_date_str}, {end_date_str}], 頻率={interval.value}")

    df: pl.DataFrame | None = None
    try:
        print(f"嘗試使用 AlphaLab 從其管理的 Parquet 文件加載 {vt_symbol} K線數據...")
        df = lab.load_bar_df(
            vt_symbols=[vt_symbol], 
            interval=interval,
            start=start_date_str,
            end=end_date_str,
            extended_days=extended_days
        )

        if df is None or df.is_empty(): raise ValueError("AlphaLab 加載數據失敗 (可能未找到對應的Parquet文件)。")
        print("AlphaLab 數據加載成功。")
    except Exception as e:
        print(f"使用 lab.load_bar_df 加載數據失敗: {e}")
        print("將嘗試手動從SQLite加載數據...")
        try:
            from vnpy.trader.database import get_database
            db_manager = get_database() 
            
            start_dt, end_dt = datetime.strptime(start_date_str, "%Y-%m-%d"), datetime.strptime(end_date_str, "%Y-%m-%d")
            print(f"手動加載: symbol={symbol}, exchange={exchange.value}, interval={interval.value}, start={start_dt}, end={end_dt}")
            bars_data: list[BarData] = db_manager.load_bar_data(symbol=symbol, exchange=exchange, interval=interval, start=start_dt, end=end_dt)
            
            if not bars_data:
                print("未能從SQLite數據庫手動加載到任何數據。請檢查配置和數據。")
                return
            bar_dicts = [{"datetime": b.datetime, "open": b.open_price, "high": b.high_price, "low": b.low_price,
                          "close": b.close_price, "volume": b.volume, "turnover": b.turnover,
                          "open_interest": b.open_interest, "vt_symbol": b.vt_symbol} for b in bars_data]
            df = pl.DataFrame(bar_dicts)

            if "turnover" not in df.columns or df["turnover"].null_count() == df.height:
                 df = df.with_columns((pl.col("volume") * pl.col("close")).fill_null(0).alias("turnover"))
            if "open_interest" not in df.columns or df["open_interest"].null_count() == df.height:
                df = df.with_columns(pl.lit(0.0).alias("open_interest").fill_null(0))
            
            if "volume" in df.columns and "turnover" in df.columns:
                df = df.with_columns(
                    (pl.col("turnover") / pl.when(pl.col("volume") != 0).then(pl.col("volume")).otherwise(None))
                    .fill_null(0) 
                    .cast(pl.Float32)
                    .alias("vwap")
                )
            elif "vwap" not in df.columns: 
                print("警告: 無法計算 vwap。將嘗試以 close 填充 vwap。")
                if "close" in df.columns:
                    df = df.with_columns(pl.col("close").cast(pl.Float32).alias("vwap"))
                else:
                    print("錯誤: 也無法使用 close 價格填充 vwap。")
                    return 
            
            required_cols = ["datetime", "open", "high", "low", "close", "volume", "turnover", "open_interest", "vwap", "vt_symbol"]
            df = df.select([col for col in required_cols if col in df.columns]) 
            
            if df.select(pl.col("datetime")).dtypes[0] != pl.Datetime:
                df = df.with_columns(pl.col("datetime").str.to_datetime()) 
            print("手動數據加載成功。")
        except ImportError as ie:
            print(f"手動從SQLite加載數據時發生導入錯誤: {ie}")
            return
        except Exception as manual_load_e:
            print(f"手動從SQLite加載數據也失敗: {manual_load_e}")
            return
            
    if df is None or df.is_empty():
        print("數據加載最終失敗，程序無法繼續。")
        return
    print(f"加載的K線數據 (DataFrame shape): {df.shape}\n數據預覽 (前5行):\n{df.head()}")

    # 步驟二：因子特徵工程與 AlphaDataset 構建
    print("\n[步驟二：因子特徵工程與 AlphaDataset 構建]")
    class TaifexDataset(Alpha158):
        def __init__(self, df_input: pl.DataFrame, train_period: tuple[str, str] | str,
                     valid_period: tuple[str, str] | str, test_period: tuple[str, str] | str):
            super().__init__(df_input, train_period, valid_period, test_period)
            print("TaifexDataset (基於 Alpha158): 父類 Alpha158 初始化完成，158因子已自動添加。")
            if "open_interest" in df_input.columns and df_input.get_column("open_interest").sum() != 0:
                self.add_feature("OI_CHANGE5_custom", "open_interest / ts_delay(open_interest, 5) - 1"); print("已添加因子: OI_CHANGE5_custom (額外自定義)")
            else:
                print("未添加 OI_CHANGE5_custom 因子，原因：'open_interest' 列不存在或數據無效。")
            self.set_label("ts_delay(close, -1) / close - 1") 
            print("已設定/覆蓋標籤 (名稱為 'label')") 
            print("TaifexDataset: 額外因子和標籤設定完成。")

    train_start, train_end = "2017-06-01", "2023-12-31" 
    valid_start, valid_end = "2024-01-01", "2024-12-31"
    test_start, test_end = "2025-01-01", "2025-05-01" 
    
    min_data_date_raw = df["datetime"].min()
    max_data_date_raw = df["datetime"].max()
    min_data_date = min_data_date_raw.replace(tzinfo=None) if min_data_date_raw is not None else None
    max_data_date = max_data_date_raw.replace(tzinfo=None) if max_data_date_raw is not None else None

    print(f"數據實際範圍 (naive for comparison): {min_data_date} to {max_data_date}")
    print(f"訓練集設定: {train_start} to {train_end}")
    print(f"驗證集設定: {valid_start} to {valid_end}")
    print(f"測試集設定: {test_start} to {test_end}")

    train_start_dt = datetime.strptime(train_start, "%Y-%m-%d")
    if min_data_date is not None and train_start_dt < min_data_date : 
        print(f"警告: 訓練開始日期 {train_start} 早於數據實際開始日期 {min_data_date.strftime('%Y-%m-%d')}。將使用數據實際開始日期。")
        train_start = min_data_date.strftime("%Y-%m-%d")
    
    test_end_dt = datetime.strptime(test_end, "%Y-%m-%d")
    if max_data_date is not None and test_end_dt > max_data_date:
        print(f"警告: 測試結束日期 {test_end} 晚於數據實際結束日期 {max_data_date.strftime('%Y-%m-%d')}。將使用數據實際結束日期。")
        test_end = max_data_date.strftime("%Y-%m-%d")
        current_valid_end_dt = datetime.strptime(valid_end, "%Y-%m-%d")
        current_test_start_dt = datetime.strptime(test_start, "%Y-%m-%d") 
        if current_valid_end_dt >= current_test_start_dt:
            new_valid_end_dt = current_test_start_dt - timedelta(days=1)
            valid_end = new_valid_end_dt.strftime("%Y-%m-%d")
            print(f"警告: 驗證集結束日期調整為 {valid_end} 以避免與測試集重疊。")
            current_valid_start_dt = datetime.strptime(valid_start, "%Y-%m-%d")
            if current_valid_start_dt > new_valid_end_dt:
                valid_start = valid_end 
                print(f"警告: 驗證集調整後起始日期晚於結束日期，已將驗證集設為單日 {valid_start}")

    if "vt_symbol" not in df.columns:
        df = df.with_columns(pl.lit(vt_symbol).alias("vt_symbol"))
        
    df = df.with_columns(
    pl.col("datetime").dt.replace_time_zone(None).alias("datetime")
    )
    print("已將 datetime 欄位移除時區標記，改為 tz-naive。✅")

    # 接著排序
    df_sorted = df.sort(["vt_symbol", "datetime"])
    print(f"排序後（naive datetime）：{df_sorted.head()}")
    print("創建 TaifexDataset 實例...")
    dataset: AlphaDataset = TaifexDataset(
        df_input=df_sorted, train_period=(train_start, train_end),
        valid_period=(valid_start, valid_end), test_period=(test_start, test_end)
    )
    print("TaifexDataset 實例創建完成。")

    print("開始數據預處理...")
    dataset.add_processor("learn", partial(process_drop_na, names=["label"]))
    print("已添加學習數據預處理: process_drop_na for 'label'")
    
    # 獲取已註冊的特徵名稱列表 (在 prepare_data 之前)
    registered_feature_names = list(dataset.feature_expressions.keys())
    # 手動添加自定義因子（如果它沒有被 feature_expressions 捕獲且確實被 add_feature 添加了）
    # Alpha158 的因子已經在 feature_expressions 中
    if "OI_CHANGE5_custom" not in registered_feature_names and \
       "open_interest" in df.columns and df.get_column("open_interest").sum() != 0:
        # 正常情況下，如果 add_feature("OI_CHANGE5_custom", ...) 被調用，它應該在 feature_expressions 中
        # 但為了保險起見，如果它不在，但我們知道它被添加了，可以考慮加入
        # 不過，AlphaDataset 的 add_feature 應該會處理 feature_expressions
        pass


    if registered_feature_names:
        dataset.add_processor(
    "infer",
    partial(process_fill_na, fill_value=0.0, fill_label=False)
)
        print(f"已添加推理數據預處理: process_fill_na for registered features ({len(registered_feature_names)}個): {registered_feature_names[:5]}...") 
    else:
        print("警告: 未找到已註冊的特徵名進行 process_fill_na 處理。")

    print("正在調用 dataset.prepare_data() 來計算因子並生成內部數據幀 (max_workers=1)...")
    try:
        dataset.prepare_data(max_workers=12) 
        print("dataset.prepare_data() 執行完成。")
    except Exception as e_prepare:
        print(f"dataset.prepare_data() 執行時發生錯誤: {e_prepare}")
        import traceback
        traceback.print_exc()
        print("由於 prepare_data 失敗，腳本將終止。")
        return 

    print("數據預處理步驟添加完成。")
    # ------------ 自動推算 input_size（特徵維度） ------------
    # 透過 fetch_learn 拿到已完成 preprocess 的訓練集 DataFrame
    df_train = dataset.fetch_learn(Segment.TRAIN)
    all_cols = df_train.columns
    # 假設欄位為 ["datetime", "vt_symbol", feat_1, feat_2, …, feat_N, "label"]
    input_size = len(all_cols) - 3

    # 步驟三：模型選擇、參數優化、訓練與預測
    print("\n[步驟三：模型選擇、參數優化、訓練與預測]")
    param_grids = {
        "Lasso": {
            "alpha": [1e-7, 1e-6,  5, 10],
            "max_iter": [500, 1000, 2000,],
            "random_state": [None],
        },
        "LightGBM": {
            "learning_rate": [0.001, 0.005],
            "num_leaves": [ 255, 511, 1023],
            "num_boost_round": [300, 500],
            "early_stopping_rounds": [100],
            "log_evaluation_period": [20],
            "seed": [None],
        },
        "MLP": {
            "input_size": [input_size],
            "hidden_sizes": [(128,), (256,), (256,128), (512,256)],
            "lr": [1e-5, 1e-4,1e-3],
            "n_epochs": [ 500,1000],
            "batch_size": [  20000, 30000],
            "early_stop_rounds": [10, 100],
            "eval_steps": [5,  50],
            "optimizer": ["adam"],
            "weight_decay": [0.0, 0.0001, 0.001],
            "device": ["cpu"],
            "seed": [None],
        }
    }

    best_models_results = {}
    all_models_performance = []  # 用於存儲所有模型的結果

    for model_name, grid in param_grids.items():
        print(f"\nOptimizing model: {model_name}")
        best_score = float("inf")
        best_params = None
        best_model_instance_for_type = None

        # 生成所有參數組合
        keys, values = zip(*grid.items())
        param_combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
        if not param_combinations and grid:
            param_combinations = [{}]
        elif not grid:
            param_combinations = [{}]

        for params_combo in param_combinations:
            print(f"  Training {model_name} with params: {params_combo}")
            model_instance: AlphaModel | None = None

            try:
                if model_name == "Lasso":
                    # LassoModel 只接受 alpha, max_iter, random_state
                    model_instance = LassoModel(**params_combo)

                elif model_name == "LightGBM":
                    # LgbModel 只接受 learning_rate, num_leaves,
                    # num_boost_round, early_stopping_rounds,
                    # log_evaluation_period, seed
                    model_instance = LgbModel(**params_combo)

                elif model_name == "MLP":
                    # MlpModel 只接受 input_size, hidden_sizes, lr,
                    # n_epochs, batch_size, early_stop_rounds,
                    # eval_steps, optimizer, weight_decay, device, seed
                    model_instance = MlpModel(**params_combo)

                if model_instance is None:
                    continue

                # 開始訓練
                model_instance.fit(dataset)

                # 在驗證集做預測
                pred_valid = model_instance.predict(dataset, Segment.VALID)
                actual_valid_labels_df = dataset.fetch_infer(Segment.VALID)

                if (
                    actual_valid_labels_df is None
                    or "label" not in actual_valid_labels_df.columns
                    or actual_valid_labels_df.is_empty()
                ):
                    print(f"    警告: 無法取得 {model_name} 的驗證集標籤，跳過此組參數。")
                    continue

                actual_valid_labels = actual_valid_labels_df["label"].to_numpy()
                valid_mask = (
                    ~np.isnan(pred_valid)
                    & ~np.isinf(pred_valid)
                    & ~np.isnan(actual_valid_labels)
                    & ~np.isinf(actual_valid_labels)
                )
                if np.sum(valid_mask) < 2:
                    current_score = float("inf")
                    print(f"    警告: {model_name} 此組參數有效資料點不足 (<2)，RMSE 計算略過。")
                else:
                    pred_valid_cleaned = pred_valid[valid_mask]
                    actual_valid_labels_cleaned = actual_valid_labels[valid_mask]
                    current_score = np.sqrt(
                        mean_squared_error(actual_valid_labels_cleaned, pred_valid_cleaned)
                    )
                    print(f"    Validation RMSE: {current_score:.6f}")

                # 更新最佳模型
                if current_score < best_score:
                    best_score = current_score
                    best_params = params_combo
                    best_model_instance_for_type = model_instance

            except Exception as e:
                print(f"    訓練/評估 {model_name} (參數 {params_combo}) 發生錯誤: {e}")
                import traceback; traceback.print_exc()

        # 檢查是否找到最佳模型，若有則在測試集上評估並保存
        if best_model_instance_for_type:
            print(
                f"\n  Best for {model_name}: Params={best_params}, Validation RMSE={best_score:.6f}"
            )
            test_rmse_val = float("inf")
            test_preds_head_val = None

            try:
                pred_test_best = best_model_instance_for_type.predict(dataset, Segment.TEST)
                actual_test_labels_df = dataset.fetch_raw(Segment.TEST)

                if (
                    actual_test_labels_df is not None
                    and "label" in actual_test_labels_df.columns
                    and not actual_test_labels_df.is_empty()
                ):
                    actual_test_labels_test = actual_test_labels_df["label"].to_numpy()
                    test_mask = (
                        ~np.isnan(pred_test_best)
                        & ~np.isinf(pred_test_best)
                        & ~np.isnan(actual_test_labels_test)
                        & ~np.isinf(actual_test_labels_test)
                    )
                    if np.sum(test_mask) > 1:
                        pred_test_best_cleaned = pred_test_best[test_mask]
                        actual_test_labels_test_cleaned = actual_test_labels_test[test_mask]
                        test_rmse_val = np.sqrt(
                            mean_squared_error(
                                actual_test_labels_test_cleaned, pred_test_best_cleaned
                            )
                        )
                        test_preds_head_val = pred_test_best_cleaned[:5].tolist()
                        print(f"    {model_name} (Best Params) - Test RMSE: {test_rmse_val:.6f}")
                        print(
                            f"    {model_name} (Best Params) - Test Predictions (first 5): {test_preds_head_val}"
                        )
                    else:
                        print(f"    {model_name} (Best Params) - 測試集有效資料點不足 (<2) 計算 RMSE。")
                else:
                    print(
                        f"    {model_name} (Best Params) - 無法取得測試集標籤，只列出原始預測頭部：{pred_test_best[:5]}"
                    )

                model_info_to_save = {
                    "best_params": best_params,
                    "validation_rmse": best_score,
                    "test_rmse": test_rmse_val,
                    "test_predictions_head": test_preds_head_val,
                }
                best_models_results[model_name] = model_info_to_save
                all_models_performance.append(
                    {
                        "model_type": model_name,
                        **model_info_to_save,
                    }
                )

                # 保存最佳模型實例
                model_save_path = os.path.join(
                    output_folder, f"best_{model_name.lower()}_model.joblib"
                )
                joblib.dump(best_model_instance_for_type, model_save_path)
                print(f"    已保存最佳 {model_name} 模型到: {model_save_path}")

            except Exception as e:
                print(f"    使用最佳 {model_name} 於測試集預測或保存時出錯: {e}")
        else:
            print(f"  未能找到 {model_name} 的最佳模型。")
            all_models_performance.append(
                {
                    "model_type": model_name,
                    "best_params": None,
                    "validation_rmse": float("inf"),
                    "test_rmse": float("inf"),
                    "test_predictions_head": None,
                }
            )


    print("\n--- 最終各模型最佳訓練結果 ---")
    for result_item in all_models_performance:
        model_name_key = result_item["model_type"]
        print(f"模型: {model_name_key}")
        if result_item['best_params'] is not None:
            print(f"  最佳參數: {result_item['best_params']}")
            print(f"  最佳驗證集 RMSE: {result_item['validation_rmse']:.6f}")
            print(f"  使用最佳參數的測試集 RMSE: {result_item['test_rmse']:.6f}")
            print(f"  測試集預測頭部 (前5): {result_item['test_predictions_head']}")
            
            if model_name_key == "LightGBM" and MATPLOTLIB_AVAILABLE:
                # 從 best_models_results 中獲取保存的實例進行繪圖
                lgb_model_instance_for_plot = None
                if model_name_key in best_models_results and "model_instance" in best_models_results[model_name_key]:
                     lgb_model_instance_for_plot = best_models_results[model_name_key]["model_instance"]

                if lgb_model_instance_for_plot and hasattr(lgb_model_instance_for_plot, 'model') and lgb_model_instance_for_plot.model and \
                   hasattr(lgb_model_instance_for_plot, 'feature_names_') and lgb_model_instance_for_plot.feature_names_:
                    print(f"  繪製 {model_name_key} 特徵重要性圖表...")
                    try:
                        lgb_feature_names = list(lgb_model_instance_for_plot.feature_names_)
                        feature_importances = pd.Series(lgb_model_instance_for_plot.model.feature_importances_, index=lgb_feature_names)
                        
                        plt.figure(figsize=(10, 8))
                        feature_importances.nlargest(20).plot(kind='barh')
                        plt.title(f"{model_name_key} Feature Importances (Best Model)")
                        plt.xlabel("Importance"); plt.ylabel("Feature"); plt.tight_layout()
                        chart_save_path = os.path.join(output_folder, f"feature_importance_{model_name_key.lower()}.png")
                        plt.savefig(chart_save_path)
                        plt.close() # 關閉圖形，避免在無UI環境下彈出
                        print(f"  特徵重要性圖表已保存到: {chart_save_path}")
                        print(f"  {model_name_key} 特徵重要性 (降序):\n{feature_importances.sort_values(ascending=False).head(10)}") # 只打印前10
                    except Exception as plot_exc:
                        print(f"  繪製 {model_name_key} 特徵重要性圖表時出錯: {plot_exc}")
                elif not MATPLOTLIB_AVAILABLE:
                     print(f"  Matplotlib 不可用，跳過 {model_name_key} 特徵重要性圖表。")
        else:
            print(f"  未能成功訓練或找到 {model_name_key} 的最佳參數。")
    
    # 保存所有模型的最佳結果到JSON文件
    results_summary_path = os.path.join(output_folder, "all_models_best_results_summary.json")
    # 從 all_models_performance 中移除 model_instance 以便 JSON 序列化
    serializable_results = []
    for item in all_models_performance:
        item_copy = item.copy()
        item_copy.pop("model_instance", None) # 移除 model_instance 鍵
        serializable_results.append(item_copy)

    with open(results_summary_path, "w", encoding="utf-8") as f:
        json.dump(serializable_results, f, indent=4, ensure_ascii=False)
    print(f"\n所有模型的最佳結果摘要已保存到: {results_summary_path}")

    
    print("\n[步驟四：信號生成與解讀 (概念)]")
    print("模型的原始預測值是連續的數值，可解釋為對未來標籤的預期。")
    print("可設定閾值將預測分數轉換為交易信號。")

    print("\n[步驟五：交易策略的實現 (基於 vnpy.app.cta_strategy) (概念)]")
    print("1. 從保存的 .joblib 文件中加載最佳模型。")
    print("2. 創建 CTA 策略 (vnpy.app.cta_strategy)。")
    print("3. 在 CTA 策略的 on_init 中加載模型。")
    print("4. 在 on_bar 中計算與訓練時一致的特徵，用模型預測，生成交易指令。")

    print("\n[步驟六：回測、性能分析與迭代優化 (概念)]")
    print("1. Alphalens 初步分析 (對單一期貨適用性有限)。")
    print("2. **CTA 回測引擎深度回測**：評估期貨模型策略的關鍵。")
    print("3. 根據 CTA 回測結果，迭代優化。")

    print("\n腳本執行完畢。")

if __name__ == "__main__":
    main()
