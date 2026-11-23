"""把 state/ 的模擬結果打包成一個獨立的示範網頁（資料內嵌、回饋只存在頁面記憶體）。
用法：python tools/build_demo.py <輸出檔.html>"""
import json, sys
from pathlib import Path
import yaml
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from explorer.webdata import Data
cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
b = Data(HERE / "state", cfg).bundle()
html = (HERE / "explorer/static/index.html").read_text(encoding="utf-8")
blob = json.dumps(b, ensure_ascii=False, default=str).replace("</", "<\\/")
html = html.replace("<script>\nconst S =", f"<script>window.DEMO = {blob};</script>\n<script>\nconst S =", 1)
Path(sys.argv[1]).write_text(html, encoding="utf-8")
print("ok", len(html))
