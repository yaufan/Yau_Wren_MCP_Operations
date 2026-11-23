-- 合成測試資料（只給本機演練用，不要在正式庫執行）
-- 結構依「推論平台資料表說明 2026-11-17」；狀態值比例參考 2026-11-19 正式環境盤點。
DROP TABLE IF EXISTS inference_record, patient_info, model_status CASCADE;

CREATE TABLE patient_info (
  id serial PRIMARY KEY,
  chtno varchar(50) NOT NULL,
  pacsno varchar(100) NOT NULL,
  locno varchar(5),
  filename varchar(255),
  filetype varchar(30),
  procedure_code varchar(30),
  platform_time timestamp,
  study_time timestamp,
  UNIQUE (chtno, pacsno, study_time, platform_time)
);
CREATE TABLE inference_record (
  id serial PRIMARY KEY,
  patient_id int NOT NULL REFERENCES patient_info(id),
  chtno varchar(50) NOT NULL,
  model varchar(50),
  inference_status varchar(20),
  data_storage_time timestamp,
  image_storage_time timestamp,
  file_path varchar(255),
  info_log text,
  UNIQUE (patient_id, model, inference_status)
);
CREATE TABLE model_status (
  id serial PRIMARY KEY,
  model varchar(50) NOT NULL UNIQUE,
  status varchar(20) NOT NULL,
  ctrl_status varchar(20) NOT NULL,
  change_time timestamp
);

SELECT setseed(0.42);

-- ECG：4000 份心電圖，各跑 3 個模型（2025-06 ~ 2026-10）
INSERT INTO patient_info (chtno, pacsno, locno, filename, filetype, procedure_code, platform_time, study_time)
SELECT lpad((10000000 + (random()*8000)::int)::text, 8, '0'),
       'pacsno',
       (ARRAY['S07','MAIN','S08','S11'])[1 + (random()*3)::int],
       'MUSE_' || g || '.xml', 'XML', 'PX01',
       t + interval '2 minutes', t
FROM (SELECT g, timestamp '2025-06-01' + random() * interval '515 days' AS t
      FROM generate_series(1, 4000) g) s;

INSERT INTO inference_record (patient_id, chtno, model, inference_status, data_storage_time, image_storage_time, file_path, info_log)
SELECT p.id, p.chtno, m.model,
       CASE WHEN r < 0.016 THEN 'NFI'
            WHEN p.study_time < '2025-12-01' THEN '5'      -- 舊版代碼
            ELSE '4.2' END,
       CASE WHEN r < 0.016 THEN NULL ELSE p.platform_time + interval '90 seconds' END,
       CASE WHEN r < 0.016 THEN NULL ELSE p.platform_time + interval '150 seconds' END,
       '/ecg/output/' || m.k || '/' || p.id || '.json',
       CASE WHEN r < 0.016 THEN 'XML 規格不符合模型' ELSE '數據與圖片檔案上傳完成' END
FROM patient_info p
CROSS JOIN (VALUES ('ECG_Abnormality_14','e14'),('ECG_LV_Function','ef'),('ECG_AFib','af')) m(model,k)
CROSS JOIN LATERAL (SELECT random() + 0 * p.id + 0 * length(m.model) AS r) x
WHERE p.filetype = 'XML';

-- 心室收縮功能：測試期（文字狀態），另外 700 份心電圖，2025-03 ~ 2025-05
WITH ins AS (
  INSERT INTO patient_info (chtno, pacsno, locno, filename, filetype, procedure_code, platform_time, study_time)
  SELECT lpad((10000000 + (random()*8000)::int)::text, 8, '0'), 'pacsno', 'MAIN',
         'MUSE_T' || g || '.xml', 'XML', 'PX01', t + interval '2 minutes', t
  FROM (SELECT g, timestamp '2025-03-01' + random() * interval '90 days' AS t
        FROM generate_series(1, 700) g) s
  RETURNING id, chtno, platform_time)
INSERT INTO inference_record (patient_id, chtno, model, inference_status, data_storage_time, info_log)
SELECT id, chtno, 'ECG_LV_Function',
       CASE WHEN r < 0.03 THEN 'error' WHEN r < 0.05 THEN 'invalid' ELSE 'success' END,
       platform_time + interval '60 seconds',
       CASE WHEN r < 0.03 THEN 'timeout' WHEN r < 0.05 THEN 'XML 規格不符合模型' ELSE '推論成功' END
FROM ins CROSS JOIN LATERAL (SELECT random() + 0 * ins.id AS r) x;

-- 影像類模型（DICOM）：各自的病人／檢查
CREATE TEMP TABLE img_spec(model text, n int, nfi numeric, proc text, d0 date);
INSERT INTO img_spec VALUES
  ('IMG_Abdominal_Trauma', 600, 0.51, 'PX03', '2025-09-01'),
  ('IMG_Dental_Pano',                             400, 0.073,'PX02', '2026-01-01'),
  ('CLIN_Bleeding_Risk',           80, 0.988,'PX04', '2026-06-01'),
  ('BONE_KUB',                  60, 0.022,'PX05', '2026-09-01'),
  ('BONE_LSpine',              30, 0.01, 'PX06', '2026-09-01'),
  ('BONE_Pelvis',               20, 0.059,'PX07', '2026-09-01'),
  ('BONE Ver 2 (Hand)',                  8, 0.125,'32015C', '2026-09-01');

DO $$
DECLARE s record;
BEGIN
  FOR s IN SELECT * FROM img_spec LOOP
    WITH ins AS (
      INSERT INTO patient_info (chtno, pacsno, locno, filename, filetype, procedure_code, platform_time, study_time)
      SELECT lpad((10000000 + (random()*8000)::int)::text, 8, '0'),
             '1.2.840.' || (random()*1e9)::bigint, (ARRAY['S07','MAIN','S08','S11'])[1 + (random()*3)::int],
             'IMG_' || g || '.dcm', 'DICOM', s.proc,
             t + interval '5 minutes', t
      FROM (SELECT g, s.d0 + random() * (date '2026-11-18' - s.d0) * interval '1 day' AS t
            FROM generate_series(1, s.n) g) z
      RETURNING id, chtno, platform_time)
    INSERT INTO inference_record (patient_id, chtno, model, inference_status, data_storage_time, image_storage_time, file_path, info_log)
    SELECT id, chtno, s.model,
           CASE WHEN r < s.nfi THEN 'NFI' ELSE '4.2' END,
           CASE WHEN r < s.nfi THEN NULL ELSE platform_time + interval '4 minutes' END,
           CASE WHEN r < s.nfi THEN NULL ELSE platform_time + interval '6 minutes' END,
           '/img/out/' || id || '.json',
           CASE WHEN r < s.nfi THEN '影像序列不符合模型規格' ELSE '數據與圖片檔案上傳完成' END
    FROM ins CROSS JOIN LATERAL (SELECT random() + 0 * ins.id AS r) x;
  END LOOP;
END $$;

-- 卡住的件：各 1 筆
UPDATE inference_record SET inference_status = '2', data_storage_time = NULL, image_storage_time = NULL, info_log = '完成作業分配'
WHERE id = (SELECT min(id) FROM inference_record WHERE model = 'ECG_LV_Function' AND inference_status = '4.2');
UPDATE inference_record SET inference_status = '3', image_storage_time = NULL, info_log = '完成結果暫存'
WHERE id = (SELECT min(id) FROM inference_record WHERE model = 'ECG_AFib' AND inference_status = '4.2');

-- 整合測試殘留（不在 model_status）
INSERT INTO inference_record (patient_id, chtno, model, inference_status, info_log)
SELECT (SELECT min(id) FROM patient_info) + g, '00000000', 'IT_2fd5b8b58836_' || g, '4.2', 'integration test'
FROM generate_series(1, 2) g;

INSERT INTO model_status (model, status, ctrl_status, change_time) VALUES
 ('ECG_Abnormality_14','running','running','2026-03-01 09:00'),
 ('ECG_LV_Function','running','running','2026-03-01 09:00'),
 ('ECG_AFib','running','running','2026-03-01 09:00'),
 ('IMG_Abdominal_Trauma','running','running','2026-07-01 16:05'),
 ('IMG_Dental_Pano','testing','testing','2026-08-21 02:56'),
 ('BONE_KUB','testing','testing','2026-11-09 03:34'),
 ('BONE_LSpine','testing','testing','2026-11-09 03:34'),
 ('BONE Ver 2 (Hand)','testing','testing','2026-11-09 03:34'),
 ('BONE_Pelvis','testing','testing','2026-11-09 03:34');
