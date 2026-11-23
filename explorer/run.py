"""
推論平台「自動找問題」— 主程式

  python run.py scan [--asof 2026-11-22] [--asof-time 08:00]   巡檢一次，產生每日摘要
  python run.py feedback <代號開頭> known|false_positive|open|resolved "備註"
  python run.py list                                         看登記簿
  python run.py evaluate --gt sim/ground_truth_day1.json --asof 2026-11-22   跟標準答案比，算命中率

環境變數：
  EXPLORER_MCP_URL / EXPLORER_MCP_TOKEN    Wren MCP（預設 http://127.0.0.1:8000/mcp）
  EXPLORER_LLM_BASE_URL / _MODEL / _API_KEY 院內 OpenAI 相容 LLM（沒設就用模板寫卡）
  EXPLORER_STATE                           登記簿與輸出位置（預設 ./state）
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from explorer.detectors import Ctx, run_all  # noqa: E402
from explorer.digest import render  # noqa: E402
from explorer.investigator import KIND_ZH, link_related, severity, write_card  # noqa: E402
from explorer.registry import Registry  # noqa: E402
from explorer.wren_client import Wren, WrenError  # noqa: E402

STATE = Path(os.environ.get("EXPLORER_STATE", HERE / "state"))
CFG = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))


async def scan(asof: date, asof_ts: datetime):
    STATE.mkdir(parents=True, exist_ok=True)
    reg = Registry(STATE / "registry.db", CFG)
    c = Ctx(asof, asof_ts, CFG)
    async with Wren() as w:
        findings, det_status = await run_all(w, c)
        groups = reg.classify(findings, asof)
        for f in findings:
            f["severity"], f["kind_zh"] = severity(f), KIND_ZH.get(f["kind"], f["kind"])
            reg.set_severity(f["signature"], f["severity"])
        cards = {}
        for key in ("new", "worse", "reopened"):          # 只有需要人看的才花力氣追查
            for f in groups[key]:
                cards[f["signature"]] = await write_card(w, f, c)
        link_related(cards)
        for s_, card in cards.items():
            reg.save_card(s_, card)
        mode = "發現卡由院內 LLM 撰寫敘述。" if os.environ.get("EXPLORER_LLM_BASE_URL") else "發現卡由模板撰寫（未設定 LLM）。"
        md = render(asof.isoformat(), groups, cards, det_status, w.log, mode)
        wlog = w.log
    (STATE / f"digest_{asof}.md").write_text(md, encoding="utf-8")
    grid = []
    if getattr(c, "seg", None) is not None:
        g = c.seg.groupby(["model", "site", "filetype", "procedure_code", "period"])[["n", "nfi"]].sum().reset_index()
        for (m, st, ft, pc), x in g.groupby(["model", "site", "filetype", "procedure_code"]):
            x = x.set_index("period")
            rn = int(x["n"].get("recent", 0)); rx = int(x["nfi"].get("recent", 0))
            bn = int(x["n"].get("base", 0)); bx = int(x["nfi"].get("base", 0))
            grid.append({"model": m, "site": st, "filetype": ft, "procedure_code": pc,
                         "n": rn, "nfi_rate": round(rx / rn, 4) if rn else None, "base_n": bn,
                         "base_rate": round(bx / bn, 4) if bn else None})
    (STATE / f"findings_{asof}.json").write_text(json.dumps(
        {"asof": asof.isoformat(), "run_at": datetime.now().isoformat(timespec="seconds"),
         "window": {"recent": [c.r0.isoformat(), c.r1.isoformat()], "base": [c.b0.isoformat(), c.b1.isoformat()]},
         "groups": {k: [x["signature"] for x in v] for k, v in groups.items()},
         "findings": findings, "cards": cards, "detectors": det_status, "queries": wlog, "grid": grid},
        ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"偵測 {len(findings)} 件；需要看 {len(groups['new']) + len(groups['worse']) + len(groups['reopened'])} 件"
          f"（新 {len(groups['new'])}、惡化 {len(groups['worse'])}、復發 {len(groups['reopened'])}）；"
          f"持續中 {len(groups['ongoing'])}；略過 {len(groups['suppressed'])}；已恢復 {len(groups['resolved'])}")
    print(f"摘要：{STATE / f'digest_{asof}.md'}")


def evaluate(gt_path, asof):
    gt = json.loads(Path(gt_path).read_text(encoding="utf-8"))
    data = json.loads((STATE / f"findings_{asof}.json").read_text(encoding="utf-8"))
    fs = data["findings"]

    def hit(g, f):
        m = g["match"]
        if f["kind"] not in m["kind"]:
            return False
        blob = json.dumps(f, ensure_ascii=False)
        if "model" in m and m["model"] not in json.dumps(f["entity"], ensure_ascii=False) and m["model"] not in f["title"]:
            return False
        if "site" in m and f["entity"].get("site") != m["site"]:
            return False
        if "text" in m and m["text"] not in blob:
            return False
        return True

    used, lines = set(), []
    for g in gt:
        hs = [f for f in fs if hit(g, f)]
        used |= {f["signature"] for f in hs}
        lines.append((g["id"], bool(hs), g["desc"], [f["title"] for f in hs]))
    extra = [f for f in fs if f["signature"] not in used]
    rec = sum(1 for l in lines if l[1]) / len(lines)
    out = [f"# 命中率評估 {asof}", "", f"埋入 {len(gt)} 個問題，找到 {sum(1 for l in lines if l[1])} 個：**召回率 {rec:.0%}**；"
           f"偵測總件數 {len(fs)}，其中 {len(extra)} 件不屬於任何埋入問題。", "",
           "| 編號 | 找到 | 埋入的問題 | 對應的偵測 |", "|---|---|---|---|"]
    out += [f"| {i} | {'✅' if ok else '❌'} | {d} | {'<br>'.join(t) if t else '—'} |" for i, ok, d, t in lines]
    if extra:
        out += ["", "## 不屬於埋入問題的偵測（要人判斷：誤報，或是模擬資料裡自然出現的現象）", ""] + [f"- {f['title']}" for f in extra]
    txt = "\n".join(out) + "\n"
    (STATE / f"evaluation_{asof}.md").write_text(txt, encoding="utf-8")
    print(txt)


def main():
    ap = argparse.ArgumentParser()
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("scan"); s.add_argument("--asof", default=date.today().isoformat()); s.add_argument("--asof-time", default=None)
    f = sp.add_parser("feedback"); f.add_argument("sig"); f.add_argument("status"); f.add_argument("note", nargs="?", default=""); f.add_argument("--who", default="命令列")
    sp.add_parser("list")
    e = sp.add_parser("evaluate"); e.add_argument("--gt", required=True); e.add_argument("--asof", required=True)
    a = ap.parse_args()
    if a.cmd == "scan":
        d = date.fromisoformat(a.asof)
        ts = datetime.fromisoformat(f"{a.asof}T{a.asof_time}") if a.asof_time else (
            datetime.now() if d == date.today() else datetime.combine(d, datetime.min.time()))
        try:
            asyncio.run(scan(d, ts))
        except WrenError as e:
            print(f"[失敗] {e}")
            sys.exit(2)
    elif a.cmd == "feedback":
        reg = Registry(STATE / "registry.db", CFG)
        hit = reg.feedback(a.sig, a.status, a.note, a.who)
        print(f"已更新 {len(hit)} 筆：" + "、".join(hit) if hit else "找不到這個代號")
    elif a.cmd == "list":
        for r in Registry(STATE / "registry.db", CFG).all():
            print(f"[{r['status']:<14}] {r['first_seen']}~{r['last_seen']} ×{r['times_seen']}  {r['title'][:70]}\n    {r['signature']}")
    elif a.cmd == "evaluate":
        evaluate(a.gt, a.asof)


if __name__ == "__main__":
    main()
