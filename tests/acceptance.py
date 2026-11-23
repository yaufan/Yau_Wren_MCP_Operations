"""
acceptance.py — Wren 部署驗收（分 4 層，哪層壞一眼看得出來）

  L1 資料庫：wren_reader 連得上、只讀得到 wren_ro、寫不進去
  L2 Wren 引擎：直接用 wren CLI 查一句 SQL
  L3 MCP 閘道：沒帶 token 會被擋（401）；帶 token 可列出工具
  L4 語意層正確性：questions.yaml 每題的 wren_sql（走 MCP）與 raw_sql（直查資料庫）結果必須相同

在 wren 容器內執行（容器內已有所需套件）：
  docker compose exec wren python /app/tests/acceptance.py

環境變數（docker compose 已帶入）：
  WREN_PG_HOST / WREN_PG_PORT / WREN_PG_DB / WREN_PG_USER / WREN_PG_PASSWORD
  MCP_TOKEN                   nginx 檢查的 Bearer token
  ACCEPT_MCP_URL              預設 http://gateway:8080/mcp（走 nginx，含 token 檢查）
"""
import asyncio
import json
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg
import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

HERE = Path(__file__).resolve().parent
MCP_URL = os.environ.get("ACCEPT_MCP_URL", "http://gateway:8080/mcp")
TOKEN = os.environ.get("MCP_TOKEN", "")
PROJECT = os.environ.get("WREN_PROJECT_DIR", "/app/wren_project")

results = []


def report(layer, name, ok, detail=""):
    results.append((layer, name, ok))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {layer} {name}" + (f" — {detail}" if detail else ""))


def dsn():
    e = os.environ
    return (f"host={e['WREN_PG_HOST']} port={e.get('WREN_PG_PORT', '5432')} dbname={e['WREN_PG_DB']} "
            f"user={e['WREN_PG_USER']} password={e['WREN_PG_PASSWORD']} connect_timeout=10 "
            "options='-c default_transaction_read_only=on -c statement_timeout=60000 -c TimeZone=Asia/Taipei'")


MODE = os.environ.get("ACCEPT_MODE", "readonly_views")  # appdb：以 readonly_user 強制唯讀連線


# ---------------- L1 ----------------
def layer1():
    try:
        conn = psycopg.connect(dsn(), autocommit=True)
    except Exception as ex:
        report("L1", "連線資料庫", False, str(ex).splitlines()[0])
        return None
    report("L1", "連線資料庫", True)
    with conn.cursor() as cur:
        if MODE == "appdb":
            cur.execute("SHOW transaction_read_only")
            report("L1", "連線為強制唯讀", cur.fetchone()[0] == "on")
            cur.execute("SELECT count(*) FROM public.model_status")
            report("L1", "讀得到推論平台資料表", True)
            try:
                cur.execute("DELETE FROM public.model_status WHERE false")
                report("L1", "不能寫入", False, "竟然成功，請檢查連線設定")
            except Exception:
                report("L1", "不能寫入", True)
            return conn
        cur.execute("SELECT count(*) FROM information_schema.views WHERE table_schema='wren_ro'")
        n = cur.fetchone()[0]
        report("L1", "wren_ro 有 3 個 view", n == 3, f"實際 {n}")
        for sql, label in [("SELECT 1 FROM public.patient_info LIMIT 1", "讀不到原始表 public.patient_info"),
                           ("SELECT 1 FROM wren_secret.salt", "讀不到 salt"),
                           ("DELETE FROM wren_ro.model_status WHERE false", "不能寫入")]:
            try:
                cur.execute(sql)
                report("L1", label, False, "竟然成功，請檢查權限")
            except Exception:
                report("L1", label, True)
    return conn


# ---------------- L2 ----------------
def layer2():
    r = subprocess.run(["wren", "--sql", "SELECT COUNT(*) AS n FROM inferences"],
                       cwd=PROJECT, capture_output=True, text=True, timeout=120)
    ok = r.returncode == 0 and "n" in r.stdout
    report("L2", "wren CLI 查詢 inferences", ok, (r.stdout or r.stderr).strip().splitlines()[-1] if not ok else "")


# ---------------- L3 / L4 ----------------
def norm(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        if f != f:  # NaN 視同 NULL
            return None
        return round(f, 2)
    return str(v)


def rows_of(cols, rows):
    return [tuple(norm(r[c] if isinstance(r, dict) else r[i]) for i, c in enumerate(cols)) for r in rows]


async def layer3_4(conn):
    # 沒帶 token 應被擋
    async with httpx.AsyncClient(timeout=10) as c:
        try:
            r = await c.post(MCP_URL, json={})
            report("L3", "沒帶 token 會被擋", r.status_code == 401, f"HTTP {r.status_code}")
        except Exception as ex:
            report("L3", "連到 MCP 閘道", False, str(ex))
            return
    headers = {"Authorization": f"Bearer {TOKEN}"}
    try:
        async with streamablehttp_client(MCP_URL, headers=headers) as (rd, wr, _):
            async with ClientSession(rd, wr) as s:
                await s.initialize()
                tools = {t.name for t in (await s.list_tools()).tools}
                report("L3", "帶 token 可列出工具", "run_sql" in tools, f"{len(tools)} 個工具")
                if "store_query" in tools:
                    report("L3", "MCP 為唯讀（沒有 store_query）", False)
                else:
                    report("L3", "MCP 為唯讀（沒有 store_query）", True)
                for label, sql in [("查不到病歷號", "SELECT chtno FROM inferences LIMIT 1"),
                                   ("查不到原始表", "SELECT * FROM patient_info LIMIT 1"),
                                   ("不能寫入", "DELETE FROM model_status")]:
                    bad = await s.call_tool("run_sql", {"sql": sql})
                    report("L3", f"MCP {label}", bool(bad.isError) or "error" in bad.content[0].text.lower())
                if conn is None:
                    report("L4", "語意層比對", False, "L1 沒連上資料庫，略過")
                    return
                qs = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))
                for q in qs:
                    res = await s.call_tool("run_sql", {"sql": q["wren_sql"], "limit": 5000})
                    if res.isError:
                        report("L4", q["id"], False, "Wren 執行錯誤：" + res.content[0].text[:200])
                        continue
                    data = json.loads(res.content[0].text)
                    got = rows_of(data["columns"], data["rows"])
                    with conn.cursor() as cur:
                        cur.execute(q["raw_sql"])
                        cols = [d.name for d in cur.description]
                        exp = rows_of(cols, cur.fetchall())
                    report("L4", f"{q['id']} {q['nl']}", got == exp,
                           "" if got == exp else f"Wren {got[:3]} vs 直查 {exp[:3]}")
    except Exception as ex:
        report("L3", "MCP 連線", False, f"{type(ex).__name__}: {ex}")


def main():
    print(f"MCP: {MCP_URL}\nDB : {os.environ.get('WREN_PG_HOST')}:{os.environ.get('WREN_PG_PORT')}/{os.environ.get('WREN_PG_DB')}\n")
    conn = layer1()
    layer2()
    asyncio.run(layer3_4(conn))
    failed = [r for r in results if not r[2]]
    print(f"\n合計 {len(results)} 項，失敗 {len(failed)} 項")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
