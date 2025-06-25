import shioaji as sj
import pandas as pd
from datetime import datetime, timedelta
import time
import pathlib
import logging

# ---------- 0. 基本設定 ----------
API_KEY       = "ANu85PtAbUngajTYfFMTbxTYs8weEWx9KVjaw5h7DWhe"
SECRET_KEY    = "AcGtYJV7svxTnK5232hqAqkwXhmt4pqdRR6FbaMDi5LQ"
CA_PATH       = "/path/to/ca.pfx"    # PFX 憑證路徑
CA_PASSWORD   = "CA_PASSWORD"
OUTDIR        = pathlib.Path("tick_csv")
OUTDIR.mkdir(exist_ok=True)

SYMBOLS = {                 # TSE 股票代號↔契約，省得每次用字串
    "2316":None          # 聯發科
}

# ---------- 1. 登入 & 下載契約 ----------
api = sj.Shioaji()
api.login(api_key=API_KEY, secret_key=SECRET_KEY)              # token 登入:contentReference[oaicite:2]{index=2}
api.fetch_contracts(contract_download=True)                    # 下載全部契約:contentReference[oaicite:3]{index=3}

# 取得股票契約物件
for code in SYMBOLS:
    SYMBOLS[code] = api.Contracts.Stocks[code]

# ---------- 2. 建立最近 7 個交易日清單 ----------
today = datetime.now().date()
# 產生往前抓 14 天，然後用 bdate_range 過濾出最近 7 個商業日:contentReference[oaicite:4]{index=4}
candidate_days = pd.date_range(end=today, periods=14, freq="B").date
trade_days = candidate_days[-7:]                               # 取最近 7 個

# ---------- 3. 下載 ticks → CSV ----------
for code, contract in SYMBOLS.items():
    for d in trade_days:
        date_str = d.strftime("%Y-%m-%d")
        fname    = OUTDIR / f"{code}_{date_str}.csv"
        try:
            ticks = api.ticks(
                contract=contract,
                date=date_str,
                query_type=sj.constant.TicksQueryType.AllDay,  # 全天資料:contentReference[oaicite:5]{index=5}
                timeout=30_000,
            )
            if len(ticks.ts) == 0:       # 休市／無資料
                logging.warning(f"{code} {date_str} 無資料，跳過")
                continue

            df = pd.DataFrame({**ticks})
            df["ts"]   = pd.to_datetime(df.ts)
            df["code"] = code
            df["date"] = d
            df.to_csv(fname, index=False)                      # 寫檔:contentReference[oaicite:6]{index=6}
            print(f"✅ Saved {fname} ({len(df)} rows)")
            time.sleep(0.3)  # 避免太快觸發 API 限速:contentReference[oaicite:7]{index=7}

        except Exception as e:
            logging.exception(f"❌ 下載 {code} {date_str} 失敗: {e}")
