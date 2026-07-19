-- @param lookbackDays INT
-- Databricks does not expose a native token-speed column. This query derives
-- post-first-byte throughput and excludes calls where that interval cannot be
-- measured (including non-streaming responses whose first-byte and total
-- latency are equal).
WITH eligible_invocations AS (
  SELECT
    DATE_TRUNC('HOUR', event_time) AS bucket_start,
    endpoint_id,
    endpoint_name,
    COALESCE(destination_model, destination_name, 'unknown') AS destination_model,
    invocation_id,
    output_tokens,
    latency_ms - time_to_first_byte_ms AS generation_ms
  FROM system.ai_gateway.usage
  WHERE workspace_id = :workspaceId
    AND event_time >= DATE_SUB(CURRENT_DATE(), :lookbackDays)
    AND status_code BETWEEN 200 AND 399
    AND output_tokens > 0
    AND time_to_first_byte_ms IS NOT NULL
    AND latency_ms > time_to_first_byte_ms
)
SELECT
  bucket_start,
  endpoint_id,
  endpoint_name,
  destination_model,
  COUNT(DISTINCT invocation_id) AS measured_invocation_count,
  SUM(output_tokens) AS measured_output_tokens,
  ROUND(
    TRY_DIVIDE(SUM(output_tokens) * 1000.0, SUM(generation_ms)),
    2
  ) AS weighted_output_tokens_per_second,
  ROUND(
    PERCENTILE_APPROX(
      output_tokens * 1000.0 / generation_ms,
      0.50,
      10000
    ),
    2
  ) AS p50_invocation_output_tokens_per_second,
  ROUND(
    PERCENTILE_APPROX(
      output_tokens * 1000.0 / generation_ms,
      0.95,
      10000
    ),
    2
  ) AS p95_invocation_output_tokens_per_second
FROM eligible_invocations
GROUP BY bucket_start, endpoint_id, endpoint_name, destination_model
ORDER BY bucket_start, endpoint_name, destination_model
