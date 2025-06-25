# -*- coding: utf-8 -*-
"""
VnPy CTA 策略儀表板主程式 (Ultimate Edition)
================================================
- 作者: Gemini
- 版本: 3.0
- 功能:
  1. 實現三種操作模式: 'backtest', 'optimize', 'walk_forward'。
  2. 完全由 `config.yaml` 驅動，實現設定與邏輯分離。
  3. 整合 QuantStats，一鍵生成專業級 HTML 視覺化分析報告。
  4. 實現滾動窗口前向分析 (Walk-Forward Optimization)，提供最嚴格的策略驗證。
  5. 自動儲存詳細的逐筆交易紀錄。
  6. 沿用 rich 和 tqdm 提供優異的命令列使用者體驗。
"""
import multiprocessing
import sys
import os
import yaml
import pandas as pd
import numpy as np
import quantstats as qs
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
import math 
import csv
from filelock import FileLock

# --- VnPy Core Imports ---
from vnpy.event import EventEngine
from vnpy.trader.engine import MainEngine
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData, TradeData

# --- CTA Strategy Imports ---
from vnpy_ctastrategy import CtaStrategyApp
from vnpy_ctastrategy.backtesting import OptimizationSetting, BacktestingEngine

# --- UI/UX Imports ---
from rich.console import Console
from rich.table import Table
from tqdm import tqdm

# --- 策略類別註冊 ---

from vnpy_ctastrategy.strategies.multi_signal_strategy import MultiSignalStrategy
from vnpy_ctastrategy.strategies.alpha import AlphaModelStrategy
from vnpy_ctastrategy.strategies.atr_rsi_strategy import AtrRsiStrategy
from vnpy_ctastrategy.strategies.boll_channel_strategy import BollChannelStrategy
from vnpy_ctastrategy.strategies.CTrendMaStrategy import CTrendMaStrategy
from vnpy_ctastrategy.strategies.DmiTrendStrategy import DmiTrendStrategy
from vnpy_ctastrategy.strategies.PagodaStrategy import BasicPagodaStrategy
from vnpy_ctastrategy.strategies.GaussianAddonStrategy import GaussianAddonStrategy
from vnpy_ctastrategy.strategies.DualSmaPullbackStrategy import DualSmaPullbackStrategy
from vnpy_ctastrategy.strategies.FibProxyPullbackStrategy import FibProxyPullbackStrategy
from vnpy_ctastrategy.strategies.VegasTripleConfirmationStrategy import VegasTripleConfirmationStrategy
from vnpy_ctastrategy.strategies.MjIndicatorStrategy import MjIndicatorStrategy
from vnpy_ctastrategy.strategies.king_keltner_strategy import KingKeltnerStrategy
from vnpy_ctastrategy.strategies.MacdVolumeFilterStrategy import MacdVolumeFilterStrategy
from vnpy_ctastrategy.strategies.TrendMomentumChannelStrategy import TrendMomentumChannelStrategy
# --- 註冊所有策略類別 ---

STRATEGY_CLASS_MAP = {
    "MultiSignalStrategy": MultiSignalStrategy,
    "AlphaModelStrategy": AlphaModelStrategy,
    "AtrRsiStrategy": AtrRsiStrategy,
    "BollChannelStrategy": BollChannelStrategy,
    "CTrendMaStrategy": CTrendMaStrategy,
    "DmiTrendStrategy": DmiTrendStrategy,
    "BasicPagodaStrategy": BasicPagodaStrategy,
    "GaussianAddonStrategy": GaussianAddonStrategy,
    "DualSmaPullbackStrategy": DualSmaPullbackStrategy,
    "FibProxyPullbackStrategy": FibProxyPullbackStrategy,
    "VegasTripleConfirmationStrategy": VegasTripleConfirmationStrategy,
    "MjIndicatorStrategy": MjIndicatorStrategy,
    "KingKeltnerStrategy": KingKeltnerStrategy,
    "MacdVolumeFilterStrategy": MacdVolumeFilterStrategy,
    "TrendMomentumChannelStrategy": TrendMomentumChannelStrategy,
    # 可以在這裡添加更多策略類別
}

# --- 枚舉擴展 (aenum) ---
try:
    from aenum import extend_enum
    def extend_exchange_enum_if_needed():
        custom_exchanges = {"TAIFEX": "臺灣期貨交易所"}
        if not hasattr(Exchange, "TAIFEX"):
            extend_enum(Exchange, "TAIFEX", "TAIFEX")
except ImportError:
    print("錯誤: 請安裝 aenum 函式庫 (pip install aenum)")
    sys.exit(1)

# --- 全域物件 ---
console = Console(width=140)
def get_target_value(result: tuple) -> float:
    """
    頂層輔助函式，用於從優化結果中提取目標值。
    取代了無法被序列化的 lambda 函式。
    """
    if isinstance(result, tuple) and len(result) > 1:
        return result[1]
    return -np.inf
def run_evaluation_for_worker(strategy_setting: dict, config: dict) -> Tuple:
    """
    這是在每個獨立的子進程中執行的函式。
    它接收純粹的設定檔，並在內部獨立創建所有必要的物件。
    (v5.5 版：新增即時結果寫入功能)
    """
    # 擴展枚舉
    extend_exchange_enum_if_needed()

    # 1. 從傳入的 config 字典創建 BacktestingEngine
    cfg_engine = config['engine']
    cfg_session = config['session']
    engine = BacktestingEngine()
    engine.set_parameters(
        vt_symbol=cfg_session['vt_symbol'],
        interval=Interval(cfg_session['interval']),
        start=datetime.strptime(cfg_session['start_date'], '%Y-%m-%d'),
        end=datetime.strptime(cfg_session['end_date'], '%Y-%m-%d'),
        rate=cfg_engine['rate'],
        slippage=cfg_engine['slippage'],
        size=cfg_engine['size'],
        pricetick=cfg_engine['pricetick'],
        capital=cfg_engine['capital']
    )
    
    # 2. 執行回測
    strategy_class = STRATEGY_CLASS_MAP[config['strategy']['name']]
    engine.add_strategy(strategy_class, strategy_setting)
    
    engine.load_data()
    engine.run_backtesting()
    engine.calculate_result()
    statistics = engine.calculate_statistics(output=False)
    
    # 3. 返回結果給優化器
    target_name = config['optimization']['target']
    target_value = statistics.get(target_name, -np.inf)

    output_folder = config["reporting"]["output_folder"]
    live_results_path = os.path.join(output_folder, "optimization_live_results.csv")
    lock_path = os.path.join(output_folder, "live_results.lock")

    # --- 核心新增：在寫入檔案前，確保輸出資料夾一定存在 ---
    # os.makedirs 會創建所有不存在的父目錄， exist_ok=True 表示如果資料夾已存在，也不會報錯。
    os.makedirs(output_folder, exist_ok=True)

    # 合併參數和結果
    result_row = {**strategy_setting, **statistics}

    # 使用檔案鎖，安全地追加寫入 CSV，防止多進程衝突
    lock = FileLock(lock_path)
    with lock:
        # 檢查檔案是否存在以決定是否寫入標頭
        file_exists = os.path.exists(live_results_path)
        
        with open(live_results_path, "a", newline="", encoding='utf-8-sig') as f:
            fieldnames = result_row.keys()
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            
            if not file_exists or os.path.getsize(live_results_path) == 0:
                writer.writeheader()
            
            writer.writerow(result_row)

    return strategy_setting, target_value, statistics
class UltimateBacktester:
    """終極回測器，整合多種分析模式與專業報告。"""

    def __init__(self, config_path="config.yaml"):
        self.config = self._load_config(config_path)
        self.output_folder = self.config["reporting"]["output_folder"]
        os.makedirs(self.output_folder, exist_ok=True)
        self.main_engine = None
        self.event_engine = None

    def _load_config(self, path: str) -> Dict:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            console.print(f"[bold red]錯誤：設定檔 '{path}' 不存在！[/bold red]")
            sys.exit(1)

    def run(self):
        """根據設定的模式，執行總任務。"""
        self._print_header()
        self._setup_engines()
        
        mode = self.config.get("mode", "backtest")
        console.log(f"[bold yellow]當前執行模式: {mode.upper()}[/bold yellow]")

        if mode == "backtest":
            self.run_single_backtest()
        elif mode == "optimize":
            self.run_full_optimization()
        elif mode == "walk_forward":
            self.run_walk_forward_analysis()
        else:
            console.print(f"[bold red]錯誤：未知的模式 '{mode}'。[/bold red]")
            
        console.print("\n[bold green]所有流程執行完畢！[/bold green]")

    def _print_header(self):
        """打印格式化的設定摘要。"""
        cfg = self.config
        table = Table(title="[bold magenta]VnPy CTA 策略儀表板 (v5.0)[/bold magenta]")
        table.add_column("項目", style="cyan")
        table.add_column("設定值", style="green")

        table.add_row("執行模式", cfg['mode'].upper())
        table.add_row("策略名稱", cfg['strategy']['name'])
        table.add_row("交易標的", cfg['session']['vt_symbol'])
        table.add_row("回測區間", f"{cfg['session']['start_date']} to {cfg['session']['end_date']}")
        
        if cfg['mode'] in ['optimize', 'walk_forward']:
            auto_detect = cfg['optimization'].get('auto_detect_params', False)
            table.add_row("參數模式", "[bold green]自動探索與調整[/bold green]" if auto_detect else "[bold yellow]手動設定[/bold yellow]")
            if auto_detect:
                min_space = cfg['optimization'].get('target_space_min', 'N/A')
                max_space = cfg['optimization'].get('target_space_max', 'N/A')
                table.add_row("目標優化空間", f"{min_space:,} - {max_space:,} 組")

        console.print(table)

    def _setup_engines(self):
        """初始化 VnPy 主引擎。"""
        extend_exchange_enum_if_needed()
        self.event_engine = EventEngine()
        self.main_engine = MainEngine(self.event_engine)
        self.main_engine.add_app(CtaStrategyApp)

    def _get_optimization_setting(self) -> OptimizationSetting:
        """
        核心升級 v5.4：
        - 修正：為週期類參數生成的最小值從 1 提升至 2，以符合 talib 等函式庫要求。
        """
        cfg_opt = self.config['optimization']
        setting = OptimizationSetting()
        setting.set_target(cfg_opt['target'])

        use_auto_detect = cfg_opt.get("auto_detect_params", False)

        if not use_auto_detect:
            # ... (手動設定部分的邏輯不變) ...
            console.log("[yellow]使用手動設定的參數範圍...[/yellow]")
            for name, params in cfg_opt['param_space'].items():
                setting.add_parameter(name, params[0], params[1], params[2])
            return setting

        # --- 自動探索與智慧調整邏輯 ---
        console.log("[cyan]啟用參數自動探索與智慧空間調整模式...[/cyan]")
        strategy_class = STRATEGY_CLASS_MAP[self.config['strategy']['name']]
        param_names = getattr(strategy_class, "parameters", [])
        
        param_configs = {}
        skipped_params = [] 

        for name in param_names:
            default_value = getattr(strategy_class, name, None)
            if default_value is None:
                continue
            
            if isinstance(default_value, bool):
                skipped_params.append((name, default_value))
                continue

            # 1. 生成初始的、較小的優化範圍
            is_int = isinstance(default_value, int)
            step = 1 if is_int else max(0.01, round(abs(default_value * 0.1), 2))
            
            # --- 核心修正：將最小起始值從 1 改為 2 ---
            # 檢查參數名是否和 'period', 'window' 等週期相關
            if any(p in name.lower() for p in ["period", "window", "length", "bar"]):
                start = max(2, default_value - 2 * step) # 確保週期參數 > 1
            else:
                start = default_value - 2 * step

            end = default_value + 2 * step
            
            param_configs[name] = {
                "start": start, "end": end, "step": step, 
                "default": default_value, "is_int": is_int
            }
        min_target = cfg_opt.get("target_space_min", 100000)
        max_target = cfg_opt.get("target_space_max", 1000000)
        iter_count = 0
        max_iters = 1000
        while iter_count < max_iters:
            total_combinations = 1
            if not param_configs: break 
            for p in param_configs.values():
                total_combinations *= math.floor((p['end'] - p['start']) / p['step']) + 1
            if total_combinations >= min_target: break
            param_to_expand = list(param_configs.keys())[iter_count % len(param_configs)]
            p_config = param_configs[param_to_expand]
            p_config['start'] -= p_config['step']
            p_config['end'] += p_config['step']
            iter_count += 1
        
        table = Table(title="[bold green]自動探索並調整後的最終優化範圍[/bold green]")
        table.add_column("參數名稱", style="cyan")
        table.add_column("策略預設值", style="yellow")
        table.add_column("最終優化範圍 [start, end, step]", style="magenta")
        final_combinations = 1
        for name, p_config in param_configs.items():
            start, end, step = p_config['start'], p_config['end'], p_config['step']
            if p_config['is_int']:
                # 再次確保週期參數的起始值和步長
                if any(p in name.lower() for p in ["period", "window", "length", "bar"]):
                    start = int(max(2, start))
                    step = int(max(1, step))
                else:
                    start, step = int(start), int(max(1, step))
                end = int(end)
            else:
                start, end, step = round(start, 4), round(end, 4), round(step, 4)
            
            setting.add_parameter(name, start, end, step)
            final_combinations *= math.floor((end - start) / step) + 1
            table.add_row(name, str(p_config['default']), f"[{start}, {end}, {step}]")

        console.print(table)
        if skipped_params:
            console.log("[yellow]注意：以下布林(開關)型參數已被自動忽略，將使用其預設值進行回測：[/yellow]")
            for name, value in skipped_params:
                console.log(f"  - [cyan]{name}[/cyan] = {value}")
        console.log(f"最終確定的優化空間總組合數: [bold green]{final_combinations:,.0f}[/bold green] (目標: {min_target:,} - {max_target:,})")
        
        return setting

    # --- 以下所有方法與 v4.0 版本完全相同, 為保持完整性全部提供 ---

    def run_single_backtest(self):
        console.log("[cyan]開始執行單次回測...[/cyan]")
        cfg_strategy = self.config['strategy']
        params = cfg_strategy.get('parameters', {})
        engine = self._create_backtesting_engine()
        engine.add_strategy(STRATEGY_CLASS_MAP[cfg_strategy['name']], params)
        self._run_engine_and_generate_reports(engine, "single_backtest")

    def run_full_optimization(self):
            """
            執行完整的參數優化流程。
            """
            console.log("[cyan]開始執行參數優化...[/cyan]")
            
            # 在開始前，刪除舊的即時結果檔案，確保每次都是全新的紀錄
            live_results_path = os.path.join(self.output_folder, "optimization_live_results.csv")
            if os.path.exists(live_results_path):
                os.remove(live_results_path)
                console.log(f"已刪除舊的即時結果檔案: [yellow]{live_results_path}[/yellow]")

            # 獲取優化設定（包含自動探索邏輯）
            optimization_setting = self._get_optimization_setting()
            
            # 執行核心優化過程
            results = self._run_optimization_process(optimization_setting)
            
            if not results:
                console.log("[yellow]優化未產生任何有意義的結果。[/yellow]")
                return
                
            # 處理並以表格顯示最終排序後的頂尖結果
            cfg_opt = self.config['optimization']
            processed_results = [dict(r[0], **r[2]) for r in results]
            results_df = pd.DataFrame(processed_results)
            results_df.sort_values(by=cfg_opt['target'], ascending=False, inplace=True)
            
            self._display_optimization_results(results_df)
            
            # 對找到的最佳參數組合，執行一次詳細的回測並生成專業報告
            console.log("\n[cyan]對最佳參數進行詳細回測並生成報告...[/cyan]")
            best_params = results_df.iloc[0].to_dict()
            
            # 從結果中篩選出策略參數部分
            best_strategy_params = {
                k: v for k, v in best_params.items() 
                if k in optimization_setting.params
            }
            
            engine = self._create_backtesting_engine()
            engine.add_strategy(
                STRATEGY_CLASS_MAP[self.config['strategy']['name']], 
                best_strategy_params
            )
            self._run_engine_and_generate_reports(engine, "optimization_best")

    def run_walk_forward_analysis(self):
        console.log("[cyan]開始執行前向分析...[/cyan]")
        cfg_session = self.config['session']
        cfg_wf = self.config['walk_forward']
        start_date = datetime.strptime(cfg_session['start_date'], '%Y-%m-%d')
        end_date = datetime.strptime(cfg_session['end_date'], '%Y-%m-%d')
        in_sample_delta = timedelta(days=cfg_wf['in_sample_days'])
        out_of_sample_delta = timedelta(days=cfg_wf['out_of_sample_days'])
        all_trades: List[TradeData] = []
        all_daily_dfs: List[pd.DataFrame] = []
        window_start = start_date
        while True:
            in_sample_end = window_start + in_sample_delta
            out_of_sample_start = in_sample_end
            out_of_sample_end = out_of_sample_start + out_of_sample_delta
            if out_of_sample_end > end_date:
                break
            console.rule(f"[bold]WF Window: In-Sample [{window_start.date()} -> {in_sample_end.date()}] | Out-of-Sample [{out_of_sample_start.date()} -> {out_of_sample_end.date()}][/bold]")
            opt_engine = self._create_backtesting_engine(start=window_start, end=in_sample_end)
            optimization_setting = self._get_optimization_setting()
            results = self._run_optimization_process(optimization_setting, engine_instance=opt_engine)
            if not results:
                console.log("[yellow]當前窗口優化無結果，跳至下一窗口。[/yellow]")
                window_start += out_of_sample_delta
                continue
            best_params = results[0][0]
            console.log(f"樣本內最佳參數: [cyan]{best_params}[/cyan]")
            validate_engine = self._create_backtesting_engine(start=out_of_sample_start, end=out_of_sample_end)
            validate_engine.add_strategy(STRATEGY_CLASS_MAP[self.config['strategy']['name']], best_params)
            validate_engine.load_data()
            validate_engine.run_backtesting()
            validate_engine.calculate_result()
            all_trades.extend(validate_engine.trades)
            all_daily_dfs.append(validate_engine.daily_df)
            window_start += out_of_sample_delta
        if not all_trades:
            console.log("[bold red]前向分析未產生任何交易，無法生成報告。[/bold red]")
            return
        console.rule("[bold green]前向分析完成，正在生成拼接報告...[/bold green]")
        final_engine = self._create_backtesting_engine()
        final_engine.trades = all_trades
        final_engine.daily_df = pd.concat(all_daily_dfs).sort_index()
        final_engine.calculate_result(from_trades=True)
        self._run_engine_and_generate_reports(final_engine, "walk_forward_result", from_trades=True)

    def _create_backtesting_engine(self, start: datetime = None, end: datetime = None) -> BacktestingEngine:
        cfg_engine = self.config['engine']
        cfg_session = self.config['session']
        engine = BacktestingEngine()
        engine.set_parameters(
            vt_symbol=cfg_session['vt_symbol'],
            interval=Interval(cfg_session['interval']),
            start=start or datetime.strptime(cfg_session['start_date'], '%Y-%m-%d'),
            end=end or datetime.strptime(cfg_session['end_date'], '%Y-%m-%d'),
            rate=cfg_engine['rate'],
            slippage=cfg_engine['slippage'],
            size=cfg_engine['size'],
            pricetick=cfg_engine['pricetick'],
            capital=cfg_engine['capital']
        )
        return engine
    
    def _evaluate_for_multiprocessing(self, strategy_setting: dict) -> Tuple:
            """
            一個獨立的、可被多進程序列化的評估方法。
            """
            # 該方法執行時，會使用暫存在 self 中的引擎和進度條
            engine = self.mp_engine
            result_tuple = self._evaluate_strategy_parameters(engine, strategy_setting)
            
            # 更新進度條
            if self.mp_pbar:
                self.mp_pbar.update(1)
                if result_tuple and len(result_tuple) > 1:
                    target_value = result_tuple[1]
                    target_name = self.config['optimization']['target']
                    self.mp_pbar.set_postfix({target_name: f"{target_value:.4f}"})
            
            return result_tuple

    def _run_optimization_process(self, setting: OptimizationSetting, engine_instance: BacktestingEngine = None) -> List:
        """
        執行優化過程並返回結果。
        (v5.4 最終修正版：使用頂層函式取代 lambda)
        """
        from vnpy.trader.optimize import run_ga_optimization, run_bf_optimization
        from functools import partial

        cfg_opt = self.config['optimization']
        evaluate_func = partial(run_evaluation_for_worker, config=self.config)

        console.log(f"開始執行 {cfg_opt['algorithm']} 優化，請稍候...")

        # 定義優化函數的通用參數
        common_params = {
            "evaluate_func": evaluate_func,
            "optimization_setting": setting,
            "key_func": get_target_value,  # <--- 核心修正：使用頂層函式取代 lambda
            "max_workers": cfg_opt['processes'],
        }

        # 根據算法執行優化
        if cfg_opt['algorithm'].upper() == "GA":
            results = run_ga_optimization(
                **common_params,
                population_size=cfg_opt['ga_settings']['population_size'],
                ngen_size=cfg_opt['ga_settings']['ngen_size']
            )
        else:
            results = run_bf_optimization(**common_params)
        
        console.log(f"[bold green]優化執行完成！[/bold green]")
        return results

    def _evaluate_strategy_parameters(self, engine: BacktestingEngine, strategy_setting: dict) -> Tuple:
        strategy_class = STRATEGY_CLASS_MAP[self.config['strategy']['name']]
        engine_for_eval = engine
        engine_for_eval.clear_data()
        engine_for_eval.add_strategy(strategy_class, strategy_setting)
        engine_for_eval.load_data()
        engine_for_eval.run_backtesting()
        engine_for_eval.calculate_result()
        statistics = engine_for_eval.calculate_statistics(output=False)
        target_name = self.config['optimization']['target']
        target_value = statistics.get(target_name, -np.inf)
        return strategy_setting, target_value, statistics

    def _run_engine_and_generate_reports(self, engine: BacktestingEngine, report_name: str, from_trades: bool = False):
        if not from_trades:
            engine.load_data()
            engine.run_backtesting()
            engine.calculate_result()
        statistics = engine.calculate_statistics(output=False)
        console.print(f"\n[bold underline]績效報告: {report_name}[/bold underline]")
        stats_df = pd.DataFrame([statistics]).T
        stats_df.columns = ["Value"]
        console.print(stats_df)
        cfg_report = self.config['reporting']
        if cfg_report.get('save_trades_log', False) and engine.trades:
            trades_df = pd.DataFrame([t.__dict__ for t in engine.trades])
            filename = os.path.join(self.output_folder, f"{report_name}_trades.csv")
            trades_df.to_csv(filename, index=False, encoding='utf-8-sig')
            console.log(f"交易紀錄已儲存至: [yellow]{filename}[/yellow]")
        if cfg_report.get('generate_quantstats_report', False) and not engine.daily_df.empty:
            returns_series = engine.daily_df['daily_return']
            filename = os.path.join(self.output_folder, f"{report_name}_quantstats_report.html")
            try:
                qs.reports.html(returns_series, output=filename, title=f"{self.config['strategy']['name']} - {report_name}")
                console.log(f"QuantStats 報告已生成: [yellow]{filename}[/yellow]")
            except Exception as e:
                console.log(f"[bold red]生成 QuantStats 報告失敗: {e}[/bold red]")

    def _display_optimization_results(self, results_df: pd.DataFrame):
        console.print("\n[bold green]優化結果 (前 N 筆):[/bold green]")
        top_n = self.config['reporting']['top_n_results']
        display_df = results_df.head(top_n).copy()
        
        # 取得參數名稱列表，這裡我們需要從 OptimizationSetting 中獲取
        # 由於此函數在優化後調用，我們可假設 setting 已被創建
        temp_setting = self._get_optimization_setting()
        param_names = list(temp_setting.params.keys())
        
        for col in display_df.columns:
            if display_df[col].dtype == 'float64':
                display_df[col] = display_df[col].map('{:,.4f}'.format)

        table = Table(title="優化結果摘要")
        for col in display_df.columns:
            style = "cyan" if col in param_names else "magenta"
            table.add_column(col, style=style, justify="right")
        
        for _, row in display_df.iterrows():
            table.add_row(*row.astype(str).tolist())
            
        console.print(table)


if __name__ == "__main__":
    if sys.platform.startswith('win'):
        multiprocessing.set_start_method('spawn', force=True)

    backtester = UltimateBacktester(config_path="config.yaml")
    backtester.run()







