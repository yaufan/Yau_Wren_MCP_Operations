#!/usr/bin/env bash
# 啟動自動巡檢（Linux）。前提：已用 deploy.sh 部署好 Wren MCP（wren-poc:0.2 映像已存在）
# 用法：cd deploy && bash explorer_up.sh
set -euo pipefail
cd "$(dirname "$0")"
[ -f ../secrets/db_password.txt ] || { echo "[失敗] 找不到 ../secrets/db_password.txt"; exit 1; }
[ -f ../secrets/mcp_token.txt ] || { echo "[失敗] 找不到 ../secrets/mcp_token.txt，請先跑 deploy.sh"; exit 1; }
docker image inspect wren-poc:0.2 >/dev/null 2>&1 || { echo "[失敗] 找不到映像 wren-poc:0.2，請先跑 deploy.sh"; exit 1; }
export WREN_PG_PASSWORD="$(head -n1 ../secrets/db_password.txt | tr -d '\r\n')"
export MCP_TOKEN="$(cat ../secrets/mcp_token.txt)"
if [ ! -f ../secrets/explorer_web_token.txt ]; then
  (umask 077; head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n' > ../secrets/explorer_web_token.txt)
  echo "已產生監控頁面通行碼：../secrets/explorer_web_token.txt（給要看頁面的同仁）"
fi
export EXPLORER_WEB_TOKEN="$(cat ../secrets/explorer_web_token.txt)"
docker compose -f docker-compose.yml -f docker-compose.explorer.yml up -d --build --no-deps explorer
sleep 5
docker compose -f docker-compose.yml -f docker-compose.explorer.yml ps explorer
echo
echo "監控頁面：http://<這台主機>:${EXPLORER_WEB_PORT:-18090}/（通行碼在 ../secrets/explorer_web_token.txt）"
echo "每天 ${EXPLORER_SCAN_AT:-05:30} 自動巡檢。要現在手動跑一次：bash dcx.sh exec explorer python run.py scan"
