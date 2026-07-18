-- Resource/meter-grain Azure Cost Management actuals. Resource IDs provide
-- deployment attribution; meters provide model/SKU context when Azure
-- includes it in the bill.
SELECT
  usage_date                                                     AS usage_date,
  workspace_id                                                   AS workspace_id,
  'azure'                                                        AS provider,
  COALESCE(meter_name, resource_type, 'unallocated')              AS model,
  COALESCE(resource_id, 'unallocated')                            AS endpoint,
  'unallocated'                                                  AS principal,
  'unallocated'                                                  AS team,
  'unallocated'                                                  AS use_case,
  ROUND(SUM(cost), 8)                                            AS cost,
  currency                                                       AS currency
FROM __AZURE_COST_DETAIL_TABLE__
WHERE usage_date >= DATE_SUB(CURRENT_DATE(), :days)
  AND workspace_id = :workspace_id
  AND environment = :environment
  AND service_bucket = 'foundry_ai'
GROUP BY usage_date, workspace_id, resource_id, resource_type, meter_name, currency
ORDER BY usage_date, cost DESC
