-- Daily DBU and list-price cost by product, SKU and authoritative billing tags.
-- Sources: system.billing.usage x system.billing.list_prices
SELECT
  u.workspace_id,
  COALESCE(u.billing_origin_product, 'unallocated')                 AS workload_type,
  u.sku_name,
  COALESCE(NULLIF(TRIM(u.custom_tags['project']), ''),
           'unallocated')                                          AS project,
  COALESCE(
    NULLIF(TRIM(u.custom_tags['app']), ''),
    NULLIF(TRIM(u.custom_tags['application']), ''),
    'unallocated'
  )                                                                AS app,
  COALESCE(NULLIF(TRIM(u.custom_tags['team']), ''),
           'unallocated')                                          AS team,
  COALESCE(NULLIF(TRIM(u.custom_tags['use_case']), ''),
           'unallocated')                                          AS use_case,
  SUM(u.usage_quantity)                                            AS dbus,
  ROUND(SUM(u.usage_quantity * COALESCE(
      p.pricing.effective_list.default, p.pricing.default)), 2)    AS list_cost_usd
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices p
  ON  u.sku_name = p.sku_name
  AND u.cloud    = p.cloud
  AND u.usage_start_time >= p.price_start_time
  AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
WHERE u.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
  AND u.workspace_id = :workspace_id
GROUP BY
  u.workspace_id,
  workload_type,
  u.sku_name,
  project,
  app,
  team,
  use_case
ORDER BY list_cost_usd DESC
