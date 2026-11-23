#!/bin/bash
# 模擬兩天：第 1 天巡檢 → 人回饋 → 第 2 天巡檢（多一個新問題）→ 兩天都跟標準答案比
# 前提：Postgres（SIM_DSN 可寫入的庫）與 Wren MCP（EXPLORER_MCP_URL，profile 指向同一個庫）都已啟動
set -e
cd "$(dirname "$0")"
: "${SIM_DSN:?請設定 SIM_DSN，例如 host=/tmp dbname=sim_infer user=postgres}"
GRANT_TO="${SIM_GRANT_TO:-wren_reader}"
gen() { python sim/generate.py --dsn "$SIM_DSN" --asof "$1" --scenario "$2"
        psql "$SIM_DSN" -qc "GRANT SELECT ON ALL TABLES IN SCHEMA public TO $GRANT_TO"; }
rm -rf state
echo "== 第 1 天 =="; gen 2026-11-22 day1
python run.py scan --asof 2026-11-22 --asof-time 08:00
python run.py evaluate --gt sim/ground_truth_day1.json --asof 2026-11-22 > /dev/null
echo "== 人看完第 1 天摘要後回饋 =="
python run.py feedback "rate_shift|model=CLIN_Bleeding_Risk" known "臨床端 90 天內缺 Vital sign，已請資訊處追" --who 值班同仁
python run.py feedback "invalid_time" known "來源系統舊測試資料，不影響臨床" --who 值班同仁
python run.py feedback "new_category|inference_status=3" false_positive "狀態 3 是正常中間狀態，跟「卡住」那張重複" --who 值班同仁
echo "== 第 2 天（多埋一個新問題 G11）=="; gen 2026-11-23 day2
python run.py scan --asof 2026-11-23 --asof-time 08:00
python run.py evaluate --gt sim/ground_truth_day2.json --asof 2026-11-23 | sed -n 3p
python run.py list
