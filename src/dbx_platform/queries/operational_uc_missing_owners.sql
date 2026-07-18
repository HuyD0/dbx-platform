-- Defensive consistency check. Unity Catalog declares TABLE_OWNER non-null, so
-- any returned row is a metadata anomaly rather than a policy heuristic.
SELECT
  table_catalog,
  table_schema,
  table_name,
  created_by AS creator_principal
FROM system.information_schema.tables
WHERE table_catalog = :catalog
  AND table_schema <> 'information_schema'
  AND (table_owner IS NULL OR TRIM(table_owner) = '')
ORDER BY table_schema, table_name
LIMIT :limit
