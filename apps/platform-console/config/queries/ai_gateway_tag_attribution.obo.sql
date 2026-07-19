-- @param lookbackDays INT
-- request_tags is already a SQL map. Exploding it produces one attribution
-- row per tag key/value, so totals must not be summed across different keys.
WITH scoped_invocations AS (
  SELECT
    DATE_TRUNC('HOUR', event_time) AS bucket_start,
    endpoint_id,
    endpoint_name,
    request_id,
    invocation_id,
    request_tags,
    input_tokens,
    output_tokens,
    status_code
  FROM system.ai_gateway.usage
  WHERE workspace_id = :workspaceId
    AND event_time >= DATE_SUB(CURRENT_DATE(), :lookbackDays)
)
SELECT
  bucket_start,
  endpoint_id,
  endpoint_name,
  COALESCE(tag_key, 'unattributed') AS tag_key,
  COALESCE(tag_value, 'unattributed') AS tag_value,
  COUNT(DISTINCT request_id) AS logical_request_count,
  COUNT(DISTINCT invocation_id) AS invocation_count,
  SUM(COALESCE(input_tokens, 0)) AS input_tokens,
  SUM(COALESCE(output_tokens, 0)) AS output_tokens,
  SUM(CASE WHEN status_code = 429 THEN 1 ELSE 0 END) AS throttled_invocations,
  SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS failed_invocations
FROM scoped_invocations
LATERAL VIEW OUTER EXPLODE(request_tags) request_tag AS tag_key, tag_value
GROUP BY
  bucket_start,
  endpoint_id,
  endpoint_name,
  COALESCE(tag_key, 'unattributed'),
  COALESCE(tag_value, 'unattributed')
ORDER BY bucket_start, endpoint_name, tag_key, tag_value
