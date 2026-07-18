-- Unity AI Gateway-enriched model-serving list cost. Callers fall back to the
-- compatibility query when the ai_gateway struct fields are unavailable.
SELECT
  u.usage_date                                                   AS usage_date,
  u.workspace_id                                                 AS workspace_id,
  CASE
    WHEN UPPER(COALESCE(
      u.usage_metadata.ai_gateway.destination_model,
      u.sku_name
    )) LIKE '%CLAUDE%'
      OR UPPER(u.sku_name) LIKE '%ANTHROPIC%' THEN 'anthropic'
    WHEN UPPER(COALESCE(
      u.usage_metadata.ai_gateway.destination_model,
      u.sku_name
    )) LIKE '%GPT%'
      OR UPPER(u.sku_name) LIKE '%OPENAI%' THEN 'openai'
    ELSE 'databricks'
  END                                                            AS provider,
  COALESCE(
    u.usage_metadata.ai_gateway.destination_model,
    u.sku_name
  )                                                              AS model,
  COALESCE(
    u.usage_metadata.ai_gateway.endpoint_name,
    u.usage_metadata.endpoint_name,
    'unallocated'
  )                                                              AS endpoint,
  COALESCE(
    u.identity_metadata.run_by,
    u.identity_metadata.run_as,
    'unallocated'
  )                                                              AS principal,
  COALESCE(u.custom_tags['team'], 'unallocated')                  AS team,
  COALESCE(
    u.custom_tags['use_case'],
    u.custom_tags['project'],
    'unallocated'
  )                                                              AS use_case,
  ROUND(SUM(u.usage_quantity * COALESCE(
    p.pricing.effective_list.default,
    p.pricing.default
  )), 8)                                                         AS cost,
  'USD'                                                          AS currency
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices p
  ON u.sku_name = p.sku_name
  AND u.cloud = p.cloud
  AND u.usage_start_time >= p.price_start_time
  AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
WHERE u.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
  AND (
    u.billing_origin_product = 'MODEL_SERVING'
    OR u.sku_name LIKE '%INFERENCE%'
    OR u.sku_name LIKE '%SERVING%'
  )
GROUP BY
  u.usage_date,
  u.workspace_id,
  provider,
  model,
  endpoint,
  principal,
  team,
  use_case
ORDER BY usage_date, cost DESC
