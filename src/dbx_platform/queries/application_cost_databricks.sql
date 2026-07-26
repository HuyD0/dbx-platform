-- Daily application cost evidence from Databricks billing metadata and tags.
-- Identity resolution is intentionally performed in application_cost.py so
-- metadata, durable bindings, and every configured tag participate in the
-- same fail-closed conflict rule.
WITH attributed AS (
  SELECT
    u.usage_date,
    COALESCE(NULLIF(u.billing_origin_product, ''), 'UNATTRIBUTED') AS workload,
    u.sku_name AS service,
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
        THEN COALESCE(
          u.usage_metadata.database_instance_id,
          u.usage_metadata.branch_id,
          u.usage_metadata.project_id
        )
      WHEN u.usage_metadata.endpoint_id IS NOT NULL THEN u.usage_metadata.endpoint_id
      WHEN u.usage_metadata.job_id IS NOT NULL THEN u.usage_metadata.job_id
      WHEN u.usage_metadata.warehouse_id IS NOT NULL THEN u.usage_metadata.warehouse_id
      WHEN u.usage_metadata.dlt_pipeline_id IS NOT NULL
        THEN u.usage_metadata.dlt_pipeline_id
      WHEN u.usage_metadata.cluster_id IS NOT NULL THEN u.usage_metadata.cluster_id
      WHEN u.usage_metadata.notebook_id IS NOT NULL THEN u.usage_metadata.notebook_id
      WHEN u.usage_metadata.ai_runtime_workload_id IS NOT NULL
        THEN u.usage_metadata.ai_runtime_workload_id
      ELSE NULL
    END AS resource_id,
    CASE
      WHEN u.billing_origin_product IN ('DATABASE', 'LAKEBASE') THEN TO_JSON(
        NAMED_STRUCT(
          'project_id', u.usage_metadata.project_id,
          'branch_id', u.usage_metadata.branch_id,
          'database_instance_id', u.usage_metadata.database_instance_id
        )
      )
      ELSE NULL
    END AS resource_aliases_json,
    CASE
      WHEN u.billing_origin_product = 'APPS'
        THEN COALESCE(u.usage_metadata.app_name, u.usage_metadata.app_id)
      WHEN u.billing_origin_product IN ('DATABASE', 'LAKEBASE')
        THEN u.usage_metadata.database_instance_id
      WHEN u.usage_metadata.endpoint_id IS NOT NULL
        THEN COALESCE(u.usage_metadata.endpoint_name, u.usage_metadata.endpoint_id)
      WHEN u.usage_metadata.job_id IS NOT NULL
        THEN COALESCE(u.usage_metadata.job_name, u.usage_metadata.job_id)
      WHEN u.usage_metadata.warehouse_id IS NOT NULL THEN u.usage_metadata.warehouse_id
      WHEN u.usage_metadata.dlt_pipeline_id IS NOT NULL
        THEN u.usage_metadata.dlt_pipeline_id
      WHEN u.usage_metadata.cluster_id IS NOT NULL THEN u.usage_metadata.cluster_id
      WHEN u.usage_metadata.notebook_id IS NOT NULL
        THEN COALESCE(u.usage_metadata.notebook_path, u.usage_metadata.notebook_id)
      WHEN u.usage_metadata.ai_runtime_workload_id IS NOT NULL
        THEN u.usage_metadata.ai_runtime_workload_id
      ELSE NULL
    END AS resource_name,
    CASE
      WHEN u.billing_origin_product = 'APPS'
        THEN u.usage_metadata.app_name
      ELSE NULL
    END AS metadata_application,
    NULLIF(TRIM(u.custom_tags['application']), '') AS application_tag,
    NULLIF(TRIM(u.custom_tags['app']), '') AS app_tag,
    NULLIF(TRIM(u.custom_tags['project']), '') AS project_tag,
    TO_JSON(u.custom_tags) AS tags_json,
    u.usage_quantity,
    COALESCE(p.pricing.effective_list.default, p.pricing.default) AS list_price,
    u.usage_end_time
  FROM system.billing.usage u
  LEFT JOIN system.billing.list_prices p
    ON u.sku_name = p.sku_name
    AND u.cloud = p.cloud
    AND u.usage_start_time >= p.price_start_time
    AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
  WHERE u.workspace_id = :workspace_id
    AND u.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
)
SELECT
  usage_date,
  workload,
  service,
  resource_type,
  resource_id,
  resource_aliases_json,
  resource_name,
  metadata_application,
  application_tag,
  app_tag,
  project_tag,
  tags_json,
  ROUND(SUM(usage_quantity * list_price), 4) AS cost,
  SUM(CASE WHEN list_price IS NULL THEN ABS(usage_quantity) ELSE 0 END)
    AS unpriced_usage_quantity,
  MAX(usage_end_time) AS evidence_at
FROM attributed
GROUP BY
  usage_date,
  workload,
  service,
  resource_type,
  resource_id,
  resource_aliases_json,
  resource_name,
  metadata_application,
  application_tag,
  app_tag,
  project_tag,
  tags_json
