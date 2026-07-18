-- Azure Cost Management actuals. Current ingestion is service/RG grain, so
-- model and endpoint attribution are explicitly marked unallocated.
SELECT
  usage_date                                                     AS usage_date,
  workspace_id                                                   AS workspace_id,
  'azure'                                                        AS provider,
  COALESCE(service_name, 'unallocated')                           AS model,
  COALESCE(resource_group, 'unallocated')                         AS endpoint,
  'unallocated'                                                  AS principal,
  'unallocated'                                                  AS team,
  'unallocated'                                                  AS use_case,
  ROUND(SUM(cost), 8)                                            AS cost,
  currency                                                       AS currency
FROM __AZURE_COST_TABLE__
WHERE usage_date >= DATE_SUB(CURRENT_DATE(), :days)
  AND workspace_id = :workspace_id
  AND environment = :environment
  AND service_bucket = 'foundry_ai'
GROUP BY usage_date, workspace_id, service_name, resource_group, currency
ORDER BY usage_date, cost DESC
