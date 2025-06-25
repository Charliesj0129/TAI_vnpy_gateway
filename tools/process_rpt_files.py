import os
import re
import zipfile
import pandas as pd
from datetime import datetime
from io import StringIO
import sqlite3
from zoneinfo import ZoneInfo # Python 3.9+
import logging

# --- 0. 初始化日誌記錄 ---
LOG_FILENAME = 'rpt_batch_to_bardata_errors.log' # 批次處理使用不同的日誌檔名
logging.basicConfig(filename=LOG_FILENAME,
                    level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s',
                    filemode='w') # 每次運行覆蓋日誌

# --- 1. 常量定義 (與之前處理 TX, MTX 的版本相同) ---
VN_EXCHANGE_TAIFEX = "TAIFEX"
BAR_INTERVAL = "1m"
PANDAS_RESAMPLE_INTERVAL = "1T"

SHIOAJI_MONTH_CODES_FUTURES = {
    1: 'A', 2: 'B', 3: 'C', 4: 'D', 5: 'E', 6: 'F',
    7: 'G', 8: 'H', 9: 'I', 10: 'J', 11: 'K', 12: 'L'
}
PRODUCT_ID_TO_PREFIX_MAP = {"TX": "TXF", "MTX": "MXF"}
TARGET_PRODUCTS = list(PRODUCT_ID_TO_PREFIX_MAP.keys())

CONTRACT_MULTIPLIERS = {"TX": 200, "MTX": 50}
TICK_SIZES = {"TX": 1.0, "MTX": 1.0}

COL_RPT_TRADE_DATE = '成交日期'
COL_RPT_PRODUCT_ID = '商品代號'
COL_RPT_EXPIRY_INFO = '到期月份(週別)'
COL_RPT_TRADE_PRICE = '成交價格'
COL_RPT_TRADE_VOLUME = '成交數量(BpS)' # 假設 TX, MTX 都用此成交量欄位
COL_RPT_TRADE_TIME = '成交時間'

TAIPEI_TZINFO = ZoneInfo("Asia/Taipei")
UTC_TZINFO = ZoneInfo("UTC")

# --- 2. 輔助函數 (與之前版本相同) ---
def parse_rpt_datetime_aware(trade_date_str: str, trade_time_str: str) -> datetime | None:
    try:
        year, month, day = 0,0,0
        if len(trade_date_str)==7 and trade_date_str.startswith('1'): year=int(trade_date_str[:3])+1911; month=int(trade_date_str[3:5]); day=int(trade_date_str[5:7])
        elif len(trade_date_str)==8: year=int(trade_date_str[:4]); month=int(trade_date_str[4:6]); day=int(trade_date_str[6:8])
        else: logging.debug(f"日期格式不符:'{trade_date_str}'"); return None
        hour,minute,second,microsecond = 0,0,0,0; time_len=len(trade_time_str)
        if time_len==5: hour=int(trade_time_str[0]);minute=int(trade_time_str[1:3]);second=int(trade_time_str[3:5])
        elif time_len==6: hour=int(trade_time_str[0:2]);minute=int(trade_time_str[2:4]);second=int(trade_time_str[4:6])
        elif time_len==8: hour=int(trade_time_str[0]);minute=int(trade_time_str[1:3]);second=int(trade_time_str[3:5]);microsecond=int(trade_time_str[5:8])*1000
        elif time_len==9: hour=int(trade_time_str[0:2]);minute=int(trade_time_str[2:4]);second=int(trade_time_str[4:6]);microsecond=int(trade_time_str[6:9])*1000
        else: logging.warning(f"時間格式長度({time_len})不符:'{trade_time_str}'"); return None
        if not(0<=hour<=23 and 0<=minute<=59 and 0<=second<=59): logging.warning(f"時間值無效:H={hour},M={minute},S={second}"); return None
        return datetime(year,month,day,hour,minute,second,microsecond,tzinfo=TAIPEI_TZINFO)
    except(ValueError,IndexError)as e: logging.warning(f"解析日期時間錯誤({type(e).__name__}):{e} (D='{trade_date_str}',T='{trade_time_str}')"); return None
    except Exception as e: logging.error(f"解析日期時間未知錯誤:{e}",exc_info=True); return None

def convert_futures_to_shioaji_5char(rpt_product_id: str, rpt_expiry_month: str) -> str | None:
    try:
        prefix = PRODUCT_ID_TO_PREFIX_MAP.get(rpt_product_id.upper())
        if not prefix: logging.warning(f"商品'{rpt_product_id}'無前綴映射"); return None
        if not(rpt_expiry_month and len(rpt_expiry_month)==6 and rpt_expiry_month.isdigit()): logging.debug(f"到期月份無效:'{rpt_expiry_month}'"); return None
        year=int(rpt_expiry_month[:4]); month=int(rpt_expiry_month[4:6])
        month_char = SHIOAJI_MONTH_CODES_FUTURES.get(month)
        if not month_char: logging.debug(f"無法獲取A-L月份代碼:Month={month}"); return None
        code = f"{prefix}{month_char}{str(year%10)}"; logging.debug(f"轉換(5char):{rpt_product_id} {rpt_expiry_month}->{code}"); return code
    except Exception as e: logging.error(f"轉換期貨代碼(5char)失敗:{e}",exc_info=True); return None

# --- 3. SQLite 資料庫操作 (與之前版本相同) ---
def get_vntrader_db_path(): return os.path.join(os.path.expanduser("~"),".vntrader","database.db")
def create_db_connection(db_file_path: str) -> sqlite3.Connection | None:
    """
    嘗試創建到指定 SQLite 資料庫檔案的連接。
    如果成功，返回 Connection 物件；否則返回 None。
    """
    conn = None
    try:
        conn = sqlite3.connect(db_file_path)
        logging.info(f"成功連接到 SQLite 資料庫: {db_file_path}")
    except sqlite3.Error as e: # 捕獲特定於 sqlite3 的錯誤
        logging.error(f"連接 SQLite 資料庫 '{db_file_path}' 失敗: {e}", exc_info=True) # exc_info=True 記錄更詳細的追蹤信息
        print(f"錯誤: 連接 SQLite 資料庫 '{db_file_path}' 失敗: {e}")
        return None # 連接失敗時明確返回 None
    except Exception as ex: # 捕獲其他所有潛在的非 SQLite 錯誤
        logging.error(f"連接資料庫 '{db_file_path}' 時發生未知錯誤: {ex}", exc_info=True)
        print(f"錯誤: 連接資料庫 '{db_file_path}' 時發生未知錯誤: {ex}")
        return None # 連接失敗時明確返回 None
    return conn
def save_bars_to_sqlite(conn: sqlite3.Connection, bars_df: pd.DataFrame):
    if bars_df.empty: return 0
    sql = """INSERT INTO dbbardata (symbol, exchange, datetime, interval, volume, turnover, 
                                   open_interest, open_price, high_price, low_price, close_price)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT(symbol, exchange, interval, datetime) DO NOTHING;"""
    cursor=conn.cursor(); rows_ins=0
    for _, bar in bars_df.iterrows():
        try:
            dt_val = bar['datetime']
            if not isinstance(dt_val, (pd.Timestamp, datetime)): logging.error(f"Bar({bar.get('symbol')}) datetime類型不正確:{type(dt_val)}"); continue
            aware_utc_dt = dt_val.tz_convert(UTC_TZINFO) if dt_val.tzinfo is not None else dt_val.tz_localize(UTC_TZINFO)
            naive_utc_dt = aware_utc_dt.to_pydatetime().replace(tzinfo=None)
            data = (bar["symbol"],bar["exchange"],naive_utc_dt,bar["interval"],bar["volume"],bar["turnover"],
                    bar.get("open_interest",0.0),bar["open_price"],bar["high_price"],bar["low_price"],bar["close_price"])
            cursor.execute(sql, data); rows_ins += cursor.rowcount
        except Exception as e: logging.error(f"SQLite插入Bar錯誤({bar.get('symbol')}@{bar.get('datetime')}):{e}",exc_info=True)
    try: conn.commit()
    except sqlite3.Error as e: logging.error(f"SQLite提交Bar錯誤:{e}"); return -1
    return rows_ins

# --- 4. RPT 內容處理與聚合 (與之前版本相同) ---
def get_str_from_row(r, col, d=""): v=r.get(col); return str(v).strip() if not pd.isna(v) else d
def extract_date_from_rpt_filename(fn:str) -> str|None:
    m=re.search(r'(?:Daily_|OptionsDaily_)?(\d{4})_(\d{2})_(\d{2})\.rpt$', fn, re.I) or \
      re.search(r'(\d{4})(\d{2})(\d{2})\.rpt$', fn, re.I)
    if m: y,mm,dd=m.groups()[-3:]; return f"{y}{mm}{dd}"
    logging.warning(f"無法從RPT檔名'{fn}'提取日期。"); return None

def process_single_rpt_file_content_to_df(c:str, log_fn:str, def_date:str|None) -> pd.DataFrame:
    ticks = []; logging.info(f"處理RPT(僅{TARGET_PRODUCTS}):{log_fn},備用日期:{def_date}")
    try:
        df = pd.read_csv(StringIO(c),encoding='big5',dtype=str,keep_default_na=False,na_values=[''])
        def std_col(s): return str(s).strip().replace(' ','').replace('(BorS)','(Bors)').replace('(B or S)','(Bors)').replace('(B+S)','(BpS)')
        df.columns=[std_col(col) for col in df.columns]; logging.info(f"RPT:{log_fn}-標準化欄位:{list(df.columns)}")
        req_cols={COL_RPT_TRADE_DATE,COL_RPT_PRODUCT_ID,COL_RPT_TRADE_TIME,COL_RPT_EXPIRY_INFO,COL_RPT_TRADE_PRICE,COL_RPT_TRADE_VOLUME}
        if not req_cols.issubset(set(df.columns)): logging.error(f"RPT:{log_fn}-缺欄位:{req_cols-set(df.columns)}"); return pd.DataFrame()
        if df.empty: logging.info(f"RPT:{log_fn}-DataFrame為空"); return pd.DataFrame()
        logging.info(f"RPT:{log_fn}-共{len(df)}行,開始提取Ticks(僅{TARGET_PRODUCTS})...")
        for i,r in df.iterrows():
            pid=get_str_from_row(r,COL_RPT_PRODUCT_ID).upper()
            if pid not in TARGET_PRODUCTS: continue
            logging.debug(f"RPT:{log_fn}-行{i+2}({pid})")
            d_str=get_str_from_row(r,COL_RPT_TRADE_DATE); t_str=get_str_from_row(r,COL_RPT_TRADE_TIME)
            cur_d_str=d_str if d_str else def_date
            if not cur_d_str: logging.warning(f"行{i+2}日期欄位與檔名日期均為空!"); continue
            dt_aware=parse_rpt_datetime_aware(cur_d_str,t_str)
            if not dt_aware: logging.warning(f"無法解析日期時間 行{i+2}:D='{cur_d_str}',T='{t_str}'"); continue
            p_str=get_str_from_row(r,COL_RPT_TRADE_PRICE); v_str=get_str_from_row(r,COL_RPT_TRADE_VOLUME)
            exp_str=get_str_from_row(r,COL_RPT_EXPIRY_INFO)
            try: price=float(p_str) if p_str else 0.0; vol_m=re.match(r"(\d+)",v_str); volume=int(vol_m.group(1)) if vol_m else 0
            except(ValueError,TypeError): logging.warning(f"價/量格式錯誤 行{i+2}:P='{p_str}',V='{v_str}'"); continue
            if volume==0: logging.debug(f"行{i+2}成交量為0,跳過"); continue
            sym=convert_futures_to_shioaji_5char(pid,exp_str)
            if sym:
                mult=CONTRACT_MULTIPLIERS.get(pid,1); turnover=price*volume*mult
                ticks.append({"datetime":dt_aware,"symbol":sym,"exchange":VN_EXCHANGE_TAIFEX,
                              "price":price,"volume":float(volume),"turnover":turnover})
                logging.debug(f"提取Tick:{sym}@{dt_aware},P={price},V={volume}")
    except Exception as e: logging.error(f"處理RPT'{log_fn}'失敗:{e}",exc_info=True); print(f"錯誤:處理RPT'{log_fn}'失敗:{e}")
    if not ticks: logging.info(f"RPT:{log_fn}-未提取到{TARGET_PRODUCTS}有效Ticks"); return pd.DataFrame()
    df_t=pd.DataFrame(ticks); logging.info(f"RPT:{log_fn}-提取{len(df_t)}筆{TARGET_PRODUCTS}Ticks"); return df_t

def aggregate_ticks_to_bars(ticks_df: pd.DataFrame, interval: str = PANDAS_RESAMPLE_INTERVAL) -> pd.DataFrame:
    # (函數內容與上一版本相同)
    if ticks_df.empty or 'datetime' not in ticks_df.columns: logging.warning("aggregate_ticks_to_bars: 輸入的 ticks_df 為空或缺少 'datetime' 欄位。"); return pd.DataFrame()
    logging.info(f"開始聚合 {len(ticks_df)} Ticks -> {interval} K線...")
    try:
        ticks_df['datetime'] = pd.to_datetime(ticks_df['datetime'], errors='coerce', utc=True)
        ticks_df.dropna(subset=['datetime'], inplace=True)
        if ticks_df.empty: logging.info("聚合：轉換時間格式或dropna後DataFrame為空。"); return pd.DataFrame()
        if ticks_df['datetime'].dt.tz is None: ticks_df['datetime'] = ticks_df['datetime'].dt.tz_localize(UTC_TZINFO)
        elif str(ticks_df['datetime'].dt.tz) != str(UTC_TZINFO): ticks_df['datetime'] = ticks_df['datetime'].dt.tz_convert(UTC_TZINFO)
        ticks_df.set_index('datetime', inplace=True)
        bars_list = []
        for symbol, group in ticks_df.groupby('symbol'):
            if group.empty: continue
            logging.debug(f"聚合商品: {symbol} ({len(group)} ticks)")
            bar_data = group.resample(interval, label='left', closed='left').agg(
                open_price=('price', 'first'), high_price=('price', 'max'),
                low_price=('price', 'min'), close_price=('price', 'last'),
                volume=('volume', 'sum'), turnover=('turnover', 'sum')
            )
            bar_data.dropna(subset=['open_price'], inplace=True)
            if bar_data.empty: continue
            bar_data['symbol'] = symbol; bar_data['exchange'] = VN_EXCHANGE_TAIFEX
            bar_data['interval'] = BAR_INTERVAL; bar_data['open_interest'] = 0.0
            bars_list.append(bar_data)
        if not bars_list: logging.info("所有商品聚合後均無有效K線"); return pd.DataFrame()
        bars_df_final = pd.concat(bars_list).reset_index(); bars_df_final.rename(columns={'index': 'datetime'}, inplace=True) # 如果reset_index後列名是 'index'
        logging.info(f"成功聚合 {len(ticks_df)} Ticks -> {len(bars_df_final)}筆 {interval} K線")
        return bars_df_final
    except Exception as e: logging.error(f"聚合Ticks到Bars時錯誤:{e}",exc_info=True); return pd.DataFrame()

# --- 5. ZIP 檔案處理 (只處理期貨ZIP, 僅目標商品 TX, MTX) ---
def process_single_zip_file_target_futures(zip_f:str, db_conn:sqlite3.Connection) -> tuple[int,int]:
    # (函數內容與上一版本相同)
    zip_fn=os.path.basename(zip_f); all_dfs=[]; rpt_cnt=0; logging.info(f"處理ZIP(僅{TARGET_PRODUCTS}):{zip_fn}")
    try:
        with zipfile.ZipFile(zip_f,'r') as arch:
            for mem_n in arch.namelist():
                if mem_n.lower().endswith('.rpt'):
                    rpt_cnt+=1; rpt_base_fn=os.path.basename(mem_n)
                    logging.info(f"  讀取RPT:{mem_n}(Base:{rpt_base_fn})")
                    def_d=extract_date_from_rpt_filename(rpt_base_fn)
                    logging.info(f"  檔名備用日期:'{def_d}'")
                    try:
                        rpt_b=arch.read(mem_n); rpt_s=rpt_b.decode('big5',errors='replace')
                        df_rpt=process_single_rpt_file_content_to_df(rpt_s,f"{zip_fn}/{mem_n}",def_d)
                        if not df_rpt.empty: all_dfs.append(df_rpt)
                    except Exception as e: logging.error(f"處理RPT'{mem_n}'內容失敗:{e}",exc_info=True)
    except zipfile.BadZipFile as bzfe: logging.error(f"無法開啟ZIP(損壞?):{zip_fn}:{bzfe}"); print(f"警告: ZIP檔案 '{zip_fn}' 損壞或無法開啟，已跳過。"); return 0,0
    except Exception as e: logging.error(f"處理ZIP未知錯誤:{e}",exc_info=True); print(f"警告: 處理ZIP檔案 '{zip_fn}' 時發生未知錯誤，已跳過。"); return 0,0
    
    total_ticks=0; saved_bars=0
    if not all_dfs: logging.info(f"ZIP {zip_fn}:未提取到有效Ticks({TARGET_PRODUCTS})"); return 0,0
    combo_df=pd.concat(all_dfs,ignore_index=True); total_ticks=len(combo_df)
    logging.info(f"ZIP {zip_fn}:共合併{total_ticks}筆{TARGET_PRODUCTS}Ticks,準備聚合...")
    bars=aggregate_ticks_to_bars(combo_df)
    if not bars.empty and db_conn:
        saved_bars=save_bars_to_sqlite(db_conn,bars)
        saved_bars=saved_bars if saved_bars>=0 else 0
    elif bars.empty: logging.info(f"ZIP {zip_fn}:聚合後無有效Bar")
    logging.info(f"處理完ZIP:{zip_fn},提Ticks:{total_ticks},生Bars:{len(bars)},寫DBBars:{saved_bars}")
    return total_ticks,saved_bars

# --- 6. 主執行區塊 (修改為批次處理) ---
def main_batch_process():
    print(f"--- {','.join(TARGET_PRODUCTS)} RPT 資料批次聚合為 1 分鐘 BarData 工具 v{datetime.now().strftime('%Y%m%d')} ---")
    print(f"日誌檔案位於: {os.path.abspath(LOG_FILENAME)}")
    print("警告: 執行此腳本可能會修改您的 VN Trader 資料庫 (dbbardata 表)，請務必提前備份！")

    while True:
        data_root_folder = input("請輸入包含年份子資料夾的父資料夾路徑: ").strip()
        if os.path.isdir(data_root_folder):
            break
        else:
            print(f"錯誤：路徑 '{data_root_folder}' 不存在或不是一個有效的資料夾，請重新輸入。")
    
    print(f"\n--- 批次處理開始 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")
    logging.info(f"開始批次處理，根資料夾: {data_root_folder}, 目標商品: {TARGET_PRODUCTS}")

    overall_ticks_extracted = 0
    overall_bars_saved = 0
    overall_zip_processed = 0
    overall_zip_skipped_破損 = 0

    db_conn = create_db_connection(get_vntrader_db_path())
    if not db_conn:
        print("無法連接到資料庫，終止批次處理。")
        return

    try:
        for year_folder_name in sorted(os.listdir(data_root_folder)):
            year_folder_path = os.path.join(data_root_folder, year_folder_name)
            if os.path.isdir(year_folder_path) and year_folder_name.isdigit() and len(year_folder_name) == 4:
                logging.info(f"開始處理年份資料夾: {year_folder_path}")
                print(f"\n正在處理年份: {year_folder_name}")
                
                year_ticks_extracted = 0
                year_bars_saved = 0
                year_zip_processed = 0

                for zip_file_name in sorted(os.listdir(year_folder_path)):
                    # 只處理期貨的 Daily ZIP 檔案
                    if zip_file_name.lower().startswith("daily_") and \
                       not zip_file_name.lower().startswith("optionsdaily_") and \
                       zip_file_name.lower().endswith(".zip"):
                        
                        zip_file_path = os.path.join(year_folder_path, zip_file_name)
                        overall_zip_processed += 1
                        year_zip_processed +=1
                        print(f"  處理 ZIP ({year_zip_processed}): {zip_file_name}")
                        logging.info(f"批次處理 ZIP: {zip_file_path}")
                        
                        # 調用單檔處理邏輯
                        try:
                            extracted_ticks, saved_bars = process_single_zip_file_target_futures(zip_file_path, db_conn)
                            year_ticks_extracted += extracted_ticks
                            year_bars_saved += saved_bars if saved_bars > 0 else 0
                            
                            log_msg_prefix = f"    => 從 {zip_file_name}: "
                            if saved_bars > 0: print(f"{log_msg_prefix}提取 {extracted_ticks} Ticks, 聚合並寫入 {saved_bars} Bars。")
                            elif extracted_ticks > 0 and saved_bars == 0 : print(f"{log_msg_prefix}提取了 {extracted_ticks} Ticks, 但無 Bar 寫入DB。")
                            elif saved_bars < 0: print(f"{log_msg_prefix}提取了 {extracted_ticks} Ticks, 但資料庫提交 Bars 失敗。")
                            # else: (extracted_ticks == 0 and saved_bars == 0) -> 已在內部記錄
                        except zipfile.BadZipFile: # process_single_zip_file_target_futures 內部已處理並返回0,0
                            overall_zip_skipped_破損 +=1
                        except Exception as e_zip_process: # 捕獲單個zip處理中的其他意外錯誤
                            logging.error(f"處理ZIP檔案 '{zip_file_name}' 時發生頂層錯誤: {e_zip_process}", exc_info=True)
                            print(f"警告: 處理ZIP檔案 '{zip_file_name}' 時發生錯誤，已跳過。詳見日誌。")


                logging.info(f"年份 {year_folder_name} 處理完畢。共提取 Ticks: {year_ticks_extracted}, 寫入 Bars: {year_bars_saved}")
                print(f"年份 {year_folder_name} 處理完畢。提取 Ticks: {year_ticks_extracted}, 寫入 Bars: {year_bars_saved}")
                overall_ticks_extracted += year_ticks_extracted
                overall_bars_saved += year_bars_saved
            else:
                logging.info(f"跳過非年份資料夾: {year_folder_path}")


        print(f"\n--- 全部批次處理完成 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")
        print(f"總共掃描並嘗試處理了 {overall_zip_processed} 個期貨 ZIP 檔案。")
        if overall_zip_skipped_破損 > 0: # 這個計數可能不完全準確，因為錯誤在 process_single_zip_file_target_futures 中捕獲
             print(f"其中有 ZIP 檔案可能因損壞或無法開啟而被跳過 (詳見 {LOG_FILENAME})。")
        print(f"總共從 RPT 檔案中提取了 {overall_ticks_extracted} 筆 {TARGET_PRODUCTS} Tick 數據。")
        print(f"總共成功寫入了 {overall_bars_saved} 筆 1分鐘 Bar 數據到資料庫。")
    finally:
        if db_conn: db_conn.close(); logging.info("批次處理資料庫連接已關閉。")
    print("\n批次腳本執行完畢。")

if __name__ == "__main__":
    # 不再詢問模式，直接進入批次處理的主流程
    main_batch_process()