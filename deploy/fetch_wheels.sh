#!/usr/bin/env bash
# 下載 Docker 映像要用的離線套件（Linux x86_64、Python 3.11）到 deploy/wheels/
# 在能上網的機器執行一次，再把整個資料夾帶到部署主機；部署時不需要網路
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p wheels
python3 -m pip download --dest wheels --only-binary=:all: \
  --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 --platform manylinux_2_17_x86_64 \
  --python-version 3.11 --implementation cp \
  'wrenai[postgres,mcp]==0.18.0'
echo "完成：$(ls wheels | wc -l) 個套件在 deploy/wheels/"
