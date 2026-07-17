-- AI/ML spend by product, SKU and serving endpoint over the last :days days.
-- Sources: system.billing.usage x system.billing.list_prices
SELECT
  u.billing_origin_product,
  u.sku_name,
  u.usage_metadata.endpoint_name                                   AS endpoint_name,
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
  AND (
    u.billing_origin_product IN (
      'MODEL_SERVING', 'VECTOR_SEARCH', 'ONLINE_TABLES',
      'AGENT_EVALUATION', 'FOUNDATION_MODEL_TRAINING'
    )
    OR u.sku_name LIKE '%INFERENCE%'
    OR u.sku_name LIKE '%SERVING%'
  )
GROUP BY u.billing_origin_product, u.sku_name, u.usage_metadata.endpoint_name
ORDER BY list_cost_usd DESC
