-- Estimated external-provider spend from Unity AI Gateway (Beta). This is
-- deliberately labeled PROVIDER_ESTIMATE by the Python layer.
SELECT
  usage_date                                                     AS usage_date,
  workspace_id                                                   AS workspace_id,
  COALESCE(usage_metadata.provider, 'external')                   AS provider,
  COALESCE(usage_metadata.model, 'unallocated')                   AS model,
  COALESCE(usage_metadata.endpoint_name, 'unallocated')           AS endpoint,
  COALESCE(
    identity_metadata.run_by,
    identity_metadata.run_as,
    'unallocated'
  )                                                              AS principal,
  COALESCE(
    custom_tags.request_tags['team'],
    custom_tags.endpoint_tags['team'],
    'unallocated'
  )                                                              AS team,
  COALESCE(
    custom_tags.request_tags['use_case'],
    custom_tags.request_tags['project'],
    custom_tags.endpoint_tags['project'],
    'unallocated'
  )                                                              AS use_case,
  ROUND(SUM(usage_quantity), 8)                                  AS cost,
  'USD'                                                          AS currency
FROM system.ai_gateway.external_model_spend
WHERE usage_date >= DATE_SUB(CURRENT_DATE(), :days)
GROUP BY
  usage_date,
  workspace_id,
  provider,
  model,
  endpoint,
  principal,
  team,
  use_case
ORDER BY usage_date, cost DESC
