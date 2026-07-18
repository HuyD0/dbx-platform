-- Per-day, per-endpoint, per-app production AI usage.
-- system.serving.endpoint_usage (90d retention; rows exist only where
-- per-endpoint usage tracking is enabled) x system.serving.served_entities.
-- 'app' comes from the caller-supplied usage_context map.
SELECT
  DATE(eu.request_time)                                          AS usage_date,
  COALESCE(se.endpoint_name, eu.served_entity_id, 'unknown')     AS endpoint_name,
  COALESCE(eu.usage_context['app'], eu.usage_context['application'],
           eu.usage_context['use_case'], eu.usage_context['project'],
           'unallocated')                                        AS app,
  COALESCE(se.entity_name, '')                                   AS entity_name,
  COALESCE(se.entity_type, '')                                   AS entity_type,
  COUNT(*)                                                       AS requests,
  SUM(CASE WHEN TRY_CAST(eu.status_code AS INT) >= 400 THEN 1 ELSE 0 END)
                                                                 AS errors,
  SUM(CASE WHEN TRY_CAST(eu.status_code AS INT) >= 500 THEN 1 ELSE 0 END)
                                                                 AS server_errors,
  SUM(COALESCE(eu.input_token_count, 0))                         AS input_tokens,
  SUM(COALESCE(eu.output_token_count, 0))                        AS output_tokens,
  COUNT(DISTINCT eu.requester)                                   AS distinct_requesters,
  CAST(NULL AS DOUBLE)                                           AS p95_latency_ms,
  'system.serving.endpoint_usage'                                AS source
FROM system.serving.endpoint_usage eu
LEFT JOIN system.serving.served_entities se
  ON eu.served_entity_id = se.served_entity_id
WHERE DATE(eu.request_time) >= DATE_SUB(CURRENT_DATE(), :days)
GROUP BY usage_date, endpoint_name, app, entity_name, entity_type
ORDER BY usage_date, requests DESC
