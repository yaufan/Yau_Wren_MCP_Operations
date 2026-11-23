#!/usr/bin/env bash
# 含自動巡檢的 docker compose 包裝，例：bash dcx.sh logs -f explorer ｜ bash dcx.sh exec explorer python run.py scan
set -euo pipefail
cd "$(dirname "$0")"
export WREN_PG_PASSWORD="$(head -n1 ../secrets/db_password.txt | tr -d '\r\n')"
export MCP_TOKEN="$(cat ../secrets/mcp_token.txt 2>/dev/null || echo not-yet-generated)"
export EXPLORER_WEB_TOKEN="$(cat ../secrets/explorer_web_token.txt 2>/dev/null || echo not-yet-generated)"
exec docker compose -f docker-compose.yml -f docker-compose.explorer.yml "$@"
