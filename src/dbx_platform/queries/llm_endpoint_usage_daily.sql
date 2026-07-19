-- Compatibility request/token usage query for workspaces without Unity AI
-- Gateway usage tracking.
SELECT
  DATE(eu.request_time)                                          AS usage_date,
  DATE_TRUNC('HOUR', eu.request_time)                            AS usage_hour,
  eu.workspace_id                                                AS workspace_id,
  'databricks-serving'                                          AS provider,
  COALESCE(se.entity_name, eu.served_entity_id, 'unallocated')   AS model,
  COALESCE(se.endpoint_name, eu.served_entity_id, 'unallocated') AS endpoint,
  COALESCE(eu.requester, 'unallocated')                          AS principal,
  'unallocated'                                                  AS team,
  'unallocated'                                                  AS use_case,
  COUNT(*)                                                       AS requests,
  CAST(NULL AS BIGINT)                                           AS successful_requests,
  COUNT(*)                                                       AS invocations,
  SUM(COALESCE(eu.input_token_count, 0))                         AS input_tokens,
  SUM(COALESCE(eu.output_token_count, 0))                        AS output_tokens,
  CAST(NULL AS BIGINT)                                           AS cached_tokens,
  CAST(NULL AS BIGINT)                                           AS reasoning_tokens,
  CAST(NULL AS BIGINT)                                           AS errors,
  CAST(NULL AS BIGINT)                                           AS retries,
  CAST(NULL AS DOUBLE)                                           AS p95_latency_ms
FROM system.serving.endpoint_usage eu
LEFT JOIN system.serving.served_entities se
  ON eu.served_entity_id = se.served_entity_id
  AND eu.workspace_id = se.workspace_id
WHERE DATE(eu.request_time) >= DATE_SUB(CURRENT_DATE(), :days)
  AND eu.workspace_id = :workspace_id
GROUP BY
  usage_date,
  usage_hour,
  eu.workspace_id,
  model,
  endpoint,
  principal
ORDER BY usage_date, input_tokens + output_tokens DESC
