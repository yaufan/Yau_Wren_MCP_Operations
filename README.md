# Wren MCP Operations：AI 推論平台的語意層、MCP 與自動巡檢

這個 repo 把一個「AI 推論平台」的營運資料庫，用 [Wren AI](https://github.com/Canner/WrenAI)（開源版 `wrenai` 0.18.0）包成唯讀的語意層與 MCP 服務。在這之上做了一套**自動找問題**的巡檢系統，並附一個監控頁面。

> 本 repo 已去識別。主機位址、帳號、資料庫名稱、院區代碼、模型名稱都換成通用名稱，例如：
> - 院區：`S01`～`S11`
> - 模型：`ECG_AFib`、`CLIN_Bleeding_Risk`
> - 資料庫主機：`db.example.internal`
>
> 所有範例資料都是**模擬資料**。密碼與 token 一律不進 git（見 `secrets/README.txt`）。

## 架構

```
推論平台 PostgreSQL（唯讀連線）
        │
        ▼
Wren 語意層（wren_project/）── 狀態分類、排除測試資料、耗時欄位、查詢規則
        │
        ▼
Wren MCP（deploy/：Docker + nginx 閘道，Bearer token、存取紀錄）
        │                                   │
        ▼                                   ▼
Agent 平台（MCP 工具，自然語言問答）     自動巡檢 explorer/
                                            ① 10 種通用偵測器：每個切片跟自己的過去比
                                            ② 記憶：已知／誤報／已恢復，只報新的、惡化、復發
                                            ③ 調查員：自動往下追查，寫成附 SQL 證據的發現卡
                                            ④ 每日摘要＋監控頁面（人可直接回饋）
```

## 資料夾

| 路徑 | 內容 |
|---|---|
| `wren_project/` | 語意層：模型、關聯、查詢規則（`knowledge/rules/business-rules.md`） |
| `deploy/` | Wren MCP 與 explorer 的 Dockerfile、docker-compose、nginx 閘道、部署腳本（Linux `.sh`／Windows `.bat`） |
| `tests/` | 驗收腳本（4 層）與 19 題測試題：每題 Wren 結果都和直查資料庫的結果比對 |
| `db/` | 建立正式唯讀帳號與檢視表的 SQL，由資料庫擁有者執行 |
| `explorer/` | 自動巡檢：偵測器、記憶、調查員、監控頁面、排程、模擬資料與評估。說明見 `explorer/README.md`，部署見 `explorer/DEPLOY.md` |
| `secrets/` | 只有說明檔；實際的密碼與 token 自行建立，不進 git |

## 快速開始

### 1. Wren MCP

```bash
bash deploy/fetch_wheels.sh                 # 在能上網的機器下載離線套件（約 210 MB）
cp deploy/.env.example deploy/.env          # 填資料庫主機、資料庫名稱、唯讀帳號
echo '<資料庫密碼>' > secrets/db_password.txt && chmod 600 secrets/db_password.txt
cd deploy && bash deploy.sh                 # 建置、啟動、驗收（預期 30 項全過）
cat ../secrets/mcp_token.txt                # Agent 平台設定 MCP 工具要用的 token
```

在 Agent 平台新增 MCP 工具時，填這兩項：
- 網址：`http://<主機>:18080/mcp`
- 標頭：`{"Authorization": "Bearer <token>"}`

### 2. 自動巡檢與監控頁面

```bash
cd deploy && bash explorer_up.sh            # 監控頁面在 http://<主機>:18090/；每天 05:30 巡檢
```

### 3. 只想看看：用模擬資料跑一次

見 `explorer/README.md`。模擬資料會埋入 10 個已知問題，第 2 天再多埋 1 個，跑完自動計算命中率。也可以直接用瀏覽器打開下面這個示範頁面（資料內嵌，不需要伺服器）：

```
explorer/examples/demo_dashboard.html
```

## 模擬結果

| 項目 | 結果 |
|---|---|
| 第 1 天，埋入 10 個問題 | 10 個都找到，沒有多報 |
| 第 2 天，人回饋 3 件後新埋 1 個問題 | 需要人看的只剩 3 件，都是這個新問題的不同面向 |
| 完全不埋問題，換 12 組亂數 | 11 組報 0 件；1 組報 1 件，那組資料確實有異常 |
| 每次巡檢 | 約 33 句 SQL，模擬資料約 2 秒 |

偵測器和埋入的問題是同一個人設計的，所以這是偏樂觀的上限。真正的考驗是正式資料。

## 安全設計

- **連線一律唯讀**：連線參數強制 `default_transaction_read_only=on`，Wren 引擎也會擋下非 SELECT 的語句。巡檢只送 SELECT。
- **MCP 只能從閘道進**：nginx 檢查 Bearer token，每次呼叫都記在 `deploy/logs/wren_access.log`。
- **監控頁面要通行碼**：頁面不連資料庫，只讀巡檢結果；唯一的寫入是人的回饋。
- **LLM 不能編數字**：寫卡時 LLM 只負責敘述，事實清單一律由程式產生。LLM 寫出證據以外的數字，就自動退回模板。
- **模擬資料產生器不進正式映像**：它會刪表重建，所以 `Dockerfile.explorer` 不複製 `explorer/sim`。

## 目前狀態

| 項目 | 狀態 |
|---|---|
| 語意層與驗收（30 項） | 以模擬資料庫驗證通過 |
| 巡檢與監控頁面 | 程式已實測：走閘道帶 token、排程、通行碼、回饋 |
| Docker 映像建置 | 只驗證了 compose 設定能合併，尚未實際建置 |

尚未接正式資料。
