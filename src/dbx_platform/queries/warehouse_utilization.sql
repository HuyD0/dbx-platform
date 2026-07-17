-- Per-warehouse spend vs query volume and queueing over the last :days days.
-- Right-sizing cuts both ways: spend with few queries suggests shrinking or a
-- shorter auto-stop; sustained queueing suggests the warehouse is undersized.
-- Sources: system.billing.usage x list_prices, system.query.history
WITH spend AS (
  SELECT
    u.workspace_id,
    u.usage_metadata.warehouse_id                                   AS warehouse_id,
    ROUND(SUM(u.usage_quantity * COALESCE(
        p.pricing.effective_list.default, p.pricing.default)), 2)   AS list_cost_usd
  FROM system.billing.usage u
  LEFT JOIN system.billing.list_prices p
    ON  u.sku_name = p.sku_name
    AND u.cloud    = p.cloud
    AND u.usage_start_time >= p.price_start_time
    AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
  WHERE u.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
    AND u.usage_metadata.warehouse_id IS NOT NULL
  GROUP BY u.workspace_id, u.usage_metadata.warehouse_id
),
queries AS (
  SELECT
    workspace_id,
    compute.warehouse_id                            AS warehouse_id,
    COUNT(*)                                        AS query_count,
    ROUND(AVG(COALESCE(
        waiting_at_capacity_duration_ms, 0)) / 1000.0, 2)  AS avg_queue_seconds
  FROM system.query.history
  WHERE start_time >= DATE_SUB(CURRENT_DATE(), :days)
    AND compute.warehouse_id IS NOT NULL
  GROUP BY workspace_id, compute.warehouse_id
)
SELECT
  COALESCE(s.warehouse_id, q.warehouse_id)          AS warehouse_id,
  COALESCE(s.list_cost_usd, 0)                      AS list_cost_usd,
  COALESCE(q.query_count, 0)                        AS query_count,
  COALESCE(q.avg_queue_seconds, 0)                  AS avg_queue_seconds
FROM spend s
FULL OUTER JOIN queries q
  ON s.workspace_id = q.workspace_id AND s.warehouse_id = q.warehouse_id
ORDER BY list_cost_usd DESC
