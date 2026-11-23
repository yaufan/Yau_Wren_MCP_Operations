#!/bin/sh
# 啟動流程：註冊連線設定（密碼用 ${變數} 從環境帶入，不落地）→ 編譯語意層 → 啟動 MCP（HTTP、唯讀）
set -e
cd /app/wren_project
for v in WREN_PG_HOST WREN_PG_PORT WREN_PG_DB WREN_PG_USER WREN_PG_PASSWORD; do
  eval "val=\${$v:-}"
  [ -n "$val" ] || { echo "缺少環境變數 $v（請檢查 .env）"; exit 1; }
done
wren profile add aicloud --from-file /app/profile.yml --no-validate >/dev/null
wren context build
echo "測試資料庫連線…"
wren --sql "SELECT current_setting('transaction_read_only') AS read_only, COUNT(*) AS models FROM model_status" || { echo "資料庫連線或權限有問題，請看上面的錯誤"; exit 1; }
# 只聽容器內網路，對外一律經過 gateway（nginx 檢查 token）
exec wren serve mcp --transport http --host 0.0.0.0 --port 8000 --quiet
