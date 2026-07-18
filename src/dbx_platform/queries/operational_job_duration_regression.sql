-- p95 duration regression for successful standard Job runs.
-- Partial coverage: run_duration_seconds is populated only on newer records.
WITH latest_jobs AS (
  SELECT workspace_id, job_id, name
  FROM system.lakeflow.jobs
  WHERE workspace_id = :workspace_id
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY workspace_id, job_id ORDER BY change_time DESC
  ) = 1
),
completed AS (
  SELECT
    workspace_id,
    job_id,
    run_duration_seconds,
    period_end_time,
    period_start_time
  FROM system.lakeflow.job_run_timeline
  WHERE workspace_id = :workspace_id
    AND run_type = 'JOB_RUN'
    AND result_state = 'SUCCEEDED'
    AND run_duration_seconds IS NOT NULL
    AND period_start_time >= DATE_SUB(CURRENT_DATE(), :window_days)
)
SELECT
  c.workspace_id,
  c.job_id,
  MAX(j.name) AS job_name,
  COUNT_IF(
    c.period_start_time >= DATE_SUB(CURRENT_DATE(), :recent_days)
  ) AS recent_samples,
  COUNT_IF(
    c.period_start_time < DATE_SUB(CURRENT_DATE(), :recent_days)
  ) AS baseline_samples,
  PERCENTILE_APPROX(
    CASE
      WHEN c.period_start_time >= DATE_SUB(CURRENT_DATE(), :recent_days)
        THEN c.run_duration_seconds
    END,
    0.95
  ) AS recent_p95_duration_seconds,
  PERCENTILE_APPROX(
    CASE
      WHEN c.period_start_time < DATE_SUB(CURRENT_DATE(), :recent_days)
        THEN c.run_duration_seconds
    END,
    0.95
  ) AS baseline_p95_duration_seconds,
  MAX(c.period_end_time) AS evidence_freshness_at
FROM completed c
LEFT JOIN latest_jobs j USING (workspace_id, job_id)
GROUP BY c.workspace_id, c.job_id
HAVING recent_samples >= :min_samples
  AND baseline_samples >= :min_samples
ORDER BY recent_p95_duration_seconds DESC
LIMIT :limit
