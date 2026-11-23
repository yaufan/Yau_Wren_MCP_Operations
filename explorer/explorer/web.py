"""監控頁面（Starlette）。只讀 state/ 裡巡檢留下的結果；唯一的寫入是人的回饋（寫進登記簿）。

啟動：uvicorn explorer.web:app --host 0.0.0.0 --port 8090
環境變數：
  EXPLORER_STATE      登記簿與巡檢結果的資料夾（預設 ./state）
  EXPLORER_WEB_TOKEN  有設定時，進頁面要先輸入這組通行碼（院內網路也建議設）
"""
import hmac
import os
from pathlib import Path

import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from .webdata import Data

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("EXPLORER_STATE", HERE.parent / "state"))
CFG = yaml.safe_load((HERE.parent / "config.yaml").read_text(encoding="utf-8"))
TOKEN = os.environ.get("EXPLORER_WEB_TOKEN", "")
data = Data(STATE, CFG)


def authed(req: Request):
    if not TOKEN:
        return True
    got = req.cookies.get("xt") or req.headers.get("x-token") or ""
    return hmac.compare_digest(got, TOKEN)


def guard(fn):
    async def inner(req: Request):
        if not authed(req):
            return JSONResponse({"error": "需要通行碼"}, status_code=401)
        return await fn(req)
    return inner


async def index(req):
    return FileResponse(HERE / "static" / "index.html", headers={"Cache-Control": "no-store"})


async def login(req: Request):
    body = await req.json()
    if not TOKEN or hmac.compare_digest(str(body.get("token", "")), TOKEN):
        r = JSONResponse({"ok": True})
        if TOKEN:
            r.set_cookie("xt", TOKEN, httponly=True, samesite="strict", max_age=30 * 86400)
        return r
    return JSONResponse({"error": "通行碼不對"}, status_code=401)


@guard
async def api_day(req):
    return JSONResponse(data.day(req.query_params.get("date")))


@guard
async def api_grid(req):
    return JSONResponse(data.grid(req.query_params.get("date")))


@guard
async def api_findings(req):
    return JSONResponse(data.findings())


@guard
async def api_card(req):
    c = data.card(req.query_params.get("sig", ""))
    return JSONResponse(c) if c else JSONResponse({"error": "找不到"}, status_code=404)


@guard
async def api_history(req):
    return JSONResponse(data.history())


@guard
async def api_feedback(req: Request):
    b = await req.json()
    sig, status = str(b.get("sig", "")), str(b.get("status", ""))
    note, who = str(b.get("note", "")).strip()[:500], str(b.get("who", "")).strip()[:50]
    if status not in ("known", "false_positive", "open"):
        return JSONResponse({"error": "狀態只能是已知、誤報或改回待處理"}, status_code=400)
    if not who:
        return JSONResponse({"error": "請填回饋人"}, status_code=400)
    if status in ("known", "false_positive") and not note:
        return JSONResponse({"error": "標成已知或誤報時，請寫一句原因"}, status_code=400)
    hit = data.feedback(sig, status, note, who)
    return JSONResponse({"ok": bool(hit), "updated": hit}) if hit else JSONResponse({"error": "找不到這個代號"}, status_code=404)


app = Starlette(routes=[
    Route("/", index), Route("/api/login", login, methods=["POST"]),
    Route("/api/day", api_day), Route("/api/grid", api_grid), Route("/api/findings", api_findings),
    Route("/api/card", api_card), Route("/api/history", api_history),
    Route("/api/feedback", api_feedback, methods=["POST"]),
])
