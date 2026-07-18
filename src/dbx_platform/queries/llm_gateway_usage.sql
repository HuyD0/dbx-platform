-- Unity AI Gateway request observability (Beta). The API router feature
-- detects failures and falls back to system.serving.endpoint_usage.
SELECT
  DATE(event_time)                                               AS usage_date,
  DATE_TRUNC('HOUR', event_time)                                 AS usage_hour,
  workspace_id                                                   AS workspace_id,
  CASE
    WHEN UPPER(destination_model) LIKE '%CLAUDE%' THEN 'anthropic'
    WHEN UPPER(destination_model) LIKE '%GPT%' THEN 'openai'
    ELSE COALESCE(destination_type, 'databricks')
  END                                                            AS provider,
  COALESCE(destination_model, destination_name, 'unallocated')    AS model,
  COALESCE(endpoint_name, 'unallocated')                          AS endpoint,
  COALESCE(requester, 'unallocated')                              AS principal,
  COALESCE(
    request_tags['team'],
    endpoint_tags['team'],
    'unallocated'
  )                                                              AS team,
  COALESCE(
    request_tags['use_case'],
    request_tags['project'],
    endpoint_tags['project'],
    'unallocated'
  )                                                              AS use_case,
  COUNT(DISTINCT request_id)                                      AS requests,
  COUNT(DISTINCT CASE WHEN status_code < 400 THEN request_id END) AS successful_requests,
  COUNT(*)                                                        AS invocations,
  SUM(COALESCE(input_tokens, 0))                                  AS input_tokens,
  SUM(COALESCE(output_tokens, 0))                                 AS output_tokens,
  SUM(COALESCE(token_details.cache_read_input_tokens, 0))         AS cached_tokens,
  SUM(COALESCE(token_details.output_reasoning_tokens, 0))         AS reasoning_tokens,
  SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END)             AS errors,
  SUM(GREATEST(COALESCE(SIZE(routing_information.attempts), 1) - 1, 0))
                                                                   AS retries,
  PERCENTILE_APPROX(latency_ms, 0.95)                             AS p95_latency_ms
FROM system.ai_gateway.usage
WHERE DATE(event_time) >= DATE_SUB(CURRENT_DATE(), :days)
GROUP BY
  usage_date,
  usage_hour,
  workspace_id,
  provider,
  model,
  endpoint,
  principal,
  team,
  use_case
ORDER BY usage_date, input_tokens + output_tokens DESC
