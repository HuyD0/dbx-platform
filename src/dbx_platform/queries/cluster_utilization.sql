-- Per-cluster CPU/memory utilization and observed worker counts over the
-- last :days days, with current cluster sizing for right-sizing decisions.
-- Sources: system.compute.node_timeline x system.compute.clusters, billing
WITH worker_minutes AS (
  SELECT
    workspace_id,
    cluster_id,
    start_time,
    COUNT(DISTINCT instance_id)                    AS workers,
    AVG(cpu_user_percent + cpu_system_percent)     AS cpu_pct,
    AVG(mem_used_percent)                          AS mem_pct
  FROM system.compute.node_timeline
  WHERE start_time >= DATE_SUB(CURRENT_DATE(), :days)
    AND NOT COALESCE(driver, false)
  GROUP BY workspace_id, cluster_id, start_time
),
util AS (
  SELECT
    workspace_id,
    cluster_id,
    ROUND(AVG(cpu_pct), 1)                         AS avg_cpu_pct,
    ROUND(PERCENTILE(cpu_pct, 0.95), 1)            AS p95_cpu_pct,
    ROUND(AVG(mem_pct), 1)                         AS avg_mem_pct,
    MAX(workers)                                   AS max_observed_workers
  FROM worker_minutes
  GROUP BY workspace_id, cluster_id
),
latest_cluster AS (
  SELECT *
  FROM system.compute.clusters
  QUALIFY ROW_NUMBER() OVER (
    PARTITION BY workspace_id, cluster_id ORDER BY change_time DESC
  ) = 1
),
spend AS (
  SELECT
    u.workspace_id,
    u.usage_metadata.cluster_id                                     AS cluster_id,
    ROUND(SUM(u.usage_quantity * COALESCE(
        p.pricing.effective_list.default, p.pricing.default)), 2)   AS list_cost_usd
  FROM system.billing.usage u
  LEFT JOIN system.billing.list_prices p
    ON  u.sku_name = p.sku_name
    AND u.cloud    = p.cloud
    AND u.usage_start_time >= p.price_start_time
    AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
  WHERE u.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
    AND u.usage_metadata.cluster_id IS NOT NULL
  GROUP BY u.workspace_id, u.usage_metadata.cluster_id
)
SELECT
  ut.cluster_id,
  c.cluster_name,
  c.owned_by                                       AS creator,
  ut.avg_cpu_pct,
  ut.p95_cpu_pct,
  ut.avg_mem_pct,
  ut.max_observed_workers,
  c.worker_count,
  c.min_autoscale_workers,
  c.max_autoscale_workers,
  c.worker_node_type,
  COALESCE(s.list_cost_usd, 0)                     AS list_cost_usd
FROM util ut
LEFT JOIN latest_cluster c
  ON ut.workspace_id = c.workspace_id AND ut.cluster_id = c.cluster_id
LEFT JOIN spend s
  ON ut.workspace_id = s.workspace_id AND ut.cluster_id = s.cluster_id
ORDER BY list_cost_usd DESC
