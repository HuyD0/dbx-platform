-- @param lookbackDays INT
-- status_code is the final invocation response. A fallback that recovered
-- from an upstream 429 can still contain a 429 in routing_information.attempts
-- and is intentionally not counted as a final throttling failure here.
SELECT
  DATE_TRUNC('HOUR', event_time) AS bucket_start,
  endpoint_id,
  endpoint_name,
  COUNT(DISTINCT invocation_id) AS invocation_count,
  COUNT(DISTINCT request_id) AS logical_request_count,
  COUNT(DISTINCT CASE
    WHEN status_code = 429 THEN invocation_id
  END) AS throttled_invocation_count,
  COUNT(DISTINCT CASE
    WHEN status_code = 429 THEN request_id
  END) AS throttled_logical_request_count,
  ROUND(
    100.0 * COUNT(DISTINCT CASE
      WHEN status_code = 429 THEN invocation_id
    END) / GREATEST(COUNT(DISTINCT invocation_id), 1),
    3
  ) AS throttled_invocation_rate_pct
FROM system.ai_gateway.usage
WHERE workspace_id = :workspaceId
  AND event_time >= DATE_SUB(CURRENT_DATE(), :lookbackDays)
GROUP BY bucket_start, endpoint_id, endpoint_name
ORDER BY bucket_start, endpoint_name
