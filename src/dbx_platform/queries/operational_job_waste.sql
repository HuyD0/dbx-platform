-- Failed attempt, repair/retry, and queue-time signals for standard Job runs.
-- Cost is intentionally unsupported here: this source does not contain billing.
WITH latest_jobs AS (
  SELECT workspace_id, job_id, name
  FROM system.lakeflow.jobs
  WHERE workspace_id = :workspace_id
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY workspace_id, job_id ORDER BY change_time DESC
  ) = 1
),
terminal_attempts AS (
  SELECT
    workspace_id,
    job_id,
    run_id,
    result_state,
    queue_duration_seconds,
    period_end_time
  FROM system.lakeflow.job_run_timeline
  WHERE workspace_id = :workspace_id
    AND run_type = 'JOB_RUN'
    AND result_state IS NOT NULL
    AND period_start_time >= DATE_SUB(CURRENT_DATE(), :recent_days)
),
-- Databricks emits result_state only on a terminal slice. A repaired run can
-- have multiple terminal rows with the same run_id; the documented Lakeflow
-- retry query counts those extra terminal rows per logical run.
per_run_retries AS (
  SELECT
    workspace_id,
    job_id,
    run_id,
    COUNT(*) - 1 AS retry_attempts
  FROM terminal_attempts
  GROUP BY workspace_id, job_id, run_id
),
job_retries AS (
  SELECT
    workspace_id,
    job_id,
    SUM(retry_attempts) AS retry_attempts
  FROM per_run_retries
  GROUP BY workspace_id, job_id
),
job_attempts AS (
  SELECT
    workspace_id,
    job_id,
    COUNT(*) AS attempts,
    COUNT_IF(result_state IN (
      'FAILED', 'ERROR', 'TIMED_OUT', 'BLOCKED', 'UPSTREAM_FAILED'
    )) AS failed_attempts,
    COUNT(queue_duration_seconds) AS queue_metric_attempts,
    PERCENTILE_APPROX(queue_duration_seconds, 0.95) AS p95_queue_seconds,
    SUM(COALESCE(queue_duration_seconds, 0)) AS total_queue_seconds,
    MAX(period_end_time) AS evidence_freshness_at
  FROM terminal_attempts
  GROUP BY workspace_id, job_id
)
SELECT
  a.workspace_id,
  a.job_id,
  MAX(j.name) AS job_name,
  MAX(a.attempts) AS attempts,
  MAX(a.failed_attempts) AS failed_attempts,
  MAX(r.retry_attempts) AS retry_attempts,
  MAX(a.queue_metric_attempts) AS queue_metric_attempts,
  MAX(a.p95_queue_seconds) AS p95_queue_seconds,
  MAX(a.total_queue_seconds) AS total_queue_seconds,
  MAX(a.evidence_freshness_at) AS evidence_freshness_at
FROM job_attempts a
JOIN job_retries r USING (workspace_id, job_id)
LEFT JOIN latest_jobs j USING (workspace_id, job_id)
GROUP BY a.workspace_id, a.job_id
ORDER BY
  failed_attempts DESC,
  retry_attempts DESC,
  total_queue_seconds DESC
LIMIT :limit
