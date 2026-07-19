-- Most expensive jobs over the last :days days, by list-price cost.
-- Sources: system.billing.usage x system.billing.list_prices x system.lakeflow.jobs
SELECT
  u.workspace_id,
  u.usage_metadata.job_id                                          AS job_id,
  MAX(j.name)                                                      AS job_name,
  SUM(u.usage_quantity)                                            AS dbus,
  ROUND(SUM(u.usage_quantity * COALESCE(
      p.pricing.effective_list.default, p.pricing.default)), 2)    AS list_cost_usd
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices p
  ON  u.sku_name = p.sku_name
  AND u.cloud    = p.cloud
  AND u.usage_start_time >= p.price_start_time
  AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
LEFT JOIN system.lakeflow.jobs j
  ON  u.usage_metadata.job_id = j.job_id
  AND u.workspace_id          = j.workspace_id
WHERE u.usage_metadata.job_id IS NOT NULL
  AND u.workspace_id = :workspace_id
  AND u.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
GROUP BY u.workspace_id, u.usage_metadata.job_id
ORDER BY list_cost_usd DESC
LIMIT :limit
