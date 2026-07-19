-- @param lookbackDays INT
-- Successful invocations only: fast failures such as HTTP 429 must not make
-- the endpoint latency distribution look healthier than it is.
WITH scoped_invocations AS (
  SELECT
    DATE_TRUNC('HOUR', event_time) AS bucket_start,
    endpoint_id,
    endpoint_name,
    COALESCE(destination_model, destination_name, 'unknown') AS destination_model,
    invocation_id,
    status_code,
    latency_ms,
    time_to_first_byte_ms
  FROM system.ai_gateway.usage
  WHERE workspace_id = :workspaceId
    AND event_time >= DATE_SUB(CURRENT_DATE(), :lookbackDays)
)
SELECT
  bucket_start,
  endpoint_id,
  endpoint_name,
  destination_model,
  COUNT(DISTINCT invocation_id) AS invocation_count,
  COUNT(DISTINCT CASE
    WHEN status_code BETWEEN 200 AND 399 THEN invocation_id
  END) AS successful_invocation_count,
  PERCENTILE_APPROX(
    CASE WHEN status_code BETWEEN 200 AND 399 THEN latency_ms END,
    0.50,
    10000
  ) AS p50_latency_ms,
  PERCENTILE_APPROX(
    CASE WHEN status_code BETWEEN 200 AND 399 THEN latency_ms END,
    0.95,
    10000
  ) AS p95_latency_ms,
  PERCENTILE_APPROX(
    CASE WHEN status_code BETWEEN 200 AND 399 THEN latency_ms END,
    0.99,
    10000
  ) AS p99_latency_ms,
  PERCENTILE_APPROX(
    CASE WHEN status_code BETWEEN 200 AND 399 THEN time_to_first_byte_ms END,
    0.95,
    10000
  ) AS p95_time_to_first_byte_ms
FROM scoped_invocations
GROUP BY bucket_start, endpoint_id, endpoint_name, destination_model
ORDER BY bucket_start, endpoint_name, destination_model
