-- Conservative scan/output triage signal; not a query-plan diagnosis.
-- Cached results and statements whose text cannot be fingerprinted are excluded.
WITH eligible AS (
  SELECT
    workspace_id,
    SHA2(REGEXP_REPLACE(TRIM(statement_text), '[[:space:]]+', ' '), 256)
      AS query_fingerprint,
    COALESCE(compute.warehouse_id, compute.cluster_id, compute.type, 'unknown')
      AS compute_id,
    read_bytes,
    produced_rows,
    end_time
  FROM system.query.history
  WHERE workspace_id = :workspace_id
    AND execution_status = 'FINISHED'
    AND statement_type = 'SELECT'
    AND statement_text IS NOT NULL
    AND TRIM(statement_text) <> ''
    AND COALESCE(from_result_cache, FALSE) = FALSE
    AND start_time >= DATE_SUB(CURRENT_DATE(), :recent_days)
    AND read_bytes IS NOT NULL
)
SELECT
  workspace_id,
  query_fingerprint,
  compute_id,
  COUNT(*) AS executions,
  SUM(read_bytes) AS total_read_bytes,
  SUM(COALESCE(produced_rows, 0)) AS total_output_rows,
  MAX(end_time) AS evidence_freshness_at
FROM eligible
GROUP BY workspace_id, query_fingerprint, compute_id
HAVING executions >= :min_samples
ORDER BY total_read_bytes DESC
LIMIT :limit
