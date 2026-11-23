"""假的 OpenAI 相容 LLM 端點，只用來驗證「LLM 寫卡」的串接與防編造檢查。
MOCK_MODE=good：只用事實裡的數字；MOCK_MODE=bad：故意編一個數字（應被擋下改用模板）"""
import json, os, re
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        facts = json.loads(body["messages"][1]["content"])
        if os.environ.get("MOCK_MODE", "good") == "bad":
            cause = "可能是 GPU 記憶體在 3 天前降到 12345 MB 造成。"
        else:
            first = (facts["事實"] or ["（無）"])[0]
            cause = f"（模擬 LLM）根據事實「{first[:40]}」，可能是上游流程變動造成，需要人確認。"
        msg = json.dumps({"likely_cause": cause, "next_steps": ["請負責同仁依證據 SQL 抽查個案", "確認同期是否有系統異動"]}, ensure_ascii=False)
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": msg}}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", int(os.environ.get("MOCK_PORT", "9009"))), H).serve_forever()
