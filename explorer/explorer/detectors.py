"""
第一層：通用偵測器。不寫「某模型 NFI 超過 X% 就叫」這種規則，
而是把每個模型、院區、檢查代碼都當成一個切片，跟自己的過去比，找出「跟平常不一樣」的地方。

每個偵測器回傳 Finding（dict）：
  kind / signature（去重用的穩定鍵）/ entity / title / metric / baseline / current /
  magnitude（嚴重程度的單一數字，記憶層判斷「惡化」用）/ score / evidence（附 SQL 與數字）
"""
import math
import re
from datetime import date, datetime, timedelta

import pandas as pd

from .wren_client import lit

CAT_NFI, CAT_FAIL, CAT_PROC, CAT_OK = "不適用", "失敗", "處理中", "成功"
DIM_ZH = {"model": "模型", "site": "院區", "procedure_code": "檢查代碼", "filetype": "檔案類型"}


class Ctx:
    """一次掃描的時間窗。D = 報告日；只用到 D-1（昨天）為止的完整日。"""

    def __init__(self, asof: date, asof_ts: datetime, cfg: dict):
        self.D, self.cfg = asof, cfg
        w = cfg["windows"]
        self.r1 = asof - timedelta(days=1)
        self.r0 = asof - timedelta(days=w["recent_days"])
        self.b1 = self.r0 - timedelta(days=1)
        self.b0 = self.r0 - timedelta(days=w["baseline_days"])
        self.asof_ts = asof_ts
        self.stuck_cutoff = (asof_ts - timedelta(hours=cfg["stuck"]["hours"])).strftime("%Y-%m-%d %H:%M:%S")

    def d(self, x):
        return f"DATE '{x.isoformat()}'"


# ---------- 小工具 ----------
def z_two_prop(x1, n1, x0, n0):
    if n1 == 0 or n0 == 0:
        return 0.0
    p = (x1 + x0) / (n1 + n0)
    se = math.sqrt(max(p * (1 - p) * (1 / n1 + 1 / n0), 1e-12))
    return (x1 / n1 - x0 / n0) / se


def poisson_cdf(k, lam):
    if lam <= 0:
        return 1.0
    s, term = 0.0, math.exp(-lam)
    for i in range(int(k) + 1):
        s += term
        term *= lam / (i + 1)
    return min(s, 1.0)


def ent_text(ent):
    return "、".join(f"{DIM_ZH.get(k, k)} {v}" for k, v in ent.items())


def sig(kind, ent, extra=""):
    return kind + "|" + "|".join(f"{k}={v}" for k, v in sorted(ent.items())) + (("|" + extra) if extra else "")


def norm_msg(s):
    s = re.sub(r"\d+", "#", str(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:200]


def pct(x):
    return f"{x * 100:.1f}%"


# ---------- 共用的切片聚合（一次查詢，給比例與量兩個偵測器用） ----------
async def load_segments(w, c: Ctx):
    sql = (f"SELECT study_date, model, site, procedure_code, filetype, COUNT(*) AS n, "
           f"SUM(CASE WHEN status_category = '{CAT_NFI}' THEN 1 ELSE 0 END) AS nfi, "
           f"SUM(CASE WHEN status_category = '{CAT_FAIL}' THEN 1 ELSE 0 END) AS fail "
           f"FROM inferences WHERE study_date BETWEEN {c.d(c.b0)} AND {c.d(c.r1)} GROUP BY 1, 2, 3, 4, 5")
    df = await w.q(sql, "切片聚合")
    df["study_date"] = pd.to_datetime(df["study_date"]).dt.date
    df["period"] = (df["study_date"] >= c.r0).map({True: "recent", False: "base"})
    for col in ("site", "procedure_code", "filetype"):
        df[col] = df[col].fillna("(空白)")
    c.seg = df          # 給網頁的「各模型健康」格子用
    return df, sql


# ---------- D1 比例偏移（NFI / 失敗率）：模型、模型×院區、模型×檢查代碼 ----------
def detect_rate_shift(seg, seg_sql, c: Ctx):
    """同時看兩個近期窗：7 天（慢慢變壞）與 1 天（昨天突然變壞，7 天平均會把它稀釋掉）"""
    out, seen = [], set()
    for k in c.cfg["rate_shift"]["windows"]:
        s2 = seg.copy()
        start = c.r1 - timedelta(days=k - 1)
        s2["period"] = s2["study_date"].map(lambda d: "recent" if d >= start else ("base" if d <= c.b1 else None))
        s2 = s2[s2["period"].notna()]
        for f in _rate_shift_window(s2, seg_sql, c, k):
            if f["signature"] not in seen:
                seen.add(f["signature"]); out.append(f)
    return out


def _rate_shift_window(seg, seg_sql, c: Ctx, k):
    cfg = c.cfg["rate_shift"]
    win = "昨天" if k == 1 else f"近 {k} 天"
    out = []
    for metric, col in (("NFI 比例", "nfi"), ("失敗率", "fail")):
        agg = lambda g: g.groupby("period")[["n", col]].sum()
        flagged_parent, flagged_child = {}, {}
        for model, gm in seg.groupby("model"):
            a = agg(gm)
            if "recent" not in a.index or "base" not in a.index:
                continue
            rn, rx, bn, bx = a.loc["recent", "n"], a.loc["recent", col], a.loc["base", "n"], a.loc["base", col]
            z = z_two_prop(rx, rn, bx, bn)
            if rn >= cfg["min_recent_n"] and z >= cfg["min_z"] and rx / rn - bx / bn >= cfg["min_abs_diff"]:
                flagged_parent[model] = (rn, rx, bn, bx, z)
            # 子切片：要「比同模型其他切片明顯高」才算局部問題
            for dim in ("site", "procedure_code"):
                for val, gc in gm.groupby(dim):
                    ac = agg(gc)
                    if "recent" not in ac.index:
                        continue
                    crn, crx = ac.loc["recent", "n"], ac.loc["recent", col]
                    cbn = ac.loc["base", "n"] if "base" in ac.index else 0
                    cbx = ac.loc["base", col] if "base" in ac.index else 0
                    orn, orx = rn - crn, rx - crx
                    if crn < cfg["min_recent_n"] or orn < cfg["min_recent_n"]:
                        continue
                    z_t = z_two_prop(crx, crn, cbx, cbn) if cbn else 0
                    z_l = z_two_prop(crx, crn, orx, orn)
                    if z_t >= cfg["min_z"] and z_l >= cfg["min_z"] and crx / crn - (cbx / cbn if cbn else 0) >= cfg["min_abs_diff"]:
                        flagged_child[(model, dim, val)] = (crn, crx, cbn, cbx, min(z_t, z_l))
        # 父切片如果「扣掉局部問題的子切片後就不顯著」→ 由子切片代表，不重複報
        for model, (rn, rx, bn, bx, z) in list(flagged_parent.items()):
            kids = [(k, v) for k, v in flagged_child.items() if k[0] == model and k[1] == "site"]
            if kids:
                rn2 = rn - sum(v[0] for _, v in kids); rx2 = rx - sum(v[1] for _, v in kids)
                bn2 = bn - sum(v[2] for _, v in kids); bx2 = bx - sum(v[3] for _, v in kids)
                if not (rn2 >= cfg["min_recent_n"] and z_two_prop(rx2, rn2, bx2, bn2) >= cfg["min_z"]
                        and rx2 / rn2 - bx2 / bn2 >= cfg["min_abs_diff"]):
                    del flagged_parent[model]
                    continue
            # 父切片已報 → 子切片的比例跟父切片差不多時不另外報
            for k in [k for k in flagged_child if k[0] == model]:
                crn, crx = flagged_child[k][0], flagged_child[k][1]
                if abs(crx / crn - rx / rn) < cfg["min_abs_diff"]:
                    del flagged_child[k]
        items = [({"model": m}, v) for m, v in flagged_parent.items()] + \
                [({"model": k[0], k[1]: k[2]}, v) for k, v in flagged_child.items()]
        for ent, (rn, rx, bn, bx, z) in items:
            cur, base = rx / rn, (bx / bn if bn else 0)
            out.append({
                "kind": "rate_shift", "signature": sig("rate_shift", ent, col), "entity": ent,
                "title": f"{ent_text(ent)}：{metric}從 {pct(base)} 升到 {pct(cur)}（{win}）",
                "metric": metric, "baseline": round(base, 4), "current": round(cur, 4),
                "magnitude": round(cur - base, 4), "score": round(z, 1),
                "window_days": k,
                "evidence": [{"label": f"{win} vs 基準 {c.cfg['windows']['baseline_days']} 天",
                              "sql": seg_sql, "values": {"近期筆數": int(rn), f"近期{metric}": pct(cur),
                                                         "基準筆數": int(bn), f"基準{metric}": pct(base), "z": round(z, 1)}}],
            })
    return out


# ---------- D2 量驟降：院區 → 院區×檔案類型 → 院區×模型；模型整體 → 模型×院區 ----------
def detect_volume_drop(seg, seg_sql, c: Ctx):
    cfg = c.cfg["volume_drop"]
    seg = seg.copy()
    seg["wkend"] = seg["study_date"].map(lambda d: d.weekday() >= 5)
    base = seg[seg["period"] == "base"]
    n_base_days = {True: 0, False: 0}
    for d in pd.date_range(c.b0, c.b1).date:
        n_base_days[d.weekday() >= 5] += 1
    last_days = [c.r1 - timedelta(days=i) for i in range(3)]          # 昨天、前天、大前天

    def test(mask_base, mask_all):
        exp_by_type = {t: base[mask_base & (base["wkend"] == t)]["n"].sum() / max(n_base_days[t], 1) for t in (True, False)}
        daily = seg[mask_all].groupby("study_date")["n"].sum()
        best = None
        for k in (1, 2, 3):
            days = last_days[:k]
            obs = sum(int(daily.get(d, 0)) for d in days)
            exp = sum(exp_by_type[d.weekday() >= 5] for d in days)
            if exp < cfg["min_expected"]:
                continue
            p = poisson_cdf(obs, exp)
            if obs / exp <= cfg["max_ratio"] and (best is None or p < best[3]):
                best = (k, obs, exp, p)
        return best

    def mask(df, ent):
        m = pd.Series(True, index=df.index)
        for k, v in ent.items():
            m &= df[k] == v
        return m

    def children(ent):
        if list(ent) == ["site"]:
            return [dict(ent, filetype=v) for v in seg[seg.site == ent["site"]].filetype.unique()]
        if set(ent) == {"site", "filetype"}:
            s = seg[(seg.site == ent["site"]) & (seg.filetype == ent["filetype"])]
            return [dict(ent, model=v) for v in s.model.unique()]
        if list(ent) == ["model"]:
            return [dict(ent, site=v) for v in seg[seg.model == ent["model"]].site.unique()]
        return []

    def explain(ent, res):
        """往下找：如果某個子切片佔了 ≥90% 的短少量，就用更精確的子切片代表"""
        deficit = res[2] - res[1]
        for ch in children(ent):
            r = test(mask(base, ch), mask(seg, ch))
            if r and (r[2] - r[1]) >= cfg["explain_share"] * deficit:
                return explain(ch, r)
        return ent, res

    out, seen = [], set()
    tops = [{"site": s} for s in seg.site.unique()] + [{"model": m} for m in seg.model.unique()]
    for ent in tops:
        res = test(mask(base, ent), mask(seg, ent))
        if not res:
            continue
        ent2, (k, obs, exp, p) = explain(ent, res)
        key = sig("volume_drop", ent2)
        if key in seen:
            continue
        seen.add(key)
        ratio = obs / exp
        out.append({
            "kind": "volume_drop", "signature": key, "entity": ent2,
            "title": f"{ent_text(ent2)}：最近 {k} 天量只有平常的 {pct(ratio)}（{obs} 筆 / 預期約 {exp:.0f} 筆）",
            "metric": f"近 {k} 天推論筆數", "baseline": round(exp, 1), "current": obs,
            "magnitude": round(1 - ratio, 3), "score": round(-math.log10(max(p, 1e-300)), 1),
            "ratio": ratio,
            "evidence": [{"label": "與基準期同類日（平日/假日）平均比較", "sql": seg_sql,
                          "values": {"觀察天數": k, "實際筆數": obs, "預期筆數": round(exp, 1), "Poisson p": f"{p:.1e}"}}],
        })
    # 院區路線找到的「院區×…×模型 M」若跟「模型 M 整體」掉得差不多，代表是模型整體的問題，只留模型那張
    whole = {f["entity"]["model"]: f["ratio"] for f in out if list(f["entity"]) == ["model"]}
    out = [f for f in out if not ("model" in f["entity"] and len(f["entity"]) > 1
                                  and f["entity"]["model"] in whole and whole[f["entity"]["model"]] <= f["ratio"] + 0.2)]
    return out


# ---------- D3 新的錯誤 / NFI 訊息 ----------
async def detect_new_message(w, c: Ctx):
    cfg = c.cfg["new_message"]
    sql = (f"SELECT model, status_category, info_log, MIN(study_date) AS first_seen, MAX(study_date) AS last_seen, "
           f"COUNT(*) AS n FROM inferences WHERE status_category IN ('{CAT_NFI}', '{CAT_FAIL}') GROUP BY 1, 2, 3")
    df = await w.q(sql, "訊息首次出現")
    if df.empty:
        return []
    df["norm"] = df["info_log"].map(norm_msg)
    df["first_seen"] = pd.to_datetime(df["first_seen"]).dt.date
    g = df.groupby("norm").agg(first_seen=("first_seen", "min"), n=("n", "sum"),
                               models=("model", lambda s: sorted(set(s))), example=("info_log", "first"))
    since = c.r1 - timedelta(days=cfg["lookback_days"] - 1)
    out = []
    for norm, r in g.iterrows():
        if since <= r.first_seen <= c.r1 and r.n >= cfg["min_count"]:
            ent = {"model": r.models[0]} if len(r.models) == 1 else {"model": "、".join(r.models)}
            out.append({
                "kind": "new_message", "signature": sig("new_message", {}, norm), "entity": ent,
                "title": f"新的訊息首次出現（{r.first_seen}，{r.n} 筆）：{r.example[:60]}",
                "metric": "訊息筆數", "baseline": 0, "current": int(r.n), "magnitude": int(r.n), "score": float(r.n),
                "message_example": r.example,
                "evidence": [{"label": "以前從沒出現過的訊息（數字已正規化）", "sql": sql,
                              "values": {"首次出現": str(r.first_seen), "筆數": int(r.n), "模型": "、".join(r.models), "訊息": r.example}}],
            })
    return out


# ---------- D4 新的類別值（模型、院區、狀態碼） ----------
async def detect_new_category(w, c: Ctx):
    out = []
    for dim, zh in (("model", "模型"), ("site", "院區"), ("inference_status", "狀態碼")):
        sql = (f"SELECT {dim} AS val, MIN(study_date) AS first_seen, COUNT(*) AS n FROM inferences "
               f"GROUP BY 1 HAVING MIN(study_date) >= {c.d(c.r0)}")
        df = await w.q(sql, f"新{zh}")
        for _, r in df.iterrows():
            out.append({
                "kind": "new_category", "signature": sig("new_category", {dim: r.val}), "entity": {dim: r.val},
                "title": f"出現新的{zh}「{r.val}」（{r.first_seen} 起，{r.n} 筆）",
                "metric": "筆數", "baseline": 0, "current": int(r.n), "magnitude": int(r.n), "score": float(r.n),
                "evidence": [{"label": f"近 {c.cfg['windows']['recent_days']} 天才第一次出現", "sql": sql,
                              "values": {"值": r.val, "首次出現": str(r.first_seen), "筆數": int(r.n)}}],
            })
    return out


# ---------- D5 不合理時間 ----------
async def detect_invalid_time(w, c: Ctx):
    hi = (c.D + timedelta(days=1)).isoformat()
    sql = (f"SELECT site, COUNT(*) AS n, MIN(study_time) AS min_t, MAX(study_time) AS max_t FROM exams "
           f"WHERE study_time > TIMESTAMP '{hi} 00:00:00' OR study_time < TIMESTAMP '2000-01-01 00:00:00' GROUP BY 1")
    df = await w.q(sql, "不合理時間")
    if df.empty:
        return []
    n = int(df.n.sum())
    return [{
        "kind": "invalid_time", "signature": "invalid_time|exams.study_time", "entity": {"欄位": "檢查時間"},
        "title": f"{n} 筆檢查的時間不合理（在未來或 2000 年以前），最晚到 {df.max_t.max()[:10]}",
        "metric": "筆數", "baseline": 0, "current": n, "magnitude": n, "score": float(n),
        "evidence": [{"label": "依院區", "sql": sql, "values": {r.site: f"{r.n} 筆（{r.min_t[:10]} ~ {r.max_t[:10]}）" for r in df.itertuples()}}],
    }]


# ---------- D6 結構不變量：同一個檢查應該跑的模型數 ----------
async def detect_invariant(w, c: Ctx):
    cfg = c.cfg["invariant"]
    sql = (f"SELECT procedure_code, study_date, n_models, COUNT(*) AS exams FROM ("
           f"SELECT exam_id, procedure_code, study_date, COUNT(DISTINCT model) AS n_models FROM inferences "
           f"WHERE study_date BETWEEN {c.d(c.b0)} AND {c.d(c.r1)} GROUP BY 1, 2, 3) t GROUP BY 1, 2, 3")
    df = await w.q(sql, "每個檢查跑的模型數")
    df["study_date"] = pd.to_datetime(df["study_date"]).dt.date
    df["recent"] = df["study_date"] >= c.r0
    out = []
    for proc, g in df.groupby("procedure_code"):
        b = g[~g.recent]
        if b.empty:
            continue
        expected = int(b.groupby("n_models")["exams"].sum().idxmax())     # 基準期最常見的模型數
        if expected <= 1:
            continue
        def short(x):
            return int(x[x.n_models < expected]["exams"].sum()), int(x["exams"].sum())
        rs, rn = short(g[g.recent]); bs, bn = short(b)
        if rn == 0:
            continue
        z = z_two_prop(rs, rn, bs, bn)
        if rs / rn - bs / bn >= cfg["min_diff"] and z >= cfg["min_z"]:
            ent = {"procedure_code": proc}
            out.append({
                "kind": "invariant", "signature": sig("invariant", ent), "entity": ent,
                "title": f"檢查代碼 {proc}：每個檢查應跑 {expected} 個模型，近期 {pct(rs / rn)} 的檢查少跑（平常 {pct(bs / bn)}）",
                "metric": "少跑模型的檢查比例", "baseline": round(bs / bn, 4), "current": round(rs / rn, 4),
                "magnitude": round(rs / rn - bs / bn, 4), "score": round(z, 1), "expected_models": expected,
                "evidence": [{"label": "每個檢查跑了幾個模型", "sql": sql,
                              "values": {"應跑模型數": expected, "近期少跑檢查": f"{rs}/{rn}", "基準少跑檢查": f"{bs}/{bn}", "z": round(z, 1)}}],
            })
    return out


# ---------- D7 未登記模型 ----------
async def detect_unregistered(w, c: Ctx):
    sql = (f"SELECT model, COUNT(*) AS n, MIN(study_date) AS first_seen, MAX(study_date) AS last_seen FROM inferences "
           f"WHERE study_date >= {c.d(c.b0)} AND model NOT IN (SELECT model FROM model_status) GROUP BY 1")
    df = await w.q(sql, "未登記模型")
    return [{
        "kind": "unregistered_model", "signature": sig("unregistered_model", {"model": r.model}), "entity": {"model": r.model},
        "title": f"模型「{r.model}」有推論紀錄（近 35 天 {r.n} 筆），但沒有登記在 model_status，平台監控看不到它",
        "metric": "筆數", "baseline": 0, "current": int(r.n), "magnitude": 1, "score": float(r.n),
        "evidence": [{"label": "推論表有、狀態表沒有", "sql": sql, "values": {"筆數": int(r.n), "期間": f"{r.first_seen} ~ {r.last_seen}"}}],
    } for r in df.itertuples()]


# ---------- D8 耗時 P90 偏移 ----------
async def detect_latency(w, c: Ctx):
    cfg = c.cfg["latency"]
    sql = (f"SELECT model, CASE WHEN study_date >= {c.d(c.r0)} THEN 'recent' ELSE 'base' END AS period, COUNT(*) AS n, "
           f"percentile_cont(0.9) WITHIN GROUP (ORDER BY infer_seconds) AS p90, "
           f"percentile_cont(0.5) WITHIN GROUP (ORDER BY infer_seconds) AS p50 FROM inferences "
           f"WHERE study_date BETWEEN {c.d(c.b0)} AND {c.d(c.r1)} AND infer_seconds IS NOT NULL GROUP BY 1, 2")
    df = await w.q(sql, "耗時分位數")
    out = []
    for model, g in df.groupby("model"):
        g = g.set_index("period")
        if not {"recent", "base"} <= set(g.index):
            continue
        r, b = g.loc["recent"], g.loc["base"]
        if r.n < cfg["min_n"] or b.p90 <= 0:
            continue
        ratio = r.p90 / b.p90
        if ratio >= cfg["min_ratio"]:
            ent = {"model": model}
            out.append({
                "kind": "latency_shift", "signature": sig("latency_shift", ent), "entity": ent,
                "title": f"{model}：推論耗時 P90 從 {b.p90:.0f} 秒變成 {r.p90:.0f} 秒（{ratio:.1f} 倍）",
                "metric": "推論耗時 P90（秒）", "baseline": round(float(b.p90), 1), "current": round(float(r.p90), 1),
                "magnitude": round(float(ratio), 2), "score": round(float(ratio), 2),
                "evidence": [{"label": "近期 vs 基準期", "sql": sql,
                              "values": {"近期筆數": int(r.n), "近期 P50": round(float(r.p50)), "近期 P90": round(float(r.p90)),
                                         "基準 P50": round(float(b.p50)), "基準 P90": round(float(b.p90))}}],
            })
    return out


# ---------- D9 卡住的推論 ----------
async def detect_stuck(w, c: Ctx):
    hrs = c.cfg["stuck"]["hours"]
    sql = (f"SELECT model, inference_status, COUNT(*) AS n, MIN(platform_time) AS oldest FROM inferences "
           f"WHERE status_category = '{CAT_PROC}' AND platform_time < TIMESTAMP '{c.stuck_cutoff}' "
           f"AND study_date >= {c.d(c.b0)} GROUP BY 1, 2")
    df = await w.q(sql, "卡住的推論")
    return [{
        "kind": "stuck", "signature": sig("stuck", {"model": r.model}, f"status={r.inference_status}"), "entity": {"model": r.model},
        "title": f"{r.model}：{r.n} 筆停在處理中（狀態 {r.inference_status}）超過 {hrs} 小時，最早從 {r.oldest[:16]}",
        "metric": "卡住筆數", "baseline": 0, "current": int(r.n), "magnitude": int(r.n), "score": float(r.n),
        "evidence": [{"label": f"處理中且進平台超過 {hrs} 小時", "sql": sql, "values": {"筆數": int(r.n), "狀態碼": r.inference_status, "最早": r.oldest}}],
    } for r in df.itertuples()]


# ---------- D10 NFI 原因組成改變 ----------
async def detect_reason_mix(w, c: Ctx):
    cfg = c.cfg["reason_mix"]
    sql = (f"SELECT model, CASE WHEN study_date >= {c.d(c.r0)} THEN 'recent' ELSE 'base' END AS period, info_log, COUNT(*) AS n "
           f"FROM inferences WHERE status_category = '{CAT_NFI}' AND study_date BETWEEN {c.d(c.b0)} AND {c.d(c.r1)} GROUP BY 1, 2, 3")
    df = await w.q(sql, "NFI 原因組成")
    if df.empty:
        return []
    df["norm"] = df["info_log"].map(norm_msg)
    out = []
    for model, g in df.groupby("model"):
        p = g.pivot_table(index="norm", columns="period", values="n", aggfunc="sum", fill_value=0)
        if not {"recent", "base"} <= set(p.columns) or p["recent"].sum() < cfg["min_recent_nfi"] or p["base"].sum() < cfg["min_recent_nfi"]:
            continue
        r, b = p["recent"] / p["recent"].sum(), p["base"] / p["base"].sum()
        tvd = 0.5 * (r - b).abs().sum()
        if tvd >= cfg["min_tvd"]:
            top = (r - b).sort_values(ascending=False).index[0]
            ent = {"model": model}
            out.append({
                "kind": "nfi_reason_mix", "signature": sig("nfi_reason_mix", ent), "entity": ent,
                "title": f"{model}：NFI 原因組成改變，增加最多的是「{top[:50]}」（{pct(b[top])} → {pct(r[top])}）",
                "metric": "原因分布差異（TVD）", "baseline": 0, "current": round(float(tvd), 3),
                "magnitude": round(float(tvd), 3), "score": round(float(tvd) * 10, 1),
                "evidence": [{"label": "NFI 原因占比", "sql": sql,
                              "values": {k[:50]: f"{pct(b[k])} → {pct(r[k])}" for k in (r - b).abs().sort_values(ascending=False).index[:4]}}],
            })
    return out


async def run_all(w, c: Ctx):
    """依序跑全部偵測器；某一個失敗不影響其他（失敗本身也記在摘要裡）"""
    results, status = [], []
    try:
        seg, seg_sql = await load_segments(w, c)
        for name, fn in (("比例偏移", detect_rate_shift), ("量驟降", detect_volume_drop)):
            try:
                f = fn(seg, seg_sql, c); results += f; status.append((name, "ok", len(f)))
            except Exception as e:  # noqa
                status.append((name, f"錯誤：{e}", 0))
    except Exception as e:  # noqa
        status.append(("切片聚合", f"錯誤：{e}", 0))
    for name, fn in (("新訊息", detect_new_message), ("新類別值", detect_new_category), ("不合理時間", detect_invalid_time),
                     ("檢查應跑模型數", detect_invariant), ("未登記模型", detect_unregistered), ("耗時偏移", detect_latency),
                     ("卡住的推論", detect_stuck), ("NFI 原因組成", detect_reason_mix)):
        try:
            f = await fn(w, c); results += f; status.append((name, "ok", len(f)))
        except Exception as e:  # noqa
            status.append((name, f"錯誤：{e}", 0))
    return results, status
