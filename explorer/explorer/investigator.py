"""
第二層：調查員。對每個新發現自動往下追查（固定的追查劇本，全部經 Wren MCP），
整理成「事實」——每個數字都來自查詢結果；再交給寫手寫成發現卡。

寫手有兩種：
  template（預設）：規則模板，不需要 LLM
  llm：院內 OpenAI 相容端點（vLLM / Nemotron 等）。LLM 只負責「敘述、推測原因、建議下一步」，
       事實清單一律由程式產生；LLM 文字裡若出現證據裡沒有的數字，就退回模板（防止編造）。
"""
import json
import math
import os
import re
from datetime import timedelta

import httpx
import pandas as pd

from .detectors import CAT_NFI, CAT_PROC, pct, z_two_prop
from .wren_client import lit


def where_of(ent):
    m = {"model": "model", "site": "site", "procedure_code": "procedure_code", "filetype": "filetype"}
    conds = [f"{m[k]} = {lit(v)}" for k, v in ent.items() if k in m and "、" not in str(v)]
    return (" AND ".join(conds)) or "1 = 1"


def change_point(daily, kind="rate"):
    """找最可能的「變化起始日」：把序列切成前後兩段，選讓兩段各自最一致的切點。
    rate：二項分布對數概似；value：對數值的平方誤差。回傳 (起始日, 前段水準, 後段水準) 或 None"""
    d = daily.sort_values("study_date").reset_index(drop=True)
    d = d[d["n"] > 0].reset_index(drop=True)
    if len(d) < 6:
        return None
    best = None
    for i in range(3, len(d)):                           # 前段至少 3 天、後段至少 1 天
        a, b = d.iloc[:i], d.iloc[i:]
        if kind == "rate":
            def ll(x):
                p = min(max(x["nfi"].sum() / x["n"].sum(), 1e-6), 1 - 1e-6)
                return float((x["nfi"] * math.log(p) + (x["n"] - x["nfi"]) * math.log(1 - p)).sum())
            score = ll(a) + ll(b)
            la, lb = a["nfi"].sum() / a["n"].sum(), b["nfi"].sum() / b["n"].sum()
        else:
            va, vb = a["val"].map(math.log), b["val"].map(math.log)
            score = -float(((va - va.mean()) ** 2).sum() + ((vb - vb.mean()) ** 2).sum())
            la, lb = math.exp(va.mean()), math.exp(vb.mean())
        if best is None or score > best[0]:
            best = (score, str(d.iloc[i]["study_date"]), la, lb)
    return best[1:] if best else None


def fill_days(df, start, end, col="n"):
    """補上沒有資料的日子（量歸零的日子不能從趨勢裡消失）"""
    idx = pd.DataFrame({"study_date": [str(x.date()) for x in pd.date_range(start, end)]})
    df = df.copy()
    df["study_date"] = df["study_date"].astype(str)
    return idx.merge(df, on="study_date", how="left").fillna({col: 0})


async def investigate(w, f, c):
    """回傳 facts: list[str]、drill: list[{label, sql, table}]、hints: dict"""
    k, ent = f["kind"], f["entity"]
    days = c.cfg["investigator"]["trend_days"]
    t0 = c.d(c.r1 - timedelta(days=days - 1))
    facts, drill, hints = [], [], {}

    async def q(label, sql):
        df = await w.q(sql, f"調查:{label}")
        drill.append({"label": label, "sql": sql, "table": df.head(15).to_dict(orient="records")})
        return df

    if k in ("rate_shift", "nfi_reason_mix"):
        wh = where_of(ent)
        tr = await q("每日 NFI 比例", f"SELECT study_date, COUNT(*) AS n, SUM(CASE WHEN status_category = '{CAT_NFI}' THEN 1 ELSE 0 END) AS nfi "
                                      f"FROM inferences WHERE {wh} AND study_date BETWEEN {t0} AND {c.d(c.r1)} GROUP BY 1 ORDER BY 1")
        tr["rate"] = tr["nfi"] / tr["n"]
        cp = change_point(tr, "rate") if k == "rate_shift" else None
        if cp and cp[2] > cp[1]:
            facts.append(f"變化點估計在 {cp[0]}：之前約 {pct(cp[1])}，之後約 {pct(cp[2])}（近 {days} 天每日資料切成前後兩段）")
            hints["since"] = cp[0]
        last7 = tr.tail(7)
        facts.append("最近 7 天每日 NFI 比例：" + "、".join(f"{str(r.study_date)[5:]} {pct(r.rate)}（{r.n}）" for r in last7.itertuples()))
        if "site" not in ent:
            bs = await q("各院區近期 NFI", f"SELECT site, COUNT(*) AS n, SUM(CASE WHEN status_category = '{CAT_NFI}' THEN 1 ELSE 0 END) AS nfi "
                                         f"FROM inferences WHERE {wh} AND study_date BETWEEN {c.d(c.r0)} AND {c.d(c.r1)} GROUP BY 1 ORDER BY 2 DESC")
            bs = bs[bs.n >= 5]
            if len(bs) >= 2:
                T, X = bs.n.sum(), bs.nfi.sum()
                outl = [r.site for r in bs.itertuples() if abs(z_two_prop(r.nfi, r.n, X - r.nfi, T - r.n)) >= 3]
                hints["widespread"] = not outl
                facts.append(("各院區比例沒有顯著差異（不是單一院區問題）：" if not outl else f"院區 {'、'.join(outl)} 跟其他院區有顯著差異：")
                             + "、".join(f"{r.site} {pct(r.nfi / r.n)}（{r.n}）" for r in bs.head(6).itertuples()))
        rs = await q("近期 NFI 主要原因", f"SELECT info_log, COUNT(*) AS n FROM inferences WHERE {wh} AND status_category = '{CAT_NFI}' "
                                       f"AND study_date BETWEEN {c.d(c.r0)} AND {c.d(c.r1)} GROUP BY 1 ORDER BY 2 DESC LIMIT 3")
        if not rs.empty:
            tot = rs.n.sum()
            facts.append("近期 NFI 主要原因：" + "；".join(f"「{r.info_log[:70]}」{r.n} 筆" for r in rs.itertuples()))
            hints["top_reason"] = rs.iloc[0].info_log
            hints["top_reason_share"] = float(rs.iloc[0].n / tot)

    elif k == "volume_drop":
        wh = where_of(ent)
        tr = await q("每日筆數", f"SELECT study_date, COUNT(*) AS n FROM inferences WHERE {wh} "
                                 f"AND study_date BETWEEN {c.d(c.r1 - timedelta(days=13))} AND {c.d(c.r1)} GROUP BY 1 ORDER BY 1")
        tr = fill_days(tr, c.r1 - timedelta(days=13), c.r1)
        facts.append("最近 14 天每日筆數：" + "、".join(f"{str(r.study_date)[5:]} {int(r.n)}" for r in tr.itertuples()))
        lt = await q("最後一筆資料", f"SELECT MAX(study_time) AS last_study, MAX(platform_time) AS last_platform FROM inferences WHERE {wh}")
        if not lt.empty and lt.iloc[0].last_platform:
            facts.append(f"最後一筆進平台的時間：{str(lt.iloc[0].last_platform)[:16]}，之後就沒有資料")
            hints["last_seen"] = str(lt.iloc[0].last_platform)[:16]
        if "site" in ent:
            ot = await q("同院區其他類型", f"SELECT filetype, model, COUNT(*) AS n FROM inferences WHERE site = {lit(ent['site'])} "
                                         f"AND study_date BETWEEN {c.d(c.r1 - timedelta(days=1))} AND {c.d(c.r1)} GROUP BY 1, 2 ORDER BY 3 DESC")
            others = ot[ot.filetype != ent.get("filetype", "__")] if "filetype" in ent else ot
            if not others.empty:
                facts.append(f"同院區 {ent['site']} 其他類型最近 2 天仍有資料：" + "、".join(f"{r.model} {r.n}" for r in others.head(4).itertuples()))
                hints["site_partially_alive"] = True
            else:
                hints["site_partially_alive"] = False

    elif k == "new_message":
        ex = f.get("message_example", "")
        bd = await q("新訊息分布", f"SELECT study_date, model, site, COUNT(*) AS n FROM inferences WHERE info_log = {lit(ex)} GROUP BY 1, 2, 3 ORDER BY 1")
        facts.append("出現位置：" + "、".join(f"{str(r.study_date)[5:]} {r.model} @{r.site} ×{r.n}" for r in bd.itertuples()))
        hints["sites"] = sorted(set(bd.site.dropna()))
        m = re.search(r"loc='([^']+)'", ex)
        if m:
            hints["loc_code"] = m.group(1)

    elif k == "latency_shift":
        wh = where_of(ent)
        tr = await q("每日 P90", f"SELECT study_date, COUNT(*) AS n, percentile_cont(0.9) WITHIN GROUP (ORDER BY infer_seconds) AS p90 "
                                f"FROM inferences WHERE {wh} AND infer_seconds IS NOT NULL AND study_date BETWEEN {t0} AND {c.d(c.r1)} GROUP BY 1 ORDER BY 1")
        tr["val"] = tr["p90"].clip(lower=1)
        cp = change_point(tr, "value")
        if cp and cp[2] > cp[1]:
            facts.append(f"變化點估計在 {cp[0]}：每日 P90 之前約 {cp[1]:.0f} 秒，之後約 {cp[2]:.0f} 秒")
            hints["since"] = cp[0]
        facts.append("最近 7 天每日 P90（秒）：" + "、".join(f"{str(r.study_date)[5:]} {r.p90:.0f}" for r in tr.tail(7).itertuples()))
        bs = await q("各院區近期 P90", f"SELECT site, COUNT(*) AS n, percentile_cont(0.9) WITHIN GROUP (ORDER BY infer_seconds) AS p90 FROM inferences "
                                      f"WHERE {wh} AND infer_seconds IS NOT NULL AND study_date BETWEEN {c.d(c.r0)} AND {c.d(c.r1)} GROUP BY 1 ORDER BY 2 DESC")
        bs = bs[bs.n >= 3]
        if len(bs) >= 2:
            facts.append("各院區近期 P90：" + "、".join(f"{r.site} {r.p90:.0f} 秒" for r in bs.head(6).itertuples()))
            hints["widespread"] = bool(bs.p90.min() >= 1.5 * f["baseline"])

    elif k == "invariant":
        proc = ent["procedure_code"]
        mm = await q("各模型涵蓋的檢查數", f"SELECT model, COUNT(DISTINCT exam_id) AS exams FROM inferences WHERE procedure_code = {lit(proc)} "
                                         f"AND study_date BETWEEN {c.d(c.r0)} AND {c.d(c.r1)} GROUP BY 1 ORDER BY 2")
        tot = await q("檢查總數", f"SELECT COUNT(DISTINCT exam_id) AS exams FROM inferences WHERE procedure_code = {lit(proc)} "
                                 f"AND study_date BETWEEN {c.d(c.r0)} AND {c.d(c.r1)}")
        T = int(tot.iloc[0].exams)
        mm["missing"] = T - mm["exams"]
        worst = mm.sort_values("missing", ascending=False).iloc[0]
        facts.append(f"近期 {T} 個檢查中，各模型漏跑數：" + "、".join(f"{r.model} {r.missing}" for r in mm.itertuples()))
        hints["missing_model"] = worst.model
        dd = await q("漏跑模型每日", f"SELECT study_date, COUNT(DISTINCT exam_id) AS exams FROM inferences WHERE procedure_code = {lit(proc)} "
                                   f"AND model = {lit(worst.model)} AND study_date BETWEEN {t0} AND {c.d(c.r1)} GROUP BY 1 ORDER BY 1")
        da = await q("全部檢查每日", f"SELECT study_date, COUNT(DISTINCT exam_id) AS exams FROM inferences WHERE procedure_code = {lit(proc)} "
                                   f"AND study_date BETWEEN {t0} AND {c.d(c.r1)} GROUP BY 1 ORDER BY 1")
        j = da.merge(dd, on="study_date", how="left", suffixes=("_all", "_m")).fillna(0)
        j["miss"] = j.exams_all - j.exams_m
        first = j[j.miss > 0].study_date.min()
        if isinstance(first, str):
            facts.append(f"{worst.model} 從 {first} 開始出現漏跑；最近 7 天每日漏跑：" +
                         "、".join(f"{str(r.study_date)[5:]} {int(r.miss)}" for r in j.tail(7).itertuples()))
            hints["since"] = first

    elif k == "stuck":
        m = ent["model"]
        st = await q("卡住清單", f"SELECT id, exam_id, site, inference_status, platform_time, data_storage_time FROM inferences "
                                f"WHERE model = {lit(m)} AND status_category = '{CAT_PROC}' AND platform_time < TIMESTAMP '{c.stuck_cutoff}' "
                                f"ORDER BY platform_time LIMIT 10")
        facts.append("卡住的推論編號：" + "、".join(f"#{r.id}（{r.site}，{str(r.platform_time)[5:16]} 進平台）" for r in st.itertuples()))
        if st.data_storage_time.notna().all():
            facts.append("這些推論都已經寫入結果資料（data_storage_time 有值），但影像結果沒有上傳完成")
            hints["stage"] = "image_upload"
        hints["sites"] = sorted(set(st.site))

    elif k == "invalid_time":
        st = await q("時間不合理的推論", f"SELECT model, site, study_time, platform_time FROM inferences "
                                        f"WHERE study_time > TIMESTAMP '{(c.D + timedelta(days=1)).isoformat()} 00:00:00' ORDER BY study_time LIMIT 10")
        if not st.empty:
            facts.append("涉及模型：" + "、".join(sorted(set(st.model))) + "；院區：" + "、".join(sorted(set(st.site))))
            facts.append("例：檢查時間 " + str(st.iloc[0].study_time)[:16] + "，進平台時間 " + str(st.iloc[0].platform_time)[:16])
            hints["platform_ok"] = bool((pd.to_datetime(st.platform_time) <= pd.Timestamp(c.D) + pd.Timedelta(days=1)).all())

    elif k == "unregistered_model":
        m = ent["model"]
        st = await q("未登記模型概況", f"SELECT MIN(study_date) AS first_seen, MAX(study_date) AS last_seen, COUNT(*) AS n, "
                                      f"SUM(CASE WHEN status_category = '{CAT_NFI}' THEN 1 ELSE 0 END) AS nfi FROM inferences WHERE model = {lit(m)}")
        r = st.iloc[0]
        facts.append(f"全部歷史：{r.first_seen} ~ {r.last_seen}，共 {r.n} 筆，NFI {pct(r.nfi / r.n)}")

    elif k == "new_category":
        dim, val = next(iter(ent.items()))
        st = await q("新類別值分布", f"SELECT model, site, COUNT(*) AS n, MIN(platform_time) AS first_t FROM inferences "
                                    f"WHERE {dim} = {lit(val)} GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10")
        facts.append("出現在：" + "、".join(f"{r.model} @{r.site} ×{r.n}" for r in st.itertuples()))
        hints["models"] = sorted(set(st.model))

    return facts, drill, hints


# ---------------- 寫手 ----------------
SEV = {"volume_drop": "高", "stuck": "高", "rate_shift": "中", "invariant": "中", "latency_shift": "中",
       "new_message": "中", "unregistered_model": "中", "nfi_reason_mix": "中", "invalid_time": "低", "new_category": "低"}

KIND_ZH = {"rate_shift": "比例偏移", "volume_drop": "量驟降", "new_message": "新訊息", "new_category": "新類別值",
           "invalid_time": "不合理時間", "invariant": "應跑模型數不符", "unregistered_model": "未登記模型",
           "latency_shift": "耗時變長", "stuck": "卡住", "nfi_reason_mix": "NFI 原因改變"}


def severity(f):
    s = SEV.get(f["kind"], "中")
    if f["kind"] == "rate_shift" and f["magnitude"] >= 0.3:
        s = "高"
    if f["kind"] == "volume_drop" and f["magnitude"] < 0.8:
        s = "中"
    return s


def template_narrative(f, hints):
    k = f["kind"]
    cause, steps = "", []
    if k in ("rate_shift", "nfi_reason_mix"):
        tr = hints.get("top_reason")
        if tr:
            cause = f"近期 NFI 大多是「{tr[:60]}」（占 {pct(hints.get('top_reason_share', 0))}），問題比較可能在前處理或上游資料，而不是模型本身。"
        if hints.get("widespread"):
            cause += " 各院區都偏高，傾向共同環節（資料來源、前處理程式版本）而不是單一院區。"
        steps = ["確認變化點前後，前處理程式、資料來源或上游系統是否有改版或異動",
                 "抽幾筆 NFI 個案，對照原始資料確認「不適用」判斷是否正確"]
        if "site" in f["entity"]:
            steps.insert(0, f"只有 {f['entity']['site']} 院區異常：先問該院區設備、檢查流程或代碼對照是否有異動")
    elif k == "volume_drop":
        cause = f"最後一筆資料在 {hints.get('last_seen', '?')}，之後完全沒有資料，比較像介接或上游傳送中斷，而不是自然波動。"
        if hints.get("site_partially_alive"):
            cause += " 同院區其他類型仍有資料，所以不是整個院區斷線，問題範圍限於這一類資料的傳送路徑。"
        steps = ["查該院區這類資料的傳送程式／介接服務是否還在跑", "與院區確認這幾天是否真的有做這類檢查（排除停診、設備維修）"]
    elif k == "new_message":
        cause = "以前沒出現過的訊息，通常代表上游資料出現新格式、或程式新版本的新錯誤路徑。"
        if hints.get("loc_code"):
            cause += f" 訊息提到地點代碼 {hints['loc_code']}，可能是代碼對照表缺這一筆。"
        steps = ["找負責這段前處理的人確認訊息意義", "若是代碼對照問題，補對照表後重跑受影響的檢查"]
    elif k == "latency_shift":
        cause = "各院區都變慢，傾向是推論服務本身（資源、佇列、模型版本），而不是單一院區資料。" if hints.get("widespread") else "只有部分院區變慢，先看這些院區的影像大小或傳輸。"
        steps = ["看推論主機 GPU / 佇列長度在起始日前後的變化", "確認是否有模型或容器版本更新"]
    elif k == "invariant":
        cause = f"漏跑集中在「{hints.get('missing_model', '?')}」，其他模型正常，傾向是這個模型的派送或服務異常。"
        steps = [f"查 {hints.get('missing_model', '')} 的服務日誌，確認是否有收到這些檢查", "漏跑的檢查是否需要補跑"]
    elif k == "stuck":
        if hints.get("stage") == "image_upload":
            cause = "結果資料已寫入、但影像結果上傳沒完成，卡在最後上傳那一步。"
        steps = ["依推論編號查平台日誌，確認卡在哪個步驟", "處理後確認是否需要重送 HIS"]
    elif k == "invalid_time":
        if hints.get("platform_ok"):
            cause = "進平台時間正常、只有檢查時間異常，比較像來源系統的檢查時間欄位填錯，不是平台的問題。"
        else:
            cause = "連進平台時間也不合理，要同時檢查平台主機時鐘與來源系統。"
        steps = ["請來源系統（PACS / HIS）確認這幾筆的檢查時間", "這些資料會讓「依月份統計」出現奇怪的月份，報表時先排除"]
    elif k == "unregistered_model":
        cause = "推論有在跑，但沒有登記在 model_status，所以平台現有的狀態監控（Monitor）看不到它，停掉也不會通知。"
        steps = ["在 model_status 補登記這個模型", "確認 Monitor 的通知對象"]
    elif k == "new_category":
        cause = "出現以前沒有的值，常見原因是程式改版加了新的狀態碼，或是異常流程停在中間狀態。"
        if hints.get("models"):
            cause += f" 只出現在 {'、'.join(hints['models'])}。"
        steps = ["確認這個值的意義是否已經寫進語意層的狀態規則", "如果是卡住的中間狀態，跟「卡住」那張卡一起處理"]
    return {"likely_cause": cause or "（需要人判斷）", "next_steps": steps, "writer": "template"}


def llm_narrative(f, facts, hints):
    base = os.environ.get("EXPLORER_LLM_BASE_URL")
    if not base:
        return None
    sys_p = ("你是醫院 AI 推論平台的維運分析師。只能根據使用者提供的事實寫，不可以自己加數字或事實。"
             "回傳 JSON：{\"likely_cause\": 一到兩句推測原因（要寫「可能」），\"next_steps\": [2~3 個具體的下一步]}")
    user_p = json.dumps({"發現": f["title"], "類型": KIND_ZH.get(f["kind"]), "事實": facts, "線索": hints}, ensure_ascii=False, default=str)
    try:
        r = httpx.post(base.rstrip("/") + "/chat/completions", timeout=60,
                       headers={"Authorization": f"Bearer {os.environ.get('EXPLORER_LLM_API_KEY', 'none')}"},
                       json={"model": os.environ.get("EXPLORER_LLM_MODEL", "default"), "temperature": 0.1,
                             "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}]})
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        js = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
        # 防編造：LLM 寫出的數字必須出現在事實或標題裡
        allowed = f["title"] + json.dumps(facts, ensure_ascii=False) + json.dumps(hints, ensure_ascii=False, default=str)
        nums = re.findall(r"\d+(?:\.\d+)?", js.get("likely_cause", "") + " ".join(js.get("next_steps", [])))
        bad = [n for n in nums if len(n.replace(".", "")) >= 2 and n not in allowed]
        if bad:
            return {"rejected": f"LLM 敘述含證據裡沒有的數字 {bad[:3]}，改用模板"}
        return {"likely_cause": js["likely_cause"], "next_steps": js["next_steps"][:3], "writer": "llm"}
    except Exception as e:  # noqa
        return {"rejected": f"LLM 呼叫失敗（{type(e).__name__}），改用模板"}


async def write_card(w, f, c):
    try:
        facts, drill, hints = await investigate(w, f, c)
    except Exception as e:  # noqa
        facts, drill, hints = [f"（追查失敗：{e}）"], [], {}
    nar = llm_narrative(f, facts, hints)
    note = None
    if nar is None or "rejected" in nar:
        note = nar.get("rejected") if nar else None
        nar = template_narrative(f, hints)
    return {
        "signature": f["signature"], "kind": KIND_ZH.get(f["kind"], f["kind"]), "severity": severity(f), "entity": f["entity"],
        "models": sorted(set(hints.get("models", [])) | ({f["entity"]["model"]} if "model" in f["entity"] else set())),
        "title": f["title"], "since": hints.get("since"), "facts": facts,
        "likely_cause": nar["likely_cause"], "next_steps": nar["next_steps"], "writer": nar["writer"], "writer_note": note,
        "evidence": f["evidence"], "drill": drill,
    }


def link_related(cards):
    """同一個模型的發現互相標註，讓人一次看到同一個根因的不同面向"""
    for a in cards.values():
        a["related"] = [b["title"] for b in cards.values() if b is not a and set(a["models"]) & set(b["models"])]
