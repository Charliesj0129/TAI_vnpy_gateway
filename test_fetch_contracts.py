import shioaji as sj
from datetime import datetime, date
from typing import Optional, Tuple
API_KEY = "5CMvwjbGomFcqfRvWn3QVQ5fczsrUYW2dFS9PdTjVdZw"

SECRET_KEY = "ECS8bSCtsVEze9jrXZFQNufUCc19kkdKyhoi55pYoU2c"
api = sj.Shioaji()
api.login(
    api_key=API_KEY,
    secret_key=SECRET_KEY,
    contracts_cb=lambda st: print(f"{st} contracts ready.")
)  # 會自動下載商品檔 :contentReference[oaicite:0]{index=0}
def find_mxf_contract(api: sj.Shioaji) -> Tuple[Optional[str], Optional[date]]:
    """
    傳回 (symbol, expiry_date)：
    1. 先找 MXFR1；若不存在再找最近未到期 MXFyyyyMM。
    """
    mxf_cat = api.Contracts.Futures.MXF         # StreamMultiContract
    # ① 連續月
    cont = getattr(mxf_cat, "MXFR1", None)      # Shioaji v1.0 起提供&#8203;:contentReference[oaicite:2]{index=2}
    if cont:
        exp = datetime.strptime(cont.delivery_date, "%Y/%m/%d").date()
        return cont.symbol, exp

    # ② Fallback 最近月份
    today = date.today()
    best: Optional[Tuple[str, date]] = None
    for fut in mxf_cat:                         # 直接迭代
        if fut.symbol.startswith("MXF"):        # 保險過濾
            exp = datetime.strptime(fut.delivery_date, "%Y/%m/%d").date()
            if exp >= today and (best is None or exp[1] < best[1]):
                best = (fut.symbol, exp)
    return best if best else (None, None)

def test_find_mxf(api):
    sym, exp = find_mxf_contract(api)
    assert sym is not None, "MXF symbol not found"
    assert exp is not None, "expiry not found"
    assert sym.startswith("MXF"), f"unexpected symbol {sym}"
test_find_mxf(api)