-- Daily SKU-family bridge between Databricks published list cost and the
-- resource-scoped Azure Databricks billed-cost ledger. This deliberately does
-- not call family matches "invoice lines", and it calculates variance only
-- when both sides use USD.
WITH current_scope AS (
  SELECT subscription_id, scope_filter
  FROM __AZURE_COST_TABLE__
  WHERE workspace_id = :workspace_id
    AND environment = :environment
    AND COALESCE(scope_filter, '') <> ''
  ORDER BY ingested_at DESC
  LIMIT 1
),
databricks_list AS (
  SELECT
    u.usage_date,
    CASE
      WHEN UPPER(u.sku_name) LIKE '%SQL%' THEN 'SQL'
      WHEN UPPER(u.sku_name) LIKE '%ANTHROPIC%'
        OR UPPER(u.sku_name) LIKE '%INFERENCE%'
        OR UPPER(u.sku_name) LIKE '%SERVING%' THEN 'MODEL_SERVING'
      WHEN UPPER(u.sku_name) LIKE '%JOBS%'
        OR UPPER(u.sku_name) LIKE '%AUTOMATED%' THEN 'JOBS'
      WHEN UPPER(u.sku_name) LIKE '%ALL_PURPOSE%'
        OR UPPER(u.sku_name) LIKE '%INTERACTIVE%' THEN 'INTERACTIVE'
      WHEN UPPER(u.sku_name) LIKE '%STORAGE%'
        OR UPPER(u.sku_name) LIKE '%DSU%' THEN 'STORAGE'
      ELSE 'OTHER'
    END AS sku_family,
    ROUND(SUM(u.usage_quantity * COALESCE(
      p.pricing.effective_list.default,
      p.pricing.default
    )), 8) AS databricks_list_usd,
    SORT_ARRAY(COLLECT_SET(u.sku_name)) AS databricks_skus
  FROM system.billing.usage u
  LEFT JOIN system.billing.list_prices p
    ON u.sku_name = p.sku_name
    AND u.cloud = p.cloud
    AND u.usage_start_time >= p.price_start_time
    AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
  WHERE u.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
    AND u.workspace_id = :workspace_id
    AND UPPER(u.cloud) = 'AZURE'
  GROUP BY u.usage_date, sku_family
),
azure_billed AS (
  SELECT
    usage_date,
    CASE
      WHEN UPPER(meter_name) LIKE '%SQL%' THEN 'SQL'
      WHEN UPPER(meter_name) LIKE '%ANTHROPIC%'
        OR UPPER(meter_name) LIKE '%INFERENCE%'
        OR UPPER(meter_name) LIKE '%SERVING%' THEN 'MODEL_SERVING'
      WHEN UPPER(meter_name) LIKE '%JOB%'
        OR UPPER(meter_name) LIKE '%AUTOMATED%' THEN 'JOBS'
      WHEN UPPER(meter_name) LIKE '%ALL PURPOSE%'
        OR UPPER(meter_name) LIKE '%INTERACTIVE%' THEN 'INTERACTIVE'
      WHEN UPPER(meter_name) LIKE '%STORAGE%'
        OR UPPER(meter_name) LIKE '%DSU%' THEN 'STORAGE'
      ELSE 'OTHER'
    END AS sku_family,
    currency AS azure_currency,
    ROUND(SUM(cost), 8) AS azure_billed_cost,
    SORT_ARRAY(COLLECT_SET(meter_name)) AS azure_meters
  FROM __AZURE_COST_DETAIL_TABLE__ a
  INNER JOIN current_scope s
    ON a.subscription_id = s.subscription_id
    AND a.scope_filter = s.scope_filter
  WHERE a.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
    AND a.workspace_id = :workspace_id
    AND a.environment = :environment
    AND a.service_bucket = 'databricks'
  GROUP BY a.usage_date, sku_family, a.currency
)
SELECT
  COALESCE(d.usage_date, a.usage_date) AS usage_date,
  COALESCE(d.sku_family, a.sku_family) AS sku_family,
  d.databricks_list_usd,
  a.azure_billed_cost,
  COALESCE(a.azure_currency, 'UNAVAILABLE') AS azure_currency,
  CASE
    WHEN d.usage_date IS NULL THEN 'AZURE_ONLY'
    WHEN a.usage_date IS NULL THEN 'DATABRICKS_LIST_ONLY'
    WHEN UPPER(a.azure_currency) <> 'USD' THEN 'CURRENCY_MISMATCH'
    ELSE 'COMPARABLE_FAMILY'
  END AS comparison_status,
  CASE
    WHEN UPPER(a.azure_currency) = 'USD'
      THEN ROUND(a.azure_billed_cost - d.databricks_list_usd, 8)
    ELSE NULL
  END AS variance,
  CASE
    WHEN UPPER(a.azure_currency) = 'USD' AND d.databricks_list_usd <> 0
      THEN ROUND(
        (a.azure_billed_cost - d.databricks_list_usd)
        / d.databricks_list_usd * 100,
        2
      )
    ELSE NULL
  END AS variance_pct,
  d.databricks_skus,
  a.azure_meters,
  'Daily SKU-family bridge; not invoice-line equivalence' AS alignment_level
FROM databricks_list d
FULL OUTER JOIN azure_billed a
  ON d.usage_date = a.usage_date
  AND d.sku_family = a.sku_family
ORDER BY usage_date DESC, sku_family, azure_currency
