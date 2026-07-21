-- Azure Cost Management actuals. Current ingestion is service/RG grain, so
-- model and endpoint attribution are explicitly marked unallocated.
WITH current_scope AS (
  SELECT subscription_id, scope_filter
  FROM __AZURE_COST_TABLE__
  WHERE workspace_id = :workspace_id
    AND environment = :environment
    AND COALESCE(scope_filter, '') <> ''
  ORDER BY ingested_at DESC
  LIMIT 1
)
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
FROM __AZURE_COST_TABLE__ a
INNER JOIN current_scope s
  ON a.subscription_id = s.subscription_id
  AND a.scope_filter = s.scope_filter
WHERE a.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
  AND a.workspace_id = :workspace_id
  AND a.environment = :environment
  AND a.service_bucket = 'foundry_ai'
GROUP BY a.usage_date, a.workspace_id, a.service_name, a.resource_group, a.currency
ORDER BY usage_date, cost DESC
