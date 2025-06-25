import inspect
from fubon_neo.sdk import FubonSDK

# 此腳本僅用於檢查函式簽章，並不會真的連線或登入
try:
    sdk = FubonSDK(30, 2) # 根據 2.2.2 版的規則，初始化時需要提供參數
    
    # 使用 inspect 模組來獲取並印出 login 函式的實際簽章
    signature = inspect.signature(sdk.login)
    
    print("--- FubonSDK.login 的實際函式簽章 ---")
    print(signature)
    
    print("\n--- 可用的參數列表 ---")
    print(list(signature.parameters.keys()))

except Exception as e:
    print(f"執行檢查時發生錯誤: {e}")