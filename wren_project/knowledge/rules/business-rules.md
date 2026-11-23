# 推論平台查詢規則

## 用哪張表
- 預設一律查 `inferences`。它已排除心室收縮功能模型的早期測試資料（狀態碼 success／error／invalid）與整合／測試模型殘留（模型名稱 `IT_` 或 `TEST` 開頭，例如 TEST_model、TEST_AI_MODEL、Test_Model_v1.0），並帶好院區、時間、狀態分類與各段耗時。
- 只有使用者明確說「包含測試資料」「原始資料」「稽核」時才查 `inferences_all`；回答時要說明數字包含測試資料。
- 問「檢查量」「幾份心電圖」「幾位病人」時：同一份檢查會被多個模型推論（每份心電圖都跑 3 個心電圖模型），所以要用 `COUNT(DISTINCT exam_id)` 或查 `exams`，不能直接數推論筆數。
- 問「推論量」「模型被呼叫幾次」時，才是數 `inferences` 的筆數。
- 目前版本沒有病人層級的鍵（不提供病歷號），回答不了「幾位病人」；請說明只能回答檢查數與推論數。

## 狀態怎麼判斷
- 一律用 `status_category`，不要直接比對原始 `inference_status`。
  - 成功：新版 `4.2`、舊版 `5`（兩者同義：數據與圖片都已上傳）
  - 不適用：`NFI`（資料規格不符合模型，不是系統錯誤）
  - 失敗：`2E`～`8E`
  - 處理中：其他數字，例如 `2`、`3`、`4.1`（舊版 `4`）
  - 測試期：`success`、`error`、`invalid`（只在 `inferences_all` 出現）
- 成功率＝`AVG(is_success)`，NFI 比例＝`AVG(is_nfi)`；顯示百分比時乘 100 並四捨五入到小數 1 位。
- 「卡住」：`status_category = '處理中'` 且 `platform_time` 距今超過 1 小時（門檻可依使用者要求調整）。
- 每筆推論只保存「最後狀態」，沒有歷程；回答不了「重跑幾次」「哪天暫停過」這類問題，請直接說明。

## 資料品質
- 少數紀錄的檢查時間在未來（例如 2177 年），屬資料錯誤；做依月份或趨勢的統計時，加上 `study_time <= CURRENT_TIMESTAMP` 排除，並在回答中說明。
- 院區 `Unk` 代表未知院區，照實列出，不要合併到其他院區。

## 時間
- 所有時間欄位都是台北時間、不帶時區。
- 依月統計用 `study_month`（字串 YYYY-MM）；依日用 `study_date`。
- 不要對 `study_date`（DATE 型別）做 `DATE_TRUNC`，結果會被轉成 UTC 而差 8 小時；要截斷請對 `study_time` 做。
- 預設以「檢查時間 study_time」界定期間；使用者說「進平台」時改用 `platform_time`。
- 日期區間用 `>=` 起日、`<` 迄日隔天，包含整個迄日。

## 耗時
- `infer_seconds`：進平台 → 結果寫入倉儲；`image_upload_seconds`：結果寫入 → 圖片上傳完成；`end_to_end_seconds`：檢查 → 圖片上傳完成。
- 算耗時只看 `status_category = '成功'` 的資料（NFI 沒有寫入時間）。
- 百分位數用 `percentile_cont(0.9) WITHIN GROUP (ORDER BY 欄位)`，中位數用 `median(欄位)`；不要用 `approx_percentile_cont`（資料庫不支援）。

## 模型狀態
- 模型目前狀態在 `model_status`，用 `model` 欄位與 `inferences.model` 對應（LEFT JOIN）。
- 有些模型有推論紀錄但不在 `model_status`（例如CLIN_Bleeding_Risk），結果會是空值，請照實呈現，不要省略。
- `ctrl_status` 是管理端下的指令；只有 `pause`、`stop` 會讓模型停止處理。

## 回答方式
- 先給數字，再用一句話說明計算方式（用了哪張表、排除了什麼）。
- 結果超過 20 列時先彙總，不要整批列出。
- `info_log` 是自由文字，要分類 NFI 原因時用 `GROUP BY info_log` 並取前 10 名。
