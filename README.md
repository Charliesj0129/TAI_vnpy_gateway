# **Shioaji 多帳戶期貨證券交易 Gateway (for VnPy)**

本項目是一個為 VnPy 交易平台設計的客製化 Gateway，旨在通過單一 Gateway 實例管理多個永豐金證券 (Shioaji) 的期貨與證券交易帳戶。它提供了集中的帳戶管理、行情訂閱和訂單路由功能，同時保留了每個帳戶操作的獨立性。

## **目錄**

* [主要功能與優點](#bookmark=id.pr47k0rhgmz6)  
* [限制與潛在缺點](#bookmark=id.b25mtnlzj74w)  
* [特別處理與配置指南](#bookmark=id.yj7p9tdh4nfz)  
  * [設定檔](#bookmark=id.l912whpq1cf) shioaji\_manager\_connect.json  
  * [策略代碼適配](#bookmark=id.4gy67z9183ld)  
  * [日誌查看](#bookmark=id.hmv0xwp32fd1)  
* [vnpy\_optionmaster Cython 編譯與除錯指南](#bookmark=id.ykfnyw6gjaqm)  
  * [前置需求](#bookmark=id.ks17o9rutsde)  
  * [步驟一：獲取 vnpy\_optionmaster 原始碼](#bookmark=id.5k1n6omc4gbv)  
  * [步驟二：進入各 Cython 模型子目錄並編譯](#bookmark=id.r9yekzgmlb9h)  
  * [步驟三：部署編譯好的 .pyd 檔案到虛擬環境](#bookmark=id.npbzbil5dv2l)  
  * [步驟四：清理 Python 快取並驗證導入](#bookmark=id.u1iz85amite0)  
  * [附註 (Cython 編譯)](#bookmark=id.bddy6lwm7sp1)

## **主要功能與優點 (Features / Advantages)**

* **多帳戶集中管理**:  
  * 在單一 VnPy Gateway (SHIOAJI\_MULTI) 實例下同時運行和監控多個永豐金證券帳戶（例如，一個股票帳戶、一個期貨帳戶，或多個同類型帳戶）。  
  * 每個子帳戶由獨立的 ShioajiSessionHandler 實例管理，確保操作隔離。  
* **配置驅動**:  
  * 所有帳戶憑證、API 設定以及 Manager 的特定行為（如主合約下載帳戶、預設下單帳戶、連接延遲）均通過 shioaji\_manager\_connect.json 文件進行配置，方便管理與部署。  
* **集中的合約資料管理**:  
  * 由 ShioajiSessionManager 或指定的主 Handler 負責下載和處理交易合約，推送到 MainEngine 前進行去重，確保全局合約資訊的一致性，減少冗餘。  
* **高效的行情訂閱**:  
  * ShioajiSessionManager 集中管理行情訂閱請求，對每個 vt\_symbol 僅由一個 Handler 執行實際的 API 層級訂閱，並通過引用計數追蹤訂閱需求，避免重複推送和資源浪費。  
* **清晰的訂單路由**:  
  * 策略在發起 OrderRequest 時**必須**指定 accountid (對應 vnpy\_account\_id)，Manager 根據此 ID 將訂單精準路由到對應的 Handler 進行處理。  
  * 對於從 VnPy UI 發起且未指定 accountid 的手動訂單，支持路由到預設帳戶。  
* **全局唯一的ID系統**:  
  * 生成的 vt\_orderid, vt\_tradeid 均包含 Handler 的唯一標識 (例如 SHIOAJI\_MULTI.STOCK\_ACCOUNT\_01.S12345)，確保在多帳戶環境下的全局唯一性。  
  * AccountData, PositionData 等物件均正確標記其所屬的 vnpy\_account\_id，方便策略和 UI 層面區分。  
* **獨立的帳戶重連機制**:  
  * 每個 ShioajiSessionHandler 擁有獨立的斷線檢測和重連邏輯，一個帳戶的連接問題不會影響其他帳戶的正常運行。  
  * 重連成功後會自動恢復先前的行情訂閱。  
* **行情數據聚合 (Conflation)**:  
  * 每個 Handler 內部對接收到的原始 Tick 和 BidAsk數據進行聚合處理，然後再生成 VnPy 的 TickData 推送，有助於處理高頻數據流並降低系統負載。  
* **穩健的併發處理**:  
  * 大量使用 threading.Lock 保護共享數據，並通過 janus.Queue 將 SDK 的異步回調與耗時的處理邏輯解耦，後者在獨立的執行緒池中運行，提高了系統的穩定性和響應速度。  
* **可配置的啟動延遲**:  
  * 允許在 shioaji\_manager\_connect.json 中設定每個帳戶連接之間的延遲時間，避免在啟動時因同時發起過多 API 請求而導致連接失敗。

## **限制與潛在缺點 (Limitations / Disadvantages)**

* **策略代碼適配**:  
  * 現有的單帳戶策略需要修改才能與此多帳戶 Gateway 配合使用，最主要的是在發送訂單 (OrderRequest) 時必須明確提供目標 accountid (即 vnpy\_account\_id)。  
* **VnPy UI 手動下單限制**:  
  * 標準的 VnPy 手動下單介面可能無法直接選擇子帳戶。此 Gateway 實作了預設帳戶路由邏輯：若手動下單時未指定帳戶，則訂單會嘗試路由到設定的預設帳戶（如 default\_order\_account\_id 或第一個可用帳戶）。這意味著無法通過標準 UI 靈活選擇手動下單的目標子帳戶，除非對 UI 進行客製化修改。  
* **行情訂閱故障轉移非即時主動**:  
  * 當負責某行情的 Handler 斷線後，Manager 會清除其 API 訂閱記錄。若其他策略仍需要此行情，目前依賴於新的訂閱請求或該 Handler 重連成功並重新訂閱。系統不會在斷線瞬間立即將「孤兒」訂閱主動轉移給其他健康的 Handler（此為用戶選擇維持現狀）。  
* **複雜性增加**:  
  * 相較於單帳戶 Gateway，多帳戶管理本身引入了額外的配置和狀態管理層次，理解和調試可能需要更多關注。

## **特別處理與配置指南 (Special Handling / Configuration)**

### **1\. 設定檔** shioaji\_manager\_connect.json

此檔案是 Gateway 運行的核心，包含兩大部分：manager\_settings 和 session\_configs。

* manager\_settings:  
  * query\_timer\_interval (整數, 可選): 定期查詢帳戶資金和持倉的間隔秒數，設為 0 表示禁用。預設 0。  
  * primary\_contract\_handler\_id (字串, 可選): 指定哪個 vnpy\_account\_id 對應的 Handler 負責主要的合約下載。如果未指定，則第一個成功連接的 Handler 會被選為主 Handler。  
  * default\_order\_account\_id (字串, 可選): 指定當 OrderRequest 未提供 accountid 時（通常來自 UI 手動下單），應路由到的預設 vnpy\_account\_id。如果未指定，會依次嘗試 primary\_contract\_handler\_id 或第一個可用的 Handler。  
  * connect\_delay\_seconds (浮點數, 可選): Manager 在啟動並連接每個帳戶 Session Handler 之間等待的秒數。建議設為 1.0 到 5.0 之間的值，以避免同時連接過多帳戶。預設 1.0。  
* session\_configs (列表): 每個元素是一個字典，代表一個要管理的 Shioaji 帳戶。  
  * vnpy\_account\_id (字串, **必填**): 在 VnPy 系統中此帳戶的唯一標識符，例如 "STOCK\_ACCOUNT\_01", "FUTURES\_ACCOUNT\_01"。  
  * APIKey (字串, **必填**): 永豐金 API Key。  
  * SecretKey (字串, **必填**): 永豐金 API Secret Key。  
  * CA路徑 (字串, 可選): CA 憑證檔案的路徑 (通常是 .pfx 檔案)。對於實盤交易，此項通常是必需的。  
  * CA密碼 (字串, 可選): CA 憑證的密碼。  
  * 身分證字號 (字串, 可選): CA 簽署時可能需要的身分證字號。  
  * simulation (布林值, **必填**): true 表示模擬盤，false 表示實盤。  
  * force\_download (布林值, 可選): 是否在每次連接時強制重新下載合約。建議主合約 Handler 設為 true，其他設為 false。預設 true。  
  * conflation\_interval\_sec (浮點數, 可選): Handler 內部行情聚合的時間間隔（秒）。設為 0 表示盡快處理。預設 0.050 (50毫秒)。  
  * janus\_batch\_timeout\_sec (浮點數, 可選): Handler 內部處理訂單/成交回報的 janus.Queue 批次收集超時（秒）。預設 0.1。  
  * reconnect\_limit (整數, 可選): Handler 斷線後的最大自動重連嘗試次數。預設 3。  
  * reconnect\_interval (整數, 可選): Handler 斷線後每次重連嘗試之間的間隔秒數。預設 5。  
  * contracts\_cb\_timeout\_sec (浮點數, 可選): Handler 等待 Shioaji API 合約下載回調的超時時間（秒）。預設 60.0。

**範例** shioaji\_manager\_connect.json**:**

{  
    "manager\_settings": {  
        "query\_timer\_interval": 60,  
        "primary\_contract\_handler\_id": "STOCK\_ACCOUNT\_01",  
        "default\_order\_account\_id": "STOCK\_ACCOUNT\_01",  
        "connect\_delay\_seconds": 2.0  
    },  
    "session\_configs": \[  
        {  
            "vnpy\_account\_id": "STOCK\_ACCOUNT\_01",  
            "APIKey": "YOUR\_API\_KEY\_1",  
            "SecretKey": "YOUR\_SECRET\_KEY\_1",  
            "CA路徑": "C:/path/to/your/ca1.pfx",  
            "CA密碼": "YOUR\_CA\_PASSWORD\_1",  
            "身分證字號": "A123456789",  
            "simulation": false,  
            "force\_download": true  
        },  
        {  
            "vnpy\_account\_id": "FUTURES\_ACCOUNT\_01",  
            "APIKey": "YOUR\_API\_KEY\_2",  
            "SecretKey": "YOUR\_SECRET\_KEY\_2",  
            "CA路徑": "C:/path/to/your/ca2.pfx",  
            "CA密碼": "YOUR\_CA\_PASSWORD\_2",  
            "身分證字號": "B987654321",  
            "simulation": false,  
            "force\_download": false,  
            "conflation\_interval\_sec": 0.020   
        }  
    \]  
}

### **2\. 策略代碼適配**

* **下單**: 策略在創建 OrderRequest 時，必須設置 req.accountid 欄位為目標帳戶的 vnpy\_account\_id。  
  \# 策略代碼中  
  req \= OrderRequest(  
      symbol="2330",  
      exchange=Exchange.TWSE,  
      direction=Direction.LONG,  
      type=OrderType.LIMIT,  
      volume=1,  
      price=600.0,  
      offset=Offset.OPEN,  
      reference="MyStrategyOrder",  
      accountid="STOCK\_ACCOUNT\_01"  \# \<--- 必須指定  
  )  
  self.send\_order(req)

* **查詢數據**: 當策略從 MainEngine 查詢持倉 (get\_position, get\_all\_positions)、帳戶資金 (get\_account, get\_all\_accounts) 等信息時，返回的結果可能包含來自多個子帳戶的數據。策略需要根據數據物件中的 accountid 欄位進行過濾，以獲取其關心的特定帳戶的數據。

### **3\. 日誌查看**

* Gateway Manager (SHIOAJI\_MULTI) 會產生關於其自身操作和從各 Handler 匯總的日誌。  
* 每個 Session Handler 也會產生關於其自身連接、API交互、錯誤等日誌，其 gateway\_name 格式為 SHIOAJI\_MULTI.VNPY\_ACCOUNT\_ID (例如 SHIOAJI\_MULTI.STOCK\_ACCOUNT\_01)。  
* 在調試問題時，請務必同時關注 Manager 和相關 Handler 的日誌。

## **vnpy\_optionmaster Cython 編譯與除錯指南 😃**

如果您在使用期權相關功能時遇到「Failed to import cython option pricing model」的錯誤，通常是因為預編譯的 Cython 模型與您的 Python 環境 (如此處的 Python 3.12) 不兼容。以下步驟說明如何在 **Python 3.12** 環境中，手動編譯並部署 vnpy\_optionmaster 的 Cython 模組。

### **前置需求 ✅**

1. **Python 3.12** (CPython) 虛擬環境已建立並啟動。  
2. 已安裝基礎編譯相關套件：  
   pip install \--upgrade pip setuptools wheel cython

3. 作業系統：Windows x64 (此處以 Windows 為例，其他平台步驟類似，請注意調整路徑和文件名)。  
4. C++ 編譯環境：通常需要 Microsoft C++ Build Tools (可通過 Visual Studio Installer 安裝，選擇 "Desktop development with C++" 工作負載)。

### **步驟一：獲取** vnpy\_optionmaster **原始碼 📥**

\# 假設您的工作目錄是 C:\\Users\\charl\\vnpy\_inter\_shioaji  
cd C:\\Users\\charl\\vnpy\_inter\_shioaji

\# 從 GitHub Clone vnpy\_optionmaster 專案  
git clone \[https://github.com/vnpy/vnpy\_optionmaster.git\](https://github.com/vnpy/vnpy\_optionmaster.git)  
cd vnpy\_optionmaster

📂 目錄結構示意：

vnpy\_optionmaster/  
├── setup.py  
├── pyproject.toml  
└── vnpy\_optionmaster/  
    └── pricing/  
        └── cython\_model/  \<-- 主要操作目錄  
            ├── binomial\_tree\_cython/  
            ├── black\_76\_cython/  
            └── black\_scholes\_cython/

### **步驟二：進入各 Cython 模型子目錄並編譯 🔨**

vnpy\_optionmaster 的每個期權定價 Cython 模型都在 pricing/cython\_model/ 下的獨立子目錄中，並且每個子目錄都有自己的 setup.py 文件用於編譯。請依次進入這些目錄並執行編譯命令：

\# 定位到第一個 Cython 模型目錄  
cd vnpy\_optionmaster\\pricing\\cython\_model\\binomial\_tree\_cython  
\# 執行編譯 (原地編譯，生成 .pyd 在當前目錄)  
python setup.py build\_ext \--inplace

\# 移動到第二個模型目錄  
cd ..\\black\_76\_cython  
python setup.py build\_ext \--inplace

\# 移動到第三個模型目錄  
cd ..\\black\_scholes\_cython  
python setup.py build\_ext \--inplace

成功執行後，您應該在每個模型的目錄（例如 binomial\_tree\_cython/）下看到一個新生成的 .pyd 文件。檔名會類似於 binomial\_tree\_cython.cp312-win\_amd64.pyd，其中 cp312 代表 CPython 3.12，win\_amd64 代表 Windows 64位元環境。 🎉

### **步驟三：部署編譯好的** .pyd **檔案到虛擬環境 🚀**

現在，需要將這些新鮮出爐的 .pyd 檔案複製到您 VnPy 項目所使用的 Python 虛擬環境的 site-packages 目錄下，對應的 vnpy\_optionmaster 路徑中，以替換掉可能存在的舊版本或不兼容版本的文件。

\# 返回到 cython\_model 的父目錄 (pricing/cython\_model/)  
cd .. 

\# 設定您的虛擬環境 site-packages 中 vnpy\_optionmaster 的 pricing 目標路徑  
\# 請根據您的實際虛擬環境路徑進行調整  
$destinationPath \= "C:\\Users\\charl\\vnpy\_inter\_shioaji\\.venv\\Lib\\site-packages\\vnpy\_optionmaster\\pricing\\"

\# 確保目標路徑存在 (如果 vnpy\_optionmaster 是通過 pip 安裝的，則通常已存在)  
\# If (\!(Test-Path $destinationPath)) { New-Item \-ItemType Directory \-Path $destinationPath \-Force | Out-Null }

\# 複製 Binomial Tree 模型  
Copy-Item .\\binomial\_tree\_cython\\\*.cp312-win\_amd64.pyd $destinationPath \-Force  
Write-Host "Copied Binomial Tree model."

\# 複製 Black 76 模型  
Copy-Item .\\black\_76\_cython\\\*.cp312-win\_amd64.pyd $destinationPath \-Force  
Write-Host "Copied Black 76 model."

\# 複製 Black-Scholes 模型  
Copy-Item .\\black\_scholes\_cython\\\*.cp312-win\_amd64.pyd $destinationPath \-Force  
Write-Host "Copied Black-Scholes model."

🔄 使用 \-Force 參數可以確保覆蓋目標路徑中任何同名的舊 .pyd 檔案（例如，來自 pip 安裝的 cp310 版本）。

### **步驟四：清理 Python 快取並驗證導入 🚩**

1. 刪除可能的 \_\_pycache\_\_：  
   為了確保 Python 加載的是新的 .pyd 文件而不是舊的快取字節碼，建議刪除目標路徑下的 \_\_pycache\_\_ 文件夾。  
   $pycachePath \= Join-Path $destinationPath "\_\_pycache\_\_"  
   If (Test-Path $pycachePath) {  
       Remove-Item $pycachePath \-Recurse \-Force  
       Write-Host "Removed $pycachePath."  
   }

2. 互動式 Python 測試導入：  
   打開 PowerShell 或 Cmd，激活您的虛擬環境，然後嘗試單獨導入每個模塊，確認沒有錯誤。  
   \# 確保虛擬環境已激活  
   \# 例如: C:\\Users\\charl\\vnpy\_inter\_shioaji\\.venv\\Scripts\\activate

   python \-c "import vnpy\_optionmaster.pricing.binomial\_tree\_cython as bt; print(f'Successfully imported {bt.\_\_name\_\_}')"  
   python \-c "import vnpy\_optionmaster.pricing.black\_76\_cython as b76; print(f'Successfully imported {b76.\_\_name\_\_}')"  
   python \-c "import vnpy\_optionmaster.pricing.black\_scholes\_cython as bs; print(f'Successfully imported {bs.\_\_name\_\_}')"

   如果沒有出現 ImportError 或其他錯誤，並且打印出成功信息，說明 .pyd 文件已正確放置並可以被 Python 識別。  
3. **執行您的 VnPy 主程式**：  
   \# 返回到您的 VnPy 專案根目錄  
   cd C:\\Users\\charl\\vnpy\_inter\_shioaji  
   \# 運行您的 VnPy 應用 (假設使用 uvicorn 配合 run.py)  
   uv run python run.py   
   \# 或者直接 python run.py，取決於您的啟動方式

若 VnPy 啟動且運行期權相關功能時不再出現「Failed to import cython option pricing model」的錯誤，那麼恭喜您，手動編譯和部署已成功完成！🎊

## **附註 (Cython 編譯) 📖**

* 如果在導入時仍然遇到問題 (例如 ImportError: DLL load failed)，可能是缺少某些運行時庫。請確保您的 Microsoft C++ Build Tools 安裝完整，或者嘗試安裝對應的 Microsoft Visual C++ Redistributable。  
* 在 vnpy\_optionmaster 的 pricing 目錄下，有一個 \_\_init\_\_.py 文件，它負責嘗試導入這些 Cython 模型。如果導入失敗，它會回退到純 Python 的實現（如果有的話）或者拋出導入失敗的錯誤。您可以在此文件中臨時加入 try-except 和 traceback.print\_exc() 來獲取更詳細的錯誤堆疊信息，幫助定位問題。  
* 考慮到未來升級或環境遷移的便利性，您可以將上述編譯和複製的 PowerShell 命令整合成一個腳本（例如 compile\_optionmaster.ps1），或者寫入 Makefile (如果您的開發環境支持 make)，以便一鍵完成編譯和部署。

希望這份 README.md 對您和您的 Gateway 使用者有所幫助！