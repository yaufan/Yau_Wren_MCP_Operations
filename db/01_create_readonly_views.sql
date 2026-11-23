-- =====================================================================
-- 01_create_readonly_views.sql
-- 在推論平台的 PostgreSQL（database-api 的 postgres 容器）建立：
--   1. 唯讀帳號 wren_reader（只能讀 wren_ro schema 的三個 view）
--   2. 去識別 view：病歷號換成加鹽雜湊值；不提供 pacsno、filename、file_path
--
-- 執行者：資料庫擁有者／superuser（psql 連到推論平台的資料庫）
--   psql -h <host> -p 5432 -U <管理帳號> -d <資料庫名> \
--        -v reader_pw="'<wren_reader 密碼>'" -v salt="'<一串隨機字>'" \
--        -f 01_create_readonly_views.sql
--
-- 注意：
--   * salt 只存在 wren_secret.salt 表，wren_reader 沒有權限讀，
--     所以看得到 view 定義也推不回病歷號。salt 請另外妥善保存，不要寫進 git。
--   * 可重複執行（CREATE OR REPLACE / IF NOT EXISTS）。
--   * 不會修改 patient_info / inference_record / model_status 任何資料。
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------- 1. 唯讀帳號 ----------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wren_reader') THEN
    CREATE ROLE wren_reader LOGIN;
  END IF;
END $$;
ALTER ROLE wren_reader WITH LOGIN PASSWORD :reader_pw;
ALTER ROLE wren_reader SET default_transaction_read_only = on;
ALTER ROLE wren_reader SET statement_timeout = '60s';   -- 防止 LLM 產生的大查詢拖垮資料庫
ALTER ROLE wren_reader CONNECTION LIMIT 5;

-- ---------- 2. salt（只有管理帳號讀得到） ----------
CREATE SCHEMA IF NOT EXISTS wren_secret;
REVOKE ALL ON SCHEMA wren_secret FROM PUBLIC;
CREATE TABLE IF NOT EXISTS wren_secret.salt (v text NOT NULL);
DELETE FROM wren_secret.salt;
INSERT INTO wren_secret.salt (v) VALUES (:salt);
REVOKE ALL ON wren_secret.salt FROM PUBLIC;

-- ---------- 3. 去識別 view ----------
CREATE SCHEMA IF NOT EXISTS wren_ro;

CREATE OR REPLACE VIEW wren_ro.patient_info AS
SELECT
  p.id,
  md5((SELECT v FROM wren_secret.salt LIMIT 1) || p.chtno) AS patient_key,  -- 去識別病人鍵
  p.locno,
  p.filetype,
  p.procedure_code,
  p.platform_time,
  p.study_time
FROM public.patient_info p;

CREATE OR REPLACE VIEW wren_ro.inference_record AS
SELECT
  r.id,
  r.patient_id,
  r.model,
  r.inference_status,
  r.data_storage_time,
  r.image_storage_time,
  r.info_log
FROM public.inference_record r;

CREATE OR REPLACE VIEW wren_ro.model_status AS
SELECT id, model, status, ctrl_status, change_time
FROM public.model_status;

-- ---------- 4. 權限：wren_reader 只碰得到 wren_ro ----------
REVOKE ALL ON SCHEMA public FROM wren_reader;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM wren_reader;
GRANT USAGE ON SCHEMA wren_ro TO wren_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA wren_ro TO wren_reader;

-- ---------- 5. 自我檢查 ----------
SELECT 'wren_ro view 數' AS check, count(*)::text AS result
  FROM information_schema.views WHERE table_schema = 'wren_ro'
UNION ALL
SELECT 'wren_reader 可讀 public.patient_info',
       has_table_privilege('wren_reader', 'public.patient_info', 'SELECT')::text
UNION ALL
SELECT 'wren_reader 可讀 wren_secret.salt',
       has_table_privilege('wren_reader', 'wren_secret.salt', 'SELECT')::text;
-- 預期：3 / false / false
