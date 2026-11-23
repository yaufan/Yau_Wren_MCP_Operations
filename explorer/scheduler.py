"""每天定時巡檢一次（給容器用；不想用 cron 時的簡單排程）。

環境變數：
  EXPLORER_SCAN_AT        每天幾點巡檢，24 小時制 HH:MM，預設 05:30（離峰）
  EXPLORER_SCAN_ON_START  設成 1 時，啟動後先立刻巡檢一次（預設不跑，避免部署當下就打正式庫）
"""
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("EXPLORER_STATE", HERE / "state"))
AT = os.environ.get("EXPLORER_SCAN_AT", "05:30")


def log(msg):
    STATE.mkdir(parents=True, exist_ok=True)
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(STATE / "scan.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def next_run(now):
    h, m = (int(x) for x in AT.split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t > now else t + dt.timedelta(days=1)


def scan():
    lock = STATE / "scan.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 3600:
        log("上一次巡檢還沒結束，這次跳過")
        return
    lock.touch()
    try:
        log("開始巡檢")
        r = subprocess.run([sys.executable, str(HERE / "run.py"), "scan"], cwd=HERE, capture_output=True, text=True, timeout=3600)
        for line in (r.stdout + r.stderr).strip().splitlines()[-15:]:
            log("  " + line)
        log("巡檢完成" if r.returncode == 0 else f"巡檢失敗（代碼 {r.returncode}），請看上面的訊息")
    except subprocess.TimeoutExpired:
        log("巡檢超過 1 小時，已中止")
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    log(f"排程啟動：每天 {AT} 巡檢")
    if os.environ.get("EXPLORER_SCAN_ON_START") == "1":
        scan()
    while True:
        now = dt.datetime.now()
        nr = next_run(now)
        log(f"下次巡檢：{nr:%Y-%m-%d %H:%M}")
        time.sleep(max(1, (nr - now).total_seconds()))
        scan()
