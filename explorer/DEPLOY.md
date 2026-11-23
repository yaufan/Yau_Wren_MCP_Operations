# 自動巡檢＋監控頁面：容器部署

部署在 Wren MCP 旁邊，會多開一個容器 `explorer`，負責兩件事：

1. **每天自動巡檢一次**：預設清晨 05:30。巡檢只透過 Wren MCP 閘道送 SELECT，所以每句查詢都會記在 `deploy/logs/wren_access.log`。
2. **監控頁面**：網址是 `http://<主機>:18090/`，要輸入通行碼才能進入。頁面有四個分頁：今日、各模型健康、全部發現、歷史。在發現卡上可以直接標「已知」或「誤報」。

映像**不含模擬資料產生器**（`explorer/sim`）：它會刪表重建，所以 `Dockerfile.explorer` 只複製正式環境要用的檔案。

## 前提

- 已經用 `deploy.sh` 或 `deploy_windows.bat` 部署好 Wren MCP，`wren-poc:0.2` 映像存在。
- `secrets/mcp_token.txt` 已經產生。

explorer 映像直接沿用 `wren-poc:0.2` 建置，所以不需要再下載任何套件。

## 啟動

| 主機 | 指令 |
|---|---|
| Linux | `cd deploy && bash explorer_up.sh` |
| Windows | 雙擊 `deploy\explorer_up.bat` |

第一次啟動時會自動產生監控頁面的通行碼，存在 `secrets/explorer_web_token.txt`。這個檔案和其他機密一樣，不要分享到群組，也不要放進 git。

**預設不會在啟動當下就巡檢**，第一次巡檢會等到隔天 05:30。要馬上跑一次，用下面的指令（會打正式庫，請挑離峰時間）：

| 主機 | 指令 |
|---|---|
| Linux | `bash dcx.sh exec explorer python run.py scan` |
| Windows | `dcx.bat exec explorer python run.py scan` |

## 日常

| 要做的事 | 指令（Windows 把 `bash dcx.sh` 換成 `dcx.bat`） |
|---|---|
| 看排程與巡檢紀錄 | `bash dcx.sh exec explorer cat state/scan.log` |
| 看容器紀錄 | `bash dcx.sh logs -f explorer` |
| 改巡檢時間 | 設定環境變數 `EXPLORER_SCAN_AT=04:30` 後重新執行 `explorer_up` |
| 改統計門檻 | 改 `explorer/config.yaml` 後重新執行 `explorer_up`（會重建映像） |
| 停掉巡檢 | `bash dcx.sh stop explorer`（Wren MCP 不受影響） |

登記簿和每天的巡檢結果存在 Docker volume `explorer_state` 裡，重建容器不會不見。

## 第一次接正式庫時

1. **第一次會把既有問題全部報成「新」。** 這是正常的。花一次時間，在頁面上把已知的問題標成「已知」並寫一句原因，之後每天就只會報新的、惡化的、復發的。
2. **用已經知道的真實問題對答案**，例如某個模型 NFI 長期偏高、有模型沒登記在 model_status、檢查時間在未來：確認系統都有抓到。
3. **如果某個偵測器在頁面上顯示「錯誤」**，例如查詢被截斷（正式庫的 info_log 太多種），調整 `explorer/detectors.py` 裡對應的查詢。

## 錯誤訊息

| 訊息 | 原因 |
|---|---|
| `[失敗] Wren MCP 拒絕連線（401）` | `secrets/mcp_token.txt` 和閘道用的 token 不一致；重跑 `explorer_up` |
| `[失敗] 連不到 Wren MCP` | gateway 容器沒在跑；先執行 `bash dcx.sh ps` 確認 |
| 頁面一直要求輸入通行碼 | 通行碼在 `secrets/explorer_web_token.txt` |
