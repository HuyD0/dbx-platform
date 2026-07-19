-- Daily DBU and list-price cost by team, project, SKU and workspace.
-- Sources: system.billing.usage x system.billing.list_prices
SELECT
  u.workspace_id,
  COALESCE(NULLIF(u.custom_tags['team'], ''), 'unallocated')       AS team,
  COALESCE(NULLIF(u.custom_tags['project'], ''), 'unallocated')    AS project,
  u.sku_name,
  SUM(u.usage_quantity)                                            AS dbus,
  ROUND(SUM(u.usage_quantity * COALESCE(
      p.pricing.effective_list.default, p.pricing.default)), 2)    AS list_cost_usd
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices p
  ON  u.sku_name = p.sku_name
  AND u.cloud    = p.cloud
  AND u.usage_start_time >= p.price_start_time
  AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
WHERE u.workspace_id = :workspace_id
  AND u.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
GROUP BY
  u.workspace_id,
  COALESCE(NULLIF(u.custom_tags['team'], ''), 'unallocated'),
  COALESCE(NULLIF(u.custom_tags['project'], ''), 'unallocated'),
  u.sku_name
ORDER BY list_cost_usd DESC
