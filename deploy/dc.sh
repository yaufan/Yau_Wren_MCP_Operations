#!/usr/bin/env bash
# docker compose 的包裝：先從 ../secrets 載入密碼與 token，再執行 docker compose "$@"
# 例：bash dc.sh ps ｜ bash dc.sh logs -f wren ｜ bash dc.sh exec -T wren python /app/tests/acceptance.py
set -euo pipefail
cd "$(dirname "$0")"
[ -f ../secrets/db_password.txt ] || { echo "[失敗] 找不到 ../secrets/db_password.txt"; exit 1; }
export WREN_PG_PASSWORD="$(head -n1 ../secrets/db_password.txt | tr -d '\r\n')"
[ -f ../secrets/mcp_token.txt ] && export MCP_TOKEN="$(cat ../secrets/mcp_token.txt)" || export MCP_TOKEN="(not-yet-generated)"
exec docker compose "$@"
