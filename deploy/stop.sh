#!/usr/bin/env bash
# 停止並移除 Wren 容器（不影響資料庫）
cd "$(dirname "$0")" && bash dc.sh down
