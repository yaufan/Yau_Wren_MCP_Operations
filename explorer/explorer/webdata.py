"""網頁要的資料，全部從 state/（登記簿 registry.db 與每次巡檢的 findings_日期.json）組出來。
網頁本身不連資料庫、不跑 SQL，只讀巡檢留下的結果。"""
import json
import re
from pathlib import Path

from .investigator import KIND_ZH
from .registry import Registry

STATUS_ZH = {"open": "待處理", "known": "已知", "false_positive": "誤報", "resolved": "已恢復"}
GROUP_ZH = {"new": "新", "worse": "惡化", "reopened": "復發", "ongoing": "持續中", "suppressed": "已略過", "resolved": "已恢復"}


class Data:
    def __init__(self, state: Path, cfg):
        self.state, self.cfg = Path(state), cfg

    def reg(self):
        return Registry(self.state / "registry.db", self.cfg)

    def dates(self):
        ds = [re.match(r"findings_(\d{4}-\d{2}-\d{2})\.json", p.name) for p in self.state.glob("findings_*.json")]
        return sorted((m.group(1) for m in ds if m), reverse=True)

    def run(self, date=None):
        ds = self.dates()
        if not ds:
            return None
        date = date if date in ds else ds[0]
        return json.loads((self.state / f"findings_{date}.json").read_text(encoding="utf-8"))

    # ---------- 今日 ----------
    def day(self, date=None):
        run = self.run(date)
        if run is None:
            return {"empty": True, "dates": []}
        reg = self.reg()
        rows = {r["signature"]: r for r in reg.all()}
        by_sig = {f["signature"]: f for f in run["findings"]}
        items = []
        for g, sigs in run["groups"].items():
            for s in sigs:
                f, r = by_sig.get(s, {}), rows.get(s, {})
                items.append({"signature": s, "group": g, "group_zh": GROUP_ZH[g],
                              "title": f.get("title") or r.get("title"), "kind_zh": KIND_ZH.get(f.get("kind") or r.get("kind"), ""),
                              "severity": f.get("severity") or r.get("severity") or "中",
                              "status": r.get("status"), "status_zh": STATUS_ZH.get(r.get("status"), ""),
                              "first_seen": r.get("first_seen"), "note": r.get("note"), "has_card": bool(r.get("has_card"))})
        counts = {g: len(v) for g, v in run["groups"].items()}
        q = run.get("queries", [])
        return {"date": run["asof"], "run_at": run.get("run_at"), "dates": self.dates(), "window": run.get("window"),
                "counts": counts, "need": counts.get("new", 0) + counts.get("worse", 0) + counts.get("reopened", 0),
                "detected": len(run["findings"]), "items": items,
                "detectors": [{"name": n, "status": s, "count": k} for n, s, k in run["detectors"]],
                "queries": {"n": len(q), "sec": round(sum(x.get("sec", 0) for x in q), 1),
                            "failed": sum(1 for x in q if not x.get("ok", True))}}

    # ---------- 各模型健康 ----------
    def grid(self, date=None):
        run = self.run(date)
        if run is None:
            return {"empty": True}
        cells = run.get("grid", [])
        models = {}
        for c in cells:
            m = models.setdefault(c["model"], {"model": c["model"], "filetype": c["filetype"], "procedures": set()})
            m["procedures"].add(c["procedure_code"])
        site_n = {}
        for c in cells:
            site_n[c["site"]] = site_n.get(c["site"], 0) + c["n"] + c["base_n"]
        sites = sorted(site_n, key=lambda s: -site_n[s])
        agg = {}
        for c in cells:
            a = agg.setdefault((c["model"], c["site"]), {"n": 0, "nfi": 0.0, "bn": 0, "bx": 0.0})
            a["n"] += c["n"]; a["nfi"] += (c["nfi_rate"] or 0) * c["n"]
            a["bn"] += c["base_n"]; a["bx"] += (c["base_rate"] or 0) * c["base_n"]
        reg = {r["signature"]: r for r in self.reg().all()}
        marks, platform = {}, []
        shown = [s for g in ("new", "worse", "reopened", "ongoing", "suppressed") for s in run["groups"].get(g, [])]
        by_sig = {f["signature"]: f for f in run["findings"]}
        for s in shown:
            f, r = by_sig[s], reg.get(s, {})
            if r.get("status") == "false_positive":
                continue
            hit = self._cells_for(f["entity"], models, sites, agg)
            info = {"signature": s, "title": f["title"], "severity": f.get("severity", "中"), "status": r.get("status"),
                    "status_zh": STATUS_ZH.get(r.get("status"), "")}
            if not hit:
                platform.append(info)
            for k in hit:
                marks.setdefault(f"{k[0]}|{k[1]}", []).append(info)
        out_cells = []
        for (m, s), a in agg.items():
            out_cells.append({"model": m, "site": s, "n": a["n"],
                              "nfi_rate": round(a["nfi"] / a["n"], 4) if a["n"] else None,
                              "base_rate": round(a["bx"] / a["bn"], 4) if a["bn"] else None,
                              "marks": marks.get(f"{m}|{s}", [])})
        return {"date": run["asof"], "window": run.get("window"), "sites": sites,
                "models": [{"model": m["model"], "filetype": m["filetype"], "procedures": sorted(m["procedures"]),
                            "marks": marks.get(f"{m['model']}|*", [])} for m in
                           sorted(models.values(), key=lambda x: (x["filetype"], x["model"]))],
                "cells": out_cells, "platform": platform}

    @staticmethod
    def _cells_for(ent, models, sites, agg):
        m, s, ft, pc = ent.get("model"), ent.get("site"), ent.get("filetype"), ent.get("procedure_code")
        if m:
            ms = [x for x in str(m).split("、") if x in models]
        elif pc:
            ms = [k for k, v in models.items() if pc in v["procedures"]]
        elif ft or s:
            ms = [k for k, v in models.items() if not ft or v["filetype"] == ft]
        else:
            return []
        if not s:                       # 沒指定院區 → 整個模型的問題，標在模型那一列
            return [(a, "*") for a in ms]
        return [(a, s) for a in ms if (a, s) in agg]

    # ---------- 全部發現 / 卡片 / 歷史 ----------
    def findings(self):
        rows = self.reg().all()
        for r in rows:
            r["kind_zh"] = KIND_ZH.get(r["kind"], r["kind"])
            r["status_zh"] = STATUS_ZH.get(r["status"], r["status"])
            r["entity"] = json.loads(r["entity"]) if r.get("entity") else {}
            r["has_card"] = bool(r["has_card"])
        return rows

    def card(self, sig):
        r = self.reg().card(sig)
        if r:
            r["kind_zh"] = KIND_ZH.get(r["kind"], r["kind"])
            r["status_zh"] = STATUS_ZH.get(r["status"], r["status"])
        return r

    def history(self):
        reg = self.reg()
        return {"runs": reg.runs(), "findings": self.findings()}

    def feedback(self, sig, status, note, who):
        return self.reg().feedback(sig, status, note, who, exact=True)

    def bundle(self):
        """示範版用：把所有日期的資料一次打包"""
        return {"dates": self.dates(), "day": {d: self.day(d) for d in self.dates()}, "grid": {d: self.grid(d) for d in self.dates()},
                "findings": self.findings(), "cards": {r["signature"]: self.card(r["signature"]) for r in self.findings()},
                "history": self.history()}
