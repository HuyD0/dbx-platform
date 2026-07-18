-- Per-day, per-endpoint, per-app usage from Unity AI Gateway (Beta). Adds
-- latency, which the serving usage table lacks. The rollup feature-detects
-- this source and records unavailability in source health instead of failing.
-- 'app' comes from caller-supplied request tags.
SELECT
  DATE(event_time)                                               AS usage_date,
  COALESCE(endpoint_name, 'unknown')                             AS endpoint_name,
  COALESCE(request_tags['app'], request_tags['application'],
           request_tags['use_case'], request_tags['project'],
           'unallocated')                                        AS app,
  COALESCE(destination_model, destination_name, '')              AS entity_name,
  COALESCE(destination_type, '')                                 AS entity_type,
  COUNT(DISTINCT request_id)                                     AS requests,
  SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END)            AS errors,
  SUM(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END)            AS server_errors,
  SUM(COALESCE(input_tokens, 0))                                 AS input_tokens,
  SUM(COALESCE(output_tokens, 0))                                AS output_tokens,
  COUNT(DISTINCT requester)                                      AS distinct_requesters,
  PERCENTILE_APPROX(latency_ms, 0.95)                            AS p95_latency_ms,
  'system.ai_gateway.usage'                                      AS source
FROM system.ai_gateway.usage
WHERE DATE(event_time) >= DATE_SUB(CURRENT_DATE(), :days)
GROUP BY usage_date, endpoint_name, app, entity_name, entity_type
ORDER BY usage_date, requests DESC
