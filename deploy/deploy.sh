#!/usr/bin/env bash
# Wren MCP 部署（Linux / macOS 的 Docker 主機）
# 用法：cd deploy && bash deploy.sh
# 需要：../secrets/db_password.txt（第一行＝資料庫密碼，請自行建立，權限 600）
set -euo pipefail
cd "$(dirname "$0")"
PW_FILE="../secrets/db_password.txt"
TOKEN_FILE="../secrets/mcp_token.txt"
[ -f .env ] || { echo "[失敗] 找不到 deploy/.env（複製 .env.example 後填資料庫主機等設定）"; exit 1; }
[ -f "$PW_FILE" ] || { echo "[失敗] 找不到 $PW_FILE（第一行放資料庫密碼）"; exit 1; }
export WREN_PG_PASSWORD="$(head -n1 "$PW_FILE" | tr -d '\r\n')"
if [ ! -f "$TOKEN_FILE" ]; then
  (umask 077; head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$TOKEN_FILE")
  echo "已產生 MCP token：$TOKEN_FILE（給 MaiAgent 設定用，請妥善保管）"
fi
export MCP_TOKEN="$(cat "$TOKEN_FILE")"
mkdir -p logs
echo "== 建置並啟動 =="
docker compose up -d --build
echo "== 等待 wren 健康檢查（最多 3 分鐘）=="
for i in $(seq 1 36); do
  s=$(docker inspect -f '{{.State.Health.Status}}' "$(docker compose ps -q wren)" 2>/dev/null || echo starting)
  [ "$s" = "healthy" ] && break; sleep 5
done
docker compose ps
echo "== 驗收 =="
docker compose exec -T wren python /app/tests/acceptance.py | tee "logs/acceptance_$(date +%Y%m%d_%H%M).log"
