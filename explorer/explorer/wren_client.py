"""透過 Wren MCP（HTTP）執行唯讀 SQL。整套監測只經過這一個出口碰資料。"""
import json
import os
import time

import pandas as pd
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


class WrenError(RuntimeError):
    pass


class Wren:
    def __init__(self, url=None, token=None):
        self.url = url or os.environ.get("EXPLORER_MCP_URL", "http://127.0.0.1:8000/mcp")
        tok = token if token is not None else os.environ.get("EXPLORER_MCP_TOKEN", "")
        self.headers = {"Authorization": f"Bearer {tok}"} if tok else {}
        self.log = []          # 每一句 SQL 的紀錄（耗時、筆數），寫進摘要供稽核
        self._cm = self._session = None

    async def __aenter__(self):
        # 先探一次：token 錯或連不到時給清楚的訊息（MCP 函式庫本身的錯誤訊息很難讀）
        import httpx
        try:
            async with httpx.AsyncClient(timeout=15) as cli:
                r = await cli.post(self.url, headers={**self.headers, "Accept": "application/json, text/event-stream"},
                                   json={"jsonrpc": "2.0", "id": 0, "method": "ping"})
        except httpx.HTTPError as e:
            raise WrenError(f"連不到 Wren MCP：{self.url}（{type(e).__name__}）") from None
        if r.status_code == 401:
            raise WrenError("Wren MCP 拒絕連線（401）：EXPLORER_MCP_TOKEN 不對") from None
        self._cm = streamablehttp_client(self.url, headers=self.headers, timeout=120)
        rd, wr, _ = await self._cm.__aenter__()
        self._scm = ClientSession(rd, wr)
        self._session = await self._scm.__aenter__()
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc):
        await self._scm.__aexit__(*exc)
        await self._cm.__aexit__(*exc)

    async def q(self, sql, label="", limit=100000):
        t0 = time.time()
        res = await self._session.call_tool("run_sql", {"sql": sql, "limit": limit})
        text = res.content[0].text if res.content else ""
        if res.isError:
            self.log.append({"label": label, "sql": sql, "ok": False, "sec": round(time.time() - t0, 2), "error": text[:300]})
            raise WrenError(f"{label}: {text[:300]}")
        data = json.loads(text)
        df = pd.DataFrame(data.get("rows", []), columns=data.get("columns"))
        self.log.append({"label": label, "sql": sql, "ok": True, "sec": round(time.time() - t0, 2),
                         "rows": len(df), "truncated": data.get("truncated", False)})
        if data.get("truncated"):
            raise WrenError(f"{label}: 結果被截斷（超過 {limit} 筆），請縮小查詢")
        return df


def lit(s):
    """SQL 字串常值（單引號加倍）"""
    return "'" + str(s).replace("'", "''") + "'"
