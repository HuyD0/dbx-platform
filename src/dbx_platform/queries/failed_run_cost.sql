-- List cost burned on failed/timed-out job runs over the last :days days.
-- Sources: system.lakeflow.job_run_timeline x system.billing x lakeflow.jobs
WITH failed_runs AS (
  SELECT DISTINCT workspace_id, job_id, run_id
  FROM system.lakeflow.job_run_timeline
  WHERE period_start_time >= DATE_SUB(CURRENT_DATE(), :days)
    AND result_state IN ('FAILED', 'ERROR', 'TIMED_OUT', 'UPSTREAM_FAILED')
)
SELECT
  f.workspace_id,
  f.job_id,
  MAX(j.name)                                                      AS job_name,
  COUNT(DISTINCT f.run_id)                                         AS failed_runs,
  ROUND(SUM(u.usage_quantity * COALESCE(
      p.pricing.effective_list.default, p.pricing.default)), 2)    AS wasted_list_cost_usd
FROM failed_runs f
JOIN system.billing.usage u
  ON  u.workspace_id             = f.workspace_id
  AND u.usage_metadata.job_id    = f.job_id
  AND u.usage_metadata.job_run_id = f.run_id
LEFT JOIN system.billing.list_prices p
  ON  u.sku_name = p.sku_name
  AND u.cloud    = p.cloud
  AND u.usage_start_time >= p.price_start_time
  AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
LEFT JOIN system.lakeflow.jobs j
  ON  f.job_id       = j.job_id
  AND f.workspace_id = j.workspace_id
WHERE u.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
GROUP BY f.workspace_id, f.job_id
ORDER BY wasted_list_cost_usd DESC
LIMIT :limit
