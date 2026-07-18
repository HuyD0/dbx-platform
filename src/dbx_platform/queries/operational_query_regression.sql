-- Duration and capacity-queue regression by exact normalized query fingerprint.
-- Statement text is hashed inside SQL and is never selected or persisted.
WITH eligible AS (
  SELECT
    workspace_id,
    SHA2(REGEXP_REPLACE(TRIM(statement_text), '[[:space:]]+', ' '), 256)
      AS query_fingerprint,
    statement_type,
    COALESCE(compute.warehouse_id, compute.cluster_id, compute.type, 'unknown')
      AS compute_id,
    total_duration_ms,
    COALESCE(waiting_at_capacity_duration_ms, 0) AS queue_duration_ms,
    start_time,
    end_time
  FROM system.query.history
  WHERE workspace_id = :workspace_id
    AND execution_status = 'FINISHED'
    AND statement_text IS NOT NULL
    AND TRIM(statement_text) <> ''
    AND start_time >= DATE_SUB(CURRENT_DATE(), :window_days)
)
SELECT
  workspace_id,
  query_fingerprint,
  statement_type,
  compute_id,
  COUNT_IF(start_time >= DATE_SUB(CURRENT_DATE(), :recent_days))
    AS recent_samples,
  COUNT_IF(start_time < DATE_SUB(CURRENT_DATE(), :recent_days))
    AS baseline_samples,
  PERCENTILE_APPROX(
    CASE
      WHEN start_time >= DATE_SUB(CURRENT_DATE(), :recent_days)
        THEN total_duration_ms
    END,
    0.95
  ) AS recent_p95_duration_ms,
  PERCENTILE_APPROX(
    CASE
      WHEN start_time < DATE_SUB(CURRENT_DATE(), :recent_days)
        THEN total_duration_ms
    END,
    0.95
  ) AS baseline_p95_duration_ms,
  PERCENTILE_APPROX(
    CASE
      WHEN start_time >= DATE_SUB(CURRENT_DATE(), :recent_days)
        THEN queue_duration_ms
    END,
    0.95
  ) AS recent_p95_queue_ms,
  PERCENTILE_APPROX(
    CASE
      WHEN start_time < DATE_SUB(CURRENT_DATE(), :recent_days)
        THEN queue_duration_ms
    END,
    0.95
  ) AS baseline_p95_queue_ms,
  MAX(end_time) AS evidence_freshness_at
FROM eligible
GROUP BY workspace_id, query_fingerprint, statement_type, compute_id
HAVING recent_samples >= :min_samples
  AND baseline_samples >= :min_samples
ORDER BY recent_p95_duration_ms DESC
LIMIT :limit
