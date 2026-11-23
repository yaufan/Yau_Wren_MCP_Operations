"""
generate.py — 產生模擬推論平台資料（結構同正式庫 public.patient_info / inference_record / model_status），
並埋入 10 個已知問題。標準答案寫到 ground_truth.json，供命中率評估。

用法：python generate.py --dsn "host=... dbname=sim_infer user=..." [--asof 2026-11-22] [--days 70]
全部是假資料：病歷號為隨機數字，不含任何真實資訊。
"""
import argparse
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg

ECG_MODELS = ["ECG_Abnormality_14", "ECG_LV_Function", "ECG_AFib"]
SITES = ["S01", "S02", "S03", "S04", "S05", "S06", "S07", "S08", "S09", "S10"]
SITE_W = [30, 18, 10, 6, 5, 5, 5, 4, 3, 2]
IMG = {  # 模型: (每日量, NFI 基準, 檢查代碼, 中位耗時秒)
    "IMG_Dental_Pano": (30, 0.07, "PX02", 110),
    "IMG_Abdominal_Trauma": (12, 0.10, "PX03", 1500),
    "CLIN_Bleeding_Risk": (4, 0.30, "PX04", 15),
    "BONE_KUB": (6, 0.02, "PX05", 220),
    "BONE_LSpine": (2, 0.01, "PX06", 530),
    "BONE_Pelvis": (1.5, 0.05, "PX07", 380),
}
NFI_MSG = {"default": "資料規格不符合模型",
           "CLIN_Bleeding_Risk": "前處理或投遞失敗: Vital sign 未通過: 查詢截止日前 90 天內沒有 Vital sign 資料，不通過",
           "IMG_Abdominal_Trauma": "前處理 pipeline 未產出最終結果"}
HOUR_W = [1, 1, 1, 1, 1, 2, 4, 8, 12, 14, 14, 12, 8, 12, 13, 12, 10, 7, 5, 4, 3, 2, 2, 1]

GROUND_TRUTH = [
    {"id": "G1", "desc": "CLIN_Bleeding_Risk NFI 自 14 天前起升到約 99%（缺 Vital sign）", "match": {"model": "CLIN_Bleeding_Risk", "kind": ["rate_shift", "nfi_reason_mix"]}},
    {"id": "G2", "desc": "IMG_Abdominal_Trauma NFI 自 10 天前起升到約 50%（前處理 pipeline 未產出）", "match": {"model": "IMG_Abdominal_Trauma", "kind": ["rate_shift", "nfi_reason_mix"]}},
    {"id": "G3", "desc": "IMG_Dental_Pano 在 S11 院區近 7 天 NFI 升到約 40%（其他院區正常）", "match": {"model": "IMG_Dental_Pano", "site": "S11", "kind": ["rate_shift"]}},
    {"id": "G4", "desc": "S08 院區心電圖近 3 天量歸零（介接中斷）", "match": {"site": "S08", "kind": ["volume_drop"]}},
    {"id": "G5", "desc": "近 3 天出現新錯誤訊息：無法由 loc='X9999' 判斷院區代碼", "match": {"kind": ["new_message"], "text": "X9999"}},
    {"id": "G6", "desc": "檢查時間在未來（2177 年）", "match": {"kind": ["invalid_time"]}},
    {"id": "G7", "desc": "CLIN_Bleeding_Risk未登記在 model_status", "match": {"model": "CLIN_Bleeding_Risk", "kind": ["unregistered_model"]}},
    {"id": "G8", "desc": "近 5 天約 6% 心電圖只跑了 2 個模型（心房顫動漏跑）", "match": {"kind": ["invariant"]}},
    {"id": "G9", "desc": "BONE_KUB 近 7 天耗時 P90 變成約 4 倍", "match": {"model": "BONE_KUB", "kind": ["latency_shift"]}},
    {"id": "G10", "desc": "有推論卡在處理中超過 24 小時（ECG_Abnormality_14，狀態 3）", "match": {"kind": ["stuck", "new_category"]}},
]
G11 = {"id": "G11", "desc": "（第二天新發生）ECG_LV_Function在 S01 院區昨天起 NFI 升到約 30%（ECG 導程數不足）",
       "match": {"model": "ECG_LV_Function", "kind": ["rate_shift", "new_message", "nfi_reason_mix"]}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--asof", default=date.today().isoformat())
    ap.add_argument("--days", type=int, default=70)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--scenario", choices=["day1", "day2", "clean"], default="day1", help="day2 多埋一個新問題 G11；clean 完全不埋問題（量誤報用）")
    args = ap.parse_args()
    rnd = random.Random(args.seed)
    inj = args.scenario != "clean"
    asof = datetime.fromisoformat(args.asof) + timedelta(hours=8)  # 模擬「今天早上 8 點」產生
    start = asof - timedelta(days=args.days)

    pi_rows, ir_rows = [], []
    pid, iid = 0, 0

    def chtno():
        return f"{rnd.randint(10000000, 19999999)}"

    def t_in_day(d):
        h = rnd.choices(range(24), HOUR_W)[0]
        return datetime(d.year, d.month, d.day, h, rnd.randint(0, 59), rnd.randint(0, 59))

    def add_exam(st, site, ftype, proc):
        nonlocal pid
        pid += 1
        pt = st + timedelta(seconds=rnd.randint(60, 240))
        pi_rows.append((pid, chtno(), "pacsno" if ftype == "XML" else f"1.2.840.{rnd.randint(1, 10**9)}",
                        site, f"F{pid}", ftype, pt, st, proc))
        return pid, pt

    def add_inf(p, ch, model, status, pt, dur, log):
        nonlocal iid
        iid += 1
        ds = pt + timedelta(seconds=dur * 0.7) if status in ("4.2", "5") else None
        im = pt + timedelta(seconds=dur) if status in ("4.2", "5") else None
        if status == "3":
            ds = pt + timedelta(seconds=dur * 0.7)
        ir_rows.append((iid, p, ch, model, status, ds, im, f"/out/{iid}.json", log))

    def dur_sample(med):
        return max(5, rnd.lognormvariate(0, 0.6) * med)

    day = start.date()
    while day <= asof.date():
        age = (asof.date() - day).days  # 0 = 今天
        wd = day.weekday()
        scale = 0.55 if wd >= 5 else 1.0
        # ---- 心電圖 ----
        n_ecg = int(rnd.gauss(380, 25) * scale)
        for _ in range(max(n_ecg, 0)):
            site = rnd.choices(SITES, SITE_W)[0]
            if inj and site == "S08" and age <= 2:          # G4：S08 近 3 天介接中斷
                continue
            st = t_in_day(day)
            if st > asof:
                continue
            p, pt = add_exam(st, site, "XML", "PX01")
            ch = pi_rows[-1][1]
            nfi = rnd.random() < 0.016
            for m in ECG_MODELS:
                if inj and m == "ECG_AFib" and age <= 4 and rnd.random() < 0.06:   # G8：漏跑
                    continue
                msg = NFI_MSG["default"]
                if (args.scenario == "day2" and m == "ECG_LV_Function" and site == "S01"
                        and age <= 1 and rnd.random() < 0.30):            # G11（只在 day2）
                    nfi_m, msg = True, "前處理失敗: ECG 導程數不足 (got 8, need 12)"
                else:
                    nfi_m = nfi
                status = "NFI" if nfi_m else ("5" if age > 60 else "4.2")
                add_inf(p, ch, m, status, pt, dur_sample(132), msg if nfi_m else "數據與圖片檔案上傳完成")
        # ---- 影像類 ----
        for m, (daily, nfi_base, proc, med) in IMG.items():
            n = rnd.gauss(daily, max(daily * 0.25, 0.5)) * scale
            n = max(0, int(round(n)))
            for _ in range(n):
                site = rnd.choices(SITES, SITE_W)[0]
                if m == "IMG_Dental_Pano" and rnd.random() < 0.25:
                    site = "S11"                      # IMG_Dental_Pano 有一部分固定在 S11
                st = t_in_day(day)
                if st > asof:
                    continue
                p, pt = add_exam(st, site, "DICOM", proc)
                ch = pi_rows[-1][1]
                p_nfi = nfi_base
                msg = NFI_MSG.get(m, NFI_MSG["default"])
                if inj and m == "CLIN_Bleeding_Risk" and age <= 13:   # G1
                    p_nfi = 0.99
                if inj and m == "IMG_Abdominal_Trauma" and age <= 9:   # G2
                    p_nfi = 0.50
                if inj and m == "IMG_Dental_Pano" and site == "S11" and age <= 6:   # G3
                    p_nfi = 0.40
                d = dur_sample(med)
                if inj and m == "BONE_KUB" and age <= 6:   # G9
                    d *= 4
                if rnd.random() < p_nfi:
                    if inj and m == "CLIN_Bleeding_Risk" and age <= 2 and rnd.random() < 0.5:   # G5 新錯誤訊息
                        msg = "前處理或投遞失敗: 無法由 loc='X9999' 判斷院區代碼"
                    add_inf(p, ch, m, "NFI", pt, d, msg)
                else:
                    add_inf(p, ch, m, "4.2", pt, d, "數據與圖片檔案上傳完成")
        day += timedelta(days=1)

    # G6：未來日期 9 筆（2177 年）
    for k in range(9 if inj else 0):      # 來源系統把檢查時間填錯；進平台時間是正常的
        p, _ = add_exam(datetime(2177, 2, 3 + k, 10, 0, 0), "S01", "DICOM", "PX02")
        pt = asof - timedelta(days=20 - 2 * k, hours=3)
        pi_rows[-1] = pi_rows[-1][:6] + (pt,) + pi_rows[-1][7:]
        add_inf(p, pi_rows[-1][1], "IMG_Dental_Pano", "4.2", pt, 100, "數據與圖片檔案上傳完成")
    # G5 補強：隨機注入在週末量太少（第一次只產生 1 筆，低於「至少 2 筆」門檻），再固定補 5 筆
    for k in range(5 if inj else 0):
        st = asof - timedelta(days=1 + (k % 2), hours=3 + k)
        p, pt = add_exam(st, "S01", "DICOM", "PX04")
        add_inf(p, pi_rows[-1][1], "CLIN_Bleeding_Risk", "NFI", pt, 15, "前處理或投遞失敗: 無法由 loc='X9999' 判斷院區代碼")
    # G10：卡在處理中超過 24 小時（ECG_Abnormality_14，狀態 3）
    for k in range(4 if inj else 0):
        st = asof - timedelta(days=2, hours=k)
        p, pt = add_exam(st, "S02", "XML", "PX01")
        add_inf(p, pi_rows[-1][1], "ECG_Abnormality_14", "3", pt, 120, "完成結果暫存")
    # 測試期與測試模型（語意層應排除，不算異常）
    for k in range(300):
        st = start + timedelta(days=rnd.randint(0, 10), hours=10)
        p, pt = add_exam(st, "S01", "XML", "PX01")
        add_inf(p, pi_rows[-1][1], "ECG_LV_Function", rnd.choice(["success", "success", "error", "invalid"]), pt, 60, "test")
    for m in ["TEST_model", "TEST_AI_MODEL"]:
        for k in range(5):
            p, pt = add_exam(start + timedelta(days=1, hours=k), "S01", "XML", "PX01")
            add_inf(p, pi_rows[-1][1], m, "completed", pt, 10, "test")

    with psycopg.connect(args.dsn, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS inference_record, patient_info, model_status CASCADE")
        c.execute("""CREATE TABLE patient_info (id int PRIMARY KEY, chtno varchar(50) NOT NULL, pacsno varchar(100) NOT NULL,
            locno varchar(5), filename varchar(255), filetype varchar(30), platform_time timestamp, study_time timestamp, procedure_code varchar(30))""")
        c.execute("""CREATE TABLE inference_record (id int PRIMARY KEY, patient_id int NOT NULL REFERENCES patient_info(id), chtno varchar(50) NOT NULL,
            model varchar(50), inference_status varchar(20), data_storage_time timestamp, image_storage_time timestamp,
            file_path varchar(255), info_log text)""")
        c.execute("""CREATE TABLE model_status (id serial PRIMARY KEY, model varchar(50) NOT NULL UNIQUE, status varchar(20) NOT NULL,
            ctrl_status varchar(20) NOT NULL, change_time timestamp)""")
        with c.cursor() as cur:
            with cur.copy("COPY patient_info (id, chtno, pacsno, locno, filename, filetype, platform_time, study_time, procedure_code) FROM STDIN") as cp:
                for r in pi_rows:
                    cp.write_row(r)
            with cur.copy("COPY inference_record (id, patient_id, chtno, model, inference_status, data_storage_time, image_storage_time, file_path, info_log) FROM STDIN") as cp:
                for r in ir_rows:
                    cp.write_row(r)
        for m in ECG_MODELS + [k for k in IMG if (k != "CLIN_Bleeding_Risk" or not inj)]:   # G7：CLIN_Bleeding_Risk沒登記
            c.execute("INSERT INTO model_status (model, status, ctrl_status, change_time) VALUES (%s,'running','running',%s)", (m, start))
        c.execute("CREATE INDEX ON patient_info (study_time)")
        c.execute("CREATE INDEX ON inference_record (patient_id)")
        c.execute("ANALYZE")
    gt = [] if not inj else GROUND_TRUTH + ([G11] if args.scenario == "day2" else [])
    Path(__file__).with_name(f"ground_truth_{args.scenario}.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"模擬資料：檢查 {len(pi_rows):,}、推論 {len(ir_rows):,}；as-of {args.asof}；情境 {args.scenario}，埋入 {len(gt)} 個問題")


if __name__ == "__main__":
    main()
