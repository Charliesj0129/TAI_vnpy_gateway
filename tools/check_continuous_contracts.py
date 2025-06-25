import sqlite3
import os

# --- 設定 ---
DATABASE_PATH = r"C:\Users\charl\.vntrader\database.db" # 請務必修改為您的資料庫檔案實際路徑
PRODUCT_BASES = ['MXF', 'TXF']
TARGET_INTERVAL = '1m' # 根據您之前的確認，這裡應該是 '1m'

# --- 核心函式 ---

def generate_continuous_contract(db_path, product_base, r1_symbol_name):
    """
    為指定的商品基礎代號生成連續合約資料，並處理重疊資料。
    """
    print(f"開始處理商品: {product_base}，生成連續合約: {r1_symbol_name}...")

    if not os.path.exists(db_path):
        print(f"錯誤: 資料庫檔案 '{db_path}' 不存在。請檢查 DATABASE_PATH 設定。")
        return

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 1. 清除舊的 R1 資料
        print(f"  正在清除舊的 '{r1_symbol_name}' ({TARGET_INTERVAL}) 資料...")
        delete_query = "DELETE FROM dbbardata WHERE symbol = ? AND interval = ?"
        cursor.execute(delete_query, (r1_symbol_name, TARGET_INTERVAL))
        print(f"  {cursor.rowcount} 筆舊資料已刪除。")

        # 2. 提取原始月合約資料
        #    按 datetime 升序，然後按 symbol 降序 (確保最新月合約在前)
        #    同時選出原始 symbol，主要用於 ORDER BY
        print(f"  正在提取 '{product_base}%' ({TARGET_INTERVAL}) 的原始月合約資料...")
        select_query = """
        SELECT 
            symbol, exchange, datetime, volume, turnover, open_interest, 
            open_price, high_price, low_price, close_price 
        FROM dbbardata 
        WHERE 
            symbol LIKE ? AND 
            symbol NOT LIKE '%R1' AND 
            interval = ?
        ORDER BY datetime ASC, symbol DESC 
        """
        cursor.execute(select_query, (f"{product_base}%", TARGET_INTERVAL))
        source_data_rows = cursor.fetchall()

        if not source_data_rows:
            print(f"  未找到商品 '{product_base}%' ({TARGET_INTERVAL}) 的原始資料。")
            conn.close() # 確保連接關閉
            return

        print(f"  共提取到 {len(source_data_rows)} 筆原始資料，開始去重並準備插入...")

        # 3. 準備並插入新的 R1 資料，加入去重邏輯
        r1_data_to_insert = []
        # 使用 set 來追蹤已經處理過的 (exchange, datetime) 組合
        # 以確保每個時間點只取一筆 (最新月份合約的) 資料
        processed_keys = set() 

        for row in source_data_rows:
            # row 內容依序對應 SELECT 查詢的欄位:
            # original_symbol, exchange, datetime, volume, turnover, open_interest,
            # open_price, high_price, low_price, close_price
            
            # original_symbol = row[0] # 主要用於排序，我們不直接用它來構造 R1
            exchange_val = row[1]
            datetime_val = row[2]
            
            current_key = (exchange_val, datetime_val)

            if current_key not in processed_keys:
                # 這是這個 (exchange, datetime) 遇到的第一筆記錄
                # 因為 ORDER BY datetime ASC, symbol DESC，所以它是來自該分鐘內 "最新" 月份的合約
                new_row_for_r1 = (
                    r1_symbol_name,    # R1 symbol (e.g., MXFR1)
                    exchange_val,      # exchange
                    datetime_val,      # datetime
                    TARGET_INTERVAL,   # interval (e.g., '1m')
                    row[3],            # volume (對應原始 SELECT 中的 volume)
                    row[4],            # turnover
                    row[5],            # open_interest
                    row[6],            # open_price
                    row[7],            # high_price
                    row[8],            # low_price
                    row[9]             # close_price
                )
                r1_data_to_insert.append(new_row_for_r1)
                processed_keys.add(current_key)
        
        if r1_data_to_insert:
            print(f"  去重後，準備將 {len(r1_data_to_insert)} 筆資料插入為 '{r1_symbol_name}'...")
            insert_query = """
            INSERT INTO dbbardata (
                symbol, exchange, datetime, interval, volume, turnover, 
                open_interest, open_price, high_price, low_price, close_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            cursor.executemany(insert_query, r1_data_to_insert)
            conn.commit()
            print(f"  成功插入 {cursor.rowcount} 筆 '{r1_symbol_name}' 資料。")
        else:
            print("  沒有準備好可插入的 R1 資料 (可能所有資料都已存在或被過濾)。")

    except sqlite3.Error as e:
        print(f"資料庫錯誤 ({product_base}): {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
        print(f"完成處理商品: {product_base}。\n")

# --- 主執行區塊 ---
if __name__ == '__main__':
    print("開始執行連續合約生成腳本 (已更新去重邏輯)...\n")

    if 'your_database_file.db' in DATABASE_PATH:
        print("重要提示：請務必在腳本中修改 'DATABASE_PATH' 為您實際的資料庫檔案路徑！")
        print("腳本執行中止。\n")
    else:
        for base_code in PRODUCT_BASES:
            r1_name = f"{base_code}R1"
            generate_continuous_contract(DATABASE_PATH, base_code, r1_name)
        
        print("所有指定商品的連續合約生成完畢。")