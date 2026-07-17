-- GPU vs total classic-compute list cost over the last :days days.
-- Sources: system.billing.usage x list_prices, system.compute.clusters,
-- system.compute.node_types
WITH gpu_clusters AS (
  SELECT DISTINCT c.workspace_id, c.cluster_id
  FROM system.compute.clusters c
  JOIN system.compute.node_types nt
    ON c.worker_node_type = nt.node_type
  WHERE nt.gpu_count > 0
),
costed AS (
  SELECT
    u.workspace_id,
    u.usage_metadata.cluster_id                                     AS cluster_id,
    u.usage_quantity * COALESCE(
        p.pricing.effective_list.default, p.pricing.default)        AS cost
  FROM system.billing.usage u
  LEFT JOIN system.billing.list_prices p
    ON  u.sku_name = p.sku_name
    AND u.cloud    = p.cloud
    AND u.usage_start_time >= p.price_start_time
    AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
  WHERE u.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
    AND u.usage_metadata.cluster_id IS NOT NULL
)
SELECT
  co.workspace_id,
  ROUND(SUM(CASE WHEN g.cluster_id IS NOT NULL THEN co.cost END), 2)  AS gpu_cost_usd,
  ROUND(SUM(co.cost), 2)                                              AS total_cluster_cost_usd,
  ROUND(100 * SUM(CASE WHEN g.cluster_id IS NOT NULL THEN co.cost END)
        / NULLIF(SUM(co.cost), 0), 1)                                 AS gpu_share_pct
FROM costed co
LEFT JOIN gpu_clusters g
  ON co.workspace_id = g.workspace_id AND co.cluster_id = g.cluster_id
GROUP BY co.workspace_id
ORDER BY gpu_cost_usd DESC
