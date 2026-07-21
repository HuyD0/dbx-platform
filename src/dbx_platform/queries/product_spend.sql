-- Workspace-scoped product cost with a like-for-like previous-period comparison.
-- Product/resource and team/project dimensions come from system.billing.usage;
-- list cost is computed from the price effective when each usage record started.
WITH attributed AS (
  SELECT
    CASE
      WHEN u.usage_date >= DATE_SUB(CURRENT_DATE(), :current_start_days) THEN 'current'
      ELSE 'previous'
    END AS period,
    COALESCE(NULLIF(u.billing_origin_product, ''), 'UNATTRIBUTED') AS product,
    COALESCE(NULLIF(u.custom_tags['team'], ''), 'unallocated') AS team,
    COALESCE(NULLIF(u.custom_tags['project'], ''), 'unallocated') AS project,
    CASE
      WHEN u.billing_origin_product = 'APPS' THEN 'app'
      WHEN u.billing_origin_product IN ('DATABASE', 'LAKEBASE') THEN 'database'
      WHEN u.usage_metadata.endpoint_id IS NOT NULL THEN 'endpoint'
      WHEN u.usage_metadata.job_id IS NOT NULL THEN 'job'
      WHEN u.usage_metadata.warehouse_id IS NOT NULL THEN 'warehouse'
      WHEN u.usage_metadata.dlt_pipeline_id IS NOT NULL THEN 'pipeline'
      WHEN u.usage_metadata.cluster_id IS NOT NULL THEN 'cluster'
      WHEN u.usage_metadata.notebook_id IS NOT NULL THEN 'notebook'
      WHEN u.usage_metadata.ai_runtime_workload_id IS NOT NULL THEN 'ai_runtime'
      ELSE 'unattributed'
    END AS resource_type,
    CASE
      WHEN u.billing_origin_product = 'APPS'
        THEN COALESCE(u.usage_metadata.app_id, u.usage_metadata.app_name)
      WHEN u.billing_origin_product IN ('DATABASE', 'LAKEBASE')
        THEN u.usage_metadata.database_instance_id
      WHEN u.usage_metadata.endpoint_id IS NOT NULL
        THEN u.usage_metadata.endpoint_id
      WHEN u.usage_metadata.job_id IS NOT NULL
        THEN u.usage_metadata.job_id
      WHEN u.usage_metadata.warehouse_id IS NOT NULL
        THEN u.usage_metadata.warehouse_id
      WHEN u.usage_metadata.dlt_pipeline_id IS NOT NULL
        THEN u.usage_metadata.dlt_pipeline_id
      WHEN u.usage_metadata.cluster_id IS NOT NULL
        THEN u.usage_metadata.cluster_id
      WHEN u.usage_metadata.notebook_id IS NOT NULL
        THEN u.usage_metadata.notebook_id
      WHEN u.usage_metadata.ai_runtime_workload_id IS NOT NULL
        THEN u.usage_metadata.ai_runtime_workload_id
      ELSE NULL
    END AS resource_id,
    CASE
      WHEN u.billing_origin_product = 'APPS'
        THEN COALESCE(u.usage_metadata.app_name, u.usage_metadata.app_id)
      WHEN u.billing_origin_product IN ('DATABASE', 'LAKEBASE')
        THEN u.usage_metadata.database_instance_id
      WHEN u.usage_metadata.endpoint_id IS NOT NULL
        THEN COALESCE(u.usage_metadata.endpoint_name, u.usage_metadata.endpoint_id)
      WHEN u.usage_metadata.job_id IS NOT NULL
        THEN COALESCE(u.usage_metadata.job_name, u.usage_metadata.job_id)
      WHEN u.usage_metadata.warehouse_id IS NOT NULL
        THEN u.usage_metadata.warehouse_id
      WHEN u.usage_metadata.dlt_pipeline_id IS NOT NULL
        THEN u.usage_metadata.dlt_pipeline_id
      WHEN u.usage_metadata.cluster_id IS NOT NULL
        THEN u.usage_metadata.cluster_id
      WHEN u.usage_metadata.notebook_id IS NOT NULL
        THEN COALESCE(u.usage_metadata.notebook_path, u.usage_metadata.notebook_id)
      WHEN u.usage_metadata.ai_runtime_workload_id IS NOT NULL
        THEN u.usage_metadata.ai_runtime_workload_id
      ELSE NULL
    END AS resource_name,
    u.sku_name,
    u.usage_type,
    u.usage_unit,
    u.usage_quantity,
    COALESCE(p.pricing.effective_list.default, p.pricing.default) AS list_price
  FROM system.billing.usage u
  LEFT JOIN system.billing.list_prices p
    ON u.sku_name = p.sku_name
    AND u.cloud = p.cloud
    AND u.usage_start_time >= p.price_start_time
    AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
  WHERE u.workspace_id = :workspace_id
    AND u.usage_date >= DATE_SUB(CURRENT_DATE(), :comparison_start_days)
)
SELECT
  period,
  product,
  team,
  project,
  resource_type,
  resource_id,
  resource_name,
  sku_name,
  usage_type,
  usage_unit,
  SUM(usage_quantity) AS usage_quantity,
  ROUND(SUM(usage_quantity * list_price), 4) AS list_cost_usd,
  SUM(CASE WHEN list_price IS NULL THEN ABS(usage_quantity) ELSE 0 END)
    AS unpriced_usage_quantity
FROM attributed
GROUP BY
  period,
  product,
  team,
  project,
  resource_type,
  resource_id,
  resource_name,
  sku_name,
  usage_type,
  usage_unit
ORDER BY period, list_cost_usd DESC
