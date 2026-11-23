SELECT
  r.id,
  r.patient_id                                   AS exam_id,
  r.model,
  r.inference_status,
  CASE
    WHEN r.inference_status IN ('success', 'error', 'invalid') THEN '測試期'
    WHEN r.inference_status IN ('4.2', '5')                     THEN '成功'
    WHEN r.inference_status = 'NFI'                             THEN '不適用'
    WHEN r.inference_status LIKE '%E' OR r.inference_status = 'error' THEN '失敗'
    ELSE '處理中'
  END                                            AS status_category,
  (r.inference_status IN ('success', 'error', 'invalid'))     AS is_test_era,
  (r.model LIKE 'IT\_%' OR UPPER(r.model) LIKE 'TEST%')       AS is_integration_test,
  p.locno                                        AS site,
  p.filetype,
  p.procedure_code,
  p.study_time,
  p.platform_time,
  r.data_storage_time,
  r.image_storage_time,
  CAST(p.study_time AS DATE)                     AS study_date,
  TO_CHAR(p.study_time, 'YYYY-MM')               AS study_month,
  date_part('epoch', r.data_storage_time - p.platform_time)  AS infer_seconds,
  date_part('epoch', r.image_storage_time - r.data_storage_time) AS image_upload_seconds,
  date_part('epoch', r.image_storage_time - p.study_time)    AS end_to_end_seconds,
  r.info_log
FROM public.inference_record r
LEFT JOIN public.patient_info p ON p.id = r.patient_id
