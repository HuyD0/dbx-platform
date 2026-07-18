-- Narrow v1 security signal: powerful data-changing grants to the built-in
-- account-wide principal, scoped to one configured catalog.
SELECT
  p.table_catalog,
  p.table_schema,
  p.table_name,
  p.grantee,
  p.privilege_type,
  p.inherited_from,
  t.table_owner
FROM system.information_schema.table_privileges p
LEFT JOIN system.information_schema.tables t
  ON p.table_catalog = t.table_catalog
  AND p.table_schema = t.table_schema
  AND p.table_name = t.table_name
WHERE p.table_catalog = :catalog
  AND p.table_schema <> 'information_schema'
  AND p.grantee = 'account users'
  AND p.privilege_type IN ('ALL PRIVILEGES', 'MANAGE', 'MODIFY')
ORDER BY p.table_schema, p.table_name, p.privilege_type
LIMIT :limit
