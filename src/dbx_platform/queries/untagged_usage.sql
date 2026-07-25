-- Share of spend with no custom tags, no team tag, or no project tag over the
-- last :days days. Live cluster/job checks are done API-side by
-- `governance tag-compliance`.
-- Sources: system.billing.usage x system.billing.list_prices
SELECT
  workspace_id,
  ROUND(SUM(list_cost_usd), 2)                                                AS total_list_cost_usd,
  ROUND(SUM(CASE WHEN untagged THEN list_cost_usd ELSE 0 END), 2)             AS untagged_list_cost_usd,
  ROUND(100 * SUM(CASE WHEN untagged THEN list_cost_usd ELSE 0 END)
            / NULLIF(SUM(list_cost_usd), 0), 1)                               AS untagged_pct,
  ROUND(SUM(CASE WHEN missing_team THEN list_cost_usd ELSE 0 END), 2)          AS missing_team_list_cost_usd,
  ROUND(100 * SUM(CASE WHEN missing_team THEN list_cost_usd ELSE 0 END)
            / NULLIF(SUM(list_cost_usd), 0), 1)                               AS missing_team_pct,
  ROUND(SUM(CASE WHEN missing_project THEN list_cost_usd ELSE 0 END), 2)       AS missing_project_list_cost_usd,
  ROUND(100 * SUM(CASE WHEN missing_project THEN list_cost_usd ELSE 0 END)
            / NULLIF(SUM(list_cost_usd), 0), 1)                               AS missing_project_pct
FROM (
  SELECT
    u.workspace_id,
    (u.custom_tags IS NULL OR cardinality(map_keys(u.custom_tags)) = 0)       AS untagged,
    (NULLIF(u.custom_tags['team'], '') IS NULL)                               AS missing_team,
    (NULLIF(u.custom_tags['project'], '') IS NULL)                            AS missing_project,
    u.usage_quantity * COALESCE(
        p.pricing.effective_list.default, p.pricing.default)                  AS list_cost_usd
  FROM system.billing.usage u
  LEFT JOIN system.billing.list_prices p
    ON  u.sku_name = p.sku_name
    AND u.cloud    = p.cloud
    AND u.usage_start_time >= p.price_start_time
    AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
  WHERE u.usage_date >= DATE_SUB(CURRENT_DATE(), :days)
)
GROUP BY workspace_id
ORDER BY untagged_list_cost_usd DESC
