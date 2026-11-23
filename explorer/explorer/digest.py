"""每日摘要（Markdown）。新的、惡化的、復發的寫成完整發現卡；其他只列一行。"""
from .investigator import KIND_ZH

SEV_ORDER = {"高": 0, "中": 1, "低": 2}


def card_md(card, tag):
    lines = [f"### [{card['severity']}] {card['title']}",
             f"`{tag}` · 類型：{card['kind']}" + (f" · 起始：{card['since']}" if card.get("since") else "") + f" · 代號：`{card['signature']}`", ""]
    lines += ["**查到的事實**（每個數字都來自下方 SQL）"] + [f"- {x}" for x in card["facts"]] + [""]
    lines += [f"**可能原因（推測）**：{card['likely_cause']}", ""]
    if card["next_steps"]:
        lines += ["**建議下一步**"] + [f"{i + 1}. {s}" for i, s in enumerate(card["next_steps"])] + [""]
    if card.get("related"):
        lines += ["**同一模型的其他發現**：" + "；".join(card["related"]), ""]
    if card.get("writer_note"):
        lines += [f"> 註：{card['writer_note']}", ""]
    lines += ["<details><summary>證據 SQL</summary>", ""]
    for e in card["evidence"]:
        lines += [f"- 偵測：{e['label']}：" + "，".join(f"{k} {v}" for k, v in e["values"].items()), "", "```sql", e["sql"], "```"]
    for d in card["drill"]:
        lines += [f"- 追查：{d['label']}", "", "```sql", d["sql"], "```"]
    lines += ["</details>", ""]
    return "\n".join(lines)


def render(run_date, groups, cards, det_status, wren_log, mode_note):
    n_alert = len(groups["new"]) + len(groups["worse"]) + len(groups["reopened"])
    total_sec = sum(x["sec"] for x in wren_log)
    out = [f"# 推論平台自動巡檢摘要 {run_date}", "",
           f"偵測到 {sum(len(groups[k]) for k in ('new', 'worse', 'reopened', 'ongoing', 'suppressed'))} 件；"
           f"**需要看的 {n_alert} 件**（新 {len(groups['new'])}、惡化 {len(groups['worse'])}、復發 {len(groups['reopened'])}），"
           f"持續中 {len(groups['ongoing'])}、已知或誤報而略過 {len(groups['suppressed'])}、已恢復 {len(groups['resolved'])}。",
           f"共查詢 {len(wren_log)} 次、{total_sec:.1f} 秒，全部經 Wren MCP 唯讀執行。{mode_note}", ""]
    rows = []
    for tag, key in (("新", "new"), ("惡化", "worse"), ("復發", "reopened")):
        for f in groups[key]:
            c = cards[f["signature"]]
            rows.append((SEV_ORDER[c["severity"]], tag, c))
    rows.sort(key=lambda x: (x[0], x[1]))
    if rows:
        out += ["| 嚴重度 | 狀態 | 類型 | 摘要 |", "|---|---|---|---|"]
        out += [f"| {c['severity']} | {tag} | {c['kind']} | {c['title']} |" for _, tag, c in rows] + [""]
        out += ["## 需要看的發現", ""] + [card_md(c, tag) for _, tag, c in rows]
    if groups["ongoing"]:
        out += ["## 持續中（已報過、還沒有人回覆）", ""] + [f"- {f['title']}（首次 {f['registry']['first_seen']}）`{f['signature']}`" for f in groups["ongoing"]] + [""]
    if groups["suppressed"]:
        out += ["## 已略過（標記為已知或誤報，且沒有惡化）", ""] + \
               [f"- {f['title']}（{'已知' if f['registry']['status'] == 'known' else '誤報'}：{f['registry'].get('note') or ''}）" for f in groups["suppressed"]] + [""]
    if groups["resolved"]:
        out += ["## 已恢復（連續幾次沒再偵測到）", ""] + [f"- {r['title']}" for r in groups["resolved"]] + [""]
    out += ["## 偵測器執行狀況", "", "| 偵測器 | 狀態 | 件數 |", "|---|---|---|"] + [f"| {n} | {s} | {k} |" for n, s, k in det_status] + [""]
    out += ["回覆方式：`python run.py feedback <代號開頭> known|false_positive \"備註\"`", ""]
    return "\n".join(out)
