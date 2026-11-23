# 推論平台「自動找問題」（Explorer）：模擬版

目的：不靠人一條一條設門檻，系統每天自己把資料翻一遍。它找出「跟平常不一樣」的地方，往下追查原因，寫成附證據的發現卡。它也會記得哪些問題已經講過、人怎麼回覆，每天只推真正新的或變嚴重的。

這一版全部跑在**模擬資料**上：資料庫結構與正式庫相同，並刻意埋了 11 個已知問題。它用來驗證整套架構是否串得起來，也用來量命中率。

## 架構

```
排程（每天一次，離峰）
   │
   ▼
① 偵測器（10 種通用檢查，每個模型／院區／檢查代碼都跟自己的過去比）
   │      全部只透過 Wren MCP 的 run_sql，唯讀
   ▼
② 記憶（registry.db）：這件事以前報過嗎？人說是已知、誤報，還是還沒處理？
   │      只有「新的、惡化的、復發的」才往下走
   ▼
③ 調查員：依劇本追查（每日趨勢與變化點、院區分布、主要原因、最後一筆時間……）
   │      寫手：模板（預設）或自架 LLM（只寫敘述；出現證據以外的數字就退回模板）
   ▼
④ 每日摘要（Markdown）：發現卡 + 證據 SQL；持續中、已略過、已恢復各列一行
   │
   ▼
⑤ 人回饋：python run.py feedback <代號> known|false_positive "備註" → 回到 ②
```

## 監控頁面

```bash
uvicorn explorer.web:app --host 0.0.0.0 --port 8090      # 讀 state/，不連資料庫
```

頁面有四個分頁：
- **今日**：需要看、持續中、已略過、已恢復，以及各偵測器是否正常
- **各模型健康**：模型 × 院區的格子
- **全部發現**：可篩選、可搜尋
- **歷史**：每次件數與每件的生命週期

點任何一件發現會打開發現卡，可以直接標「已知」或「誤報」，回饋會寫進登記簿。設了 `EXPLORER_WEB_TOKEN` 就要輸入通行碼才能進入。

`python tools/build_demo.py out.html` 會把模擬結果打包成一個獨立的示範網頁。`scheduler.py` 是每日排程，給容器用。正式部署見 `DEPLOY.md`。

## 10 種偵測器

| 偵測器 | 找什麼 | 怎麼判斷 |
|---|---|---|
| 比例偏移 | NFI／失敗率變高 | 近 7 天與昨天各跟前 28 天比（兩比例 z 檢定）；子切片要比同模型其他切片明顯高，才算局部問題 |
| 量驟降 | 某院區、類型或模型突然沒資料 | 跟基準期同類日（平日／假日）平均比，Poisson；往下找出佔 90% 短少量的最小切片 |
| 新訊息 | 以前沒出現過的 NFI／錯誤訊息 | 數字正規化後，比對首次出現日 |
| 新類別值 | 新的模型、院區、狀態碼 | 首次出現在近 7 天內 |
| 不合理時間 | 檢查時間在未來或很久以前 | 固定規則 |
| 應跑模型數 | 例如心電圖應跑 3 個模型，卻只跑了 2 個 | 從基準期學出「應跑幾個」，再比近期少跑的比例 |
| 未登記模型 | 有推論、但 model_status 沒有登記 | 兩表比對 |
| 耗時偏移 | 推論耗時 P90 變長 | 近期與基準期的比值 |
| 卡住 | 處理中超過 24 小時 | 固定規則 |
| NFI 原因組成 | NFI 的原因分布改變 | 總變異距離（TVD） |

統計門檻都在 `config.yaml`。這些是統計門檻（「跟自己比差多少」），不是業務門檻。

## 模擬結果（雲端實跑）

| 項目 | 結果 |
|---|---|
| 第 1 天：埋入 10 個問題 | 找到 10 個，沒有多報 |
| 第 2 天：人回饋 3 件（2 件已知、1 件誤報），並新埋 1 個問題 | 需要看的只剩 3 件，都是新問題（同一根因的 3 個面向，卡片互相標註）；持續中 8 件只列一行；略過 3 件 |
| 誤報測試：完全不埋問題，12 組亂數 | 11 組 0 件；1 組報 1 件（模擬資料剛好連 2 天量很少，資料上確實異常） |
| 變化點（起始日）估計 | CLIN_Bleeding_Risk、IMG_Abdominal_Trauma 都抓到正確日期；IMG_Dental_Pano×S11 差 1 天 |
| 速度 | 每次巡檢約 20～35 句 SQL，模擬資料（7 萬筆推論）約 2 秒 |

**注意：** 偵測器和埋的問題是同一個人設計的，所以 100% 是偏樂觀的上限。真正的考驗是正式資料：先挑幾個已經知道的真實問題，看系統有沒有抓到。

## 檔案

```
run.py                   主程式（scan／feedback／list／evaluate）
config.yaml              統計門檻
explorer/wren_client.py  MCP 連線（唯一碰資料的出口；每句 SQL 都記錄耗時與筆數）
explorer/detectors.py    10 種偵測器
explorer/registry.py     記憶（SQLite）
explorer/investigator.py 追查劇本、變化點、寫手（模板／LLM）與防編造檢查
explorer/digest.py       每日摘要
sim/generate.py          模擬資料產生器（day1／day2／clean）與標準答案
tools/mock_llm.py        假的 LLM 端點，只用來測 LLM 串接與防編造
run_demo.sh              一鍵重現兩天模擬
scheduler.py             每日排程
explorer/web.py          監控頁面（API）
explorer/webdata.py      頁面資料（只讀 state/）
explorer/static/         頁面本身
tools/build_demo.py      產生示範版網頁
```

## 自己重跑模擬（Linux，需要 Postgres + Wren MCP）

```bash
pip install 'wrenai[postgres,mcp]==0.18.0' pyyaml     # 已有 Wren venv 就不用
# 1) 建一個「測試用」Postgres 資料庫（例如 sim_infer）與一個唯讀帳號
# 2) 用本 repo 的 wren_project 啟動 Wren MCP，profile 指到模擬庫（範例：sim/wren_sim_profile.yml）
cd ../wren_project && wren profile add sim --from-file ../explorer/sim/wren_sim_profile.yml --no-validate
#    把 wren_project.yml 的 profile 改成 sim，然後：
wren context build && wren serve mcp --transport http --host 127.0.0.1 --port 8000 &
# 3) 跑兩天模擬
cd ../explorer && export EXPLORER_MCP_URL=http://127.0.0.1:8000/mcp
SIM_DSN="host=... dbname=sim_infer user=..." SIM_GRANT_TO=<唯讀帳號> ./run_demo.sh
```

模擬資料只能灌進測試用資料庫，**絕對不要指向正式庫**：產生器會先 DROP 再重建三張表。

## 接正式資料（下一步，需要人決定何時開始）

1. Wren MCP（本 repo 的 deploy/）本來就是唯讀。巡檢只送 SELECT，沒有任何寫入。容器部署方式見 `DEPLOY.md`。
2. 設定好以下環境變數後，執行 `python run.py scan`：
   - `EXPLORER_MCP_URL=http://<主機>:18080/mcp`
   - `EXPLORER_MCP_TOKEN=<token>`：用環境變數帶入，不要寫進檔案
3. 第一次跑會把既有問題全部報成「新」。這是正常的：人花一次時間把已知問題標成 known，之後就只剩新東西。
4. 建議離峰執行（例如清晨），或之後改成對快照執行。
5. 要用自架 LLM 寫卡時，再設定 `EXPLORER_LLM_BASE_URL` 和 `EXPLORER_LLM_MODEL`（OpenAI 相容）。病人資料只送自架模型，不使用雲端模型。

## 已知限制

- 「量驟降」看的是到昨天為止的完整日；當天的中斷要隔天才會報（即時中斷仍由現有 Supervisor 處理）。
- 同一個根因可能觸發好幾種偵測器（例如新訊息、比例偏移、原因組成）。目前用「同一模型互相標註」串起來，還沒合併成一張卡。
- 調查員是固定劇本，沒有讓 LLM 自己決定要查什麼。這是刻意的：先求可重現、可稽核，之後再評估要不要讓 LLM 自由追查。
- 正式庫的 info_log 如果含有大量不重複字串，「新訊息」查詢可能被截斷。截斷時該偵測器會在摘要裡標示錯誤，不會靜默漏報。
