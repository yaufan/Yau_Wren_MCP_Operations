"""
記憶層：發現登記簿（SQLite，單一檔案）。
讓系統「記得已經講過什麼、人怎麼回覆」，每天只把真正新的、或變嚴重的東西推給人看。

狀態：
  open            已報出、還沒有人處理（下次只用一行列在「持續中」）
  known           人確認是已知問題 → 不再報，除非嚴重程度變成 worsen_factor 倍
  false_positive  人判定誤報 → 不再報
  resolved        連續 resolve_after_runs 次沒偵測到 → 自動結案；再出現算「復發」
"""
import json
import sqlite3
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS findings (
  signature TEXT PRIMARY KEY, kind TEXT, entity TEXT, title TEXT,
  status TEXT, first_seen TEXT, last_seen TEXT, times_seen INTEGER,
  ack_magnitude REAL, last_magnitude REAL, runs_unseen INTEGER DEFAULT 0, note TEXT, card TEXT);
CREATE TABLE IF NOT EXISTS feedback_log (ts TEXT, signature TEXT, status TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS runs (run_date TEXT, detected INTEGER, reported INTEGER, suppressed INTEGER);
"""


class Registry:
    def __init__(self, path, cfg):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        for ddl in ("ALTER TABLE findings ADD COLUMN severity TEXT", "ALTER TABLE feedback_log ADD COLUMN who TEXT"):
            try:
                self.db.execute(ddl)
            except sqlite3.OperationalError:
                pass                      # 欄位已存在
        self.cfg = cfg["registry"]

    def get(self, sig):
        cur = self.db.execute("SELECT * FROM findings WHERE signature = ?", (sig,))
        row = cur.fetchone()
        return dict(zip([d[0] for d in cur.description], row)) if row else None

    def classify(self, findings, run_date: date):
        """把今天偵測到的東西分成：新、惡化、復發、持續中、已抑制；並更新登記簿"""
        d = run_date.isoformat()
        out = {"new": [], "worse": [], "reopened": [], "ongoing": [], "suppressed": [], "resolved": []}
        seen = set()
        for f in findings:
            s = f["signature"]
            seen.add(s)
            r = self.get(s)
            mag = float(f.get("magnitude") or 0)
            if r is None:
                out["new"].append(f)
                self.db.execute("INSERT INTO findings (signature, kind, entity, title, status, first_seen, last_seen, times_seen, "
                                "ack_magnitude, last_magnitude, runs_unseen) VALUES (?,?,?,?, 'open', ?, ?, 1, NULL, ?, 0)",
                                (s, f["kind"], json.dumps(f["entity"], ensure_ascii=False), f["title"], d, d, mag))
                continue
            self.db.execute("UPDATE findings SET last_seen=?, times_seen=times_seen+1, last_magnitude=?, runs_unseen=0, title=? WHERE signature=?",
                            (d, mag, f["title"], s))
            f["registry"] = r
            st = r["status"]
            if st == "resolved":
                out["reopened"].append(f)
                self.db.execute("UPDATE findings SET status='open' WHERE signature=?", (s,))
            elif st == "known":
                base = r["ack_magnitude"] or r["last_magnitude"] or 0
                if base and mag >= self.cfg["worsen_factor"] * base:
                    out["worse"].append(f)
                else:
                    out["suppressed"].append(f)
            elif st == "false_positive":
                out["suppressed"].append(f)
            else:
                out["ongoing"].append(f)
        # 今天沒看到的 → 累計；連續 N 次沒看到就結案
        for s, st in self.db.execute("SELECT signature, status FROM findings WHERE status IN ('open','known')").fetchall():
            if s in seen:
                continue
            self.db.execute("UPDATE findings SET runs_unseen = runs_unseen + 1 WHERE signature=?", (s,))
            if self.get(s)["runs_unseen"] >= self.cfg["resolve_after_runs"]:
                self.db.execute("UPDATE findings SET status='resolved' WHERE signature=?", (s,))
                out["resolved"].append(self.get(s))
        self.db.execute("INSERT INTO runs VALUES (?,?,?,?)",
                        (d, len(findings), len(out["new"]) + len(out["worse"]) + len(out["reopened"]), len(out["suppressed"])))
        self.db.commit()
        return out

    def save_card(self, sig, card):
        self.db.execute("UPDATE findings SET card=? WHERE signature=?", (json.dumps(card, ensure_ascii=False), sig))
        self.db.commit()

    def set_severity(self, sig, sev):
        self.db.execute("UPDATE findings SET severity=? WHERE signature=?", (sev, sig))
        self.db.commit()

    def feedback(self, sig_or_prefix, status, note="", who="", exact=False):
        """人的回饋：known / false_positive / open / resolved。可用 signature 前綴指定"""
        assert status in ("known", "false_positive", "open", "resolved")
        if exact:
            rows = self.db.execute("SELECT signature, last_magnitude FROM findings WHERE signature = ?", (sig_or_prefix,)).fetchall()
        else:
            rows = self.db.execute("SELECT signature, last_magnitude FROM findings WHERE signature LIKE ?", (sig_or_prefix + "%",)).fetchall()
        for s, mag in rows:
            self.db.execute("UPDATE findings SET status=?, note=?, ack_magnitude=? WHERE signature=?", (status, note, mag, s))
            self.db.execute("INSERT INTO feedback_log (ts, signature, status, note, who) VALUES (datetime('now', 'localtime'), ?, ?, ?, ?)",
                            (s, status, note, who))
        self.db.commit()
        return [r[0] for r in rows]

    def _rows(self, sql, args=()):
        cur = self.db.execute(sql, args)
        return [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]

    def all(self):
        return self._rows("SELECT signature, kind, status, severity, first_seen, last_seen, times_seen, runs_unseen, note, title, "
                          "entity, card IS NOT NULL AS has_card FROM findings ORDER BY first_seen, kind")

    def card(self, sig):
        r = self.get(sig)
        if not r:
            return None
        r["card"] = json.loads(r["card"]) if r.get("card") else None
        r["entity"] = json.loads(r["entity"]) if r.get("entity") else {}
        r["feedback"] = self._rows("SELECT ts, status, note, who FROM feedback_log WHERE signature=? ORDER BY ts", (sig,))
        return r

    def runs(self):
        return self._rows("SELECT run_date, detected, reported, suppressed FROM runs ORDER BY run_date")
