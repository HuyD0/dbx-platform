-- @param catalogName STRING
-- @param schemaName STRING
-- @param lookbackDays INT
-- @param resultLimit INT
-- One latest incident row per table. Nested check statuses are the
-- authoritative ML anomaly signals; derived gaps provide UI context only.
WITH latest_results AS (
  SELECT
    event_time,
    catalog_name,
    schema_name,
    table_name,
    table_id,
    status AS overall_status,
    freshness.status AS freshness_status,
    freshness.commit_freshness.status AS commit_freshness_status,
    freshness.commit_freshness.error_code AS freshness_error_code,
    freshness.commit_freshness.last_value AS last_commit_at,
    freshness.commit_freshness.predicted_value AS expected_commit_by,
    completeness.status AS completeness_status,
    completeness.daily_row_count.status AS daily_row_count_status,
    completeness.daily_row_count.error_code AS completeness_error_code,
    completeness.daily_row_count.last_value AS observed_rows_24h,
    completeness.daily_row_count.min_predicted_value AS expected_rows_min_24h,
    completeness.daily_row_count.max_predicted_value AS expected_rows_max_24h,
    downstream_impact.impact_level AS downstream_impact_level,
    downstream_impact.num_downstream_tables AS downstream_table_count,
    downstream_impact.num_queries_on_affected_tables AS impacted_query_count,
    TO_JSON(root_cause_analysis.upstream_jobs) AS upstream_jobs_json,
    ROW_NUMBER() OVER (
      PARTITION BY table_id
      ORDER BY event_time DESC
    ) AS result_rank
  FROM system.data_quality_monitoring.table_results
  WHERE catalog_name = :catalogName
    AND schema_name = :schemaName
    AND event_time >= DATE_SUB(CURRENT_DATE(), :lookbackDays)
)
SELECT
  event_time,
  catalog_name,
  schema_name,
  table_name,
  table_id,
  overall_status,
  freshness_status,
  commit_freshness_status,
  commit_freshness_status = 'Unhealthy' AS is_freshness_alert,
  freshness_error_code,
  last_commit_at,
  expected_commit_by,
  CASE
    WHEN expected_commit_by IS NOT NULL AND event_time > expected_commit_by
      THEN TIMESTAMPDIFF(MINUTE, expected_commit_by, event_time)
    ELSE 0
  END AS minutes_past_expected_at_scan,
  completeness_status,
  daily_row_count_status,
  daily_row_count_status = 'Unhealthy' AS is_completeness_alert,
  completeness_error_code,
  observed_rows_24h,
  expected_rows_min_24h,
  expected_rows_max_24h,
  CASE
    WHEN observed_rows_24h IS NOT NULL
      AND expected_rows_min_24h IS NOT NULL
      AND observed_rows_24h < expected_rows_min_24h
      THEN expected_rows_min_24h - observed_rows_24h
    ELSE 0
  END AS rows_below_expected_minimum,
  downstream_impact_level,
  downstream_table_count,
  impacted_query_count,
  upstream_jobs_json
FROM latest_results
WHERE result_rank = 1
  AND overall_status = 'Unhealthy'
ORDER BY downstream_impact_level DESC NULLS LAST, event_time DESC, table_name
LIMIT :resultLimit
