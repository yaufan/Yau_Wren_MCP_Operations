#!/bin/sh
# 背景跑每日排程，前景跑監控頁面
set -e
[ -n "${EXPLORER_MCP_TOKEN:-}" ] || { echo "缺少 EXPLORER_MCP_TOKEN"; exit 1; }
[ -n "${EXPLORER_WEB_TOKEN:-}" ] || { echo "缺少 EXPLORER_WEB_TOKEN（監控頁面通行碼）"; exit 1; }
python /app/explorer/scheduler.py &
exec uvicorn explorer.web:app --host 0.0.0.0 --port 8090 --no-access-log
