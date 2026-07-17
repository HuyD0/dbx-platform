"""AI/BI dashboard support: render templates and provision their dependencies.

The four dashboards under dashboards/templates/ are adapted from the community
suite github.com/mohanab89/databricks-dashboard-suite (system-table dashboards,
provided as-is, no license file — see README attribution). Their queries use
``{catalog}.{schema}`` placeholders and depend on helper objects the suite's
notebook normally creates:

- functions ``job_type_from_sku``, ``sql_type_from_sku``, ``team_name_from_tags``
- reference tables ``workspace_reference`` and ``warehouse_reference``

``dbx-platform dashboards setup`` provisions all of those through the
Statement Execution API (works from a laptop — no Spark required), and
``dbx-platform dashboards render`` writes the deployable .lvdash.json files
that the bundle's resources/dashboards.yml points at.
"""

from __future__ import annotations

from pathlib import Path

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import run_query

TEMPLATE_NAMES = (
    "unified_cost_analysis",
    "job_operations_cost",
    "dbsql_cost_performance",
    "lineage_catalog_utilization",
)


# --- render ------------------------------------------------------------------

def render_template(template_text: str, catalog: str, schema: str) -> str:
    """Substitute {catalog}/{schema} placeholders. Pure function."""
    return template_text.replace("{catalog}", catalog).replace("{schema}", schema)


def render_all(dashboards_dir: str | Path, catalog: str, schema: str) -> list[Path]:
    """Render dashboards/templates/*.lvdash.json -> dashboards/*.lvdash.json."""
    import json

    root = Path(dashboards_dir)
    templates = root / "templates"
    written = []
    for name in TEMPLATE_NAMES:
        src = templates / f"{name}.lvdash.json"
        dest = root / f"{name}.lvdash.json"
        rendered = render_template(src.read_text(), catalog, schema)
        data = json.loads(rendered)  # fail fast on a corrupted template
        if not data.get("datasets") or not data.get("pages"):
            raise ValueError(f"{src}: not a valid Lakeview dashboard (datasets/pages missing)")
        dest.write_text(rendered)
        written.append(dest)
    return written


# --- setup (schema, functions, reference tables) -------------------------------

def build_team_name_function_sql(catalog: str, schema: str, tag_keys: list[str]) -> str:
    """Port of the suite's dynamic team_name_from_tags builder. Pure function.

    Resolves a team name by checking the given tag keys on the cluster tags
    first, then the job tags; falls back to 'unknown'.
    """
    param_cols = ["cluster_tags", "job_tags"]
    case_list = []
    for col in param_cols:
        case_statement = "CASE\n"
        for key in tag_keys:
            k = key.strip()
            case_statement += f"  WHEN map_contains_key({col}, '{k}') THEN lower({col}.`{k}`)\n"
        case_statement += (
            f"  WHEN map_contains_key({col}, 'LakehouseMonitoring') "
            f"AND {col}.LakehouseMonitoring = 'true' THEN 'LakehouseMonitoring'\n"
        )
        case_statement += f"  ELSE NULL END AS {col}_team_name_init\n"
        case_list.append(case_statement)
    inner = (
        f"SELECT ifnull({param_cols[0]}_team_name_init, {param_cols[1]}_team_name_init) "
        f"AS team_name_init FROM\n (SELECT {', '.join(case_list)})"
    )
    query = f"(SELECT ifnull(team_name_init, 'unknown') AS team_name FROM\n ({inner}))"
    return (
        f"CREATE OR REPLACE FUNCTION {catalog}.{schema}.team_name_from_tags"
        f"(cluster_tags MAP<STRING,STRING>, job_tags MAP<STRING,STRING>)\n"
        f"RETURNS STRING RETURN {query}"
    )


def setup_statements(catalog: str, schema: str, tag_keys: list[str]) -> list[tuple[str, str]]:
    """All (description, sql) statements needed by the dashboards. Pure function."""
    fq = f"{catalog}.{schema}"
    return [
        (f"schema {fq}", f"CREATE SCHEMA IF NOT EXISTS {fq}"),
        (
            f"function {fq}.job_type_from_sku",
            f"""CREATE OR REPLACE FUNCTION {fq}.job_type_from_sku(sku STRING)
RETURNS STRING
RETURN CASE
  WHEN sku LIKE '%JOBS_SERVERLESS%' THEN 'JOBS_SERVERLESS'
  WHEN sku LIKE '%JOBS_COMPUTE_(PHOTON)%' THEN 'JOBS_COMPUTE_PHOTON'
  WHEN sku LIKE '%JOBS_COMPUTE%' THEN 'JOBS_COMPUTE'
  WHEN sku IS NULL THEN 'UNKNOWN'
  ELSE 'OTHER'
END""",
        ),
        (
            f"function {fq}.sql_type_from_sku",
            f"""CREATE OR REPLACE FUNCTION {fq}.sql_type_from_sku(sku STRING)
RETURNS STRING
RETURN CASE
  WHEN sku LIKE '%SERVERLESS_SQL%' THEN 'SQL_SERVERLESS'
  WHEN sku LIKE '%SQL_PRO%' THEN 'SQL_PRO'
  WHEN sku LIKE '%SQL%' THEN 'SQL_CLASSIC'
  WHEN sku IS NULL THEN 'UNKNOWN'
  ELSE 'OTHER'
END""",
        ),
        (
            f"function {fq}.team_name_from_tags",
            build_team_name_function_sql(catalog, schema, tag_keys),
        ),
        (
            f"table {fq}.workspace_reference",
            f"CREATE TABLE IF NOT EXISTS {fq}.workspace_reference "
            f"(workspace_id STRING, workspace_name STRING)",
        ),
        (
            f"table {fq}.workspace_reference rows",
            f"""MERGE INTO {fq}.workspace_reference AS tgt
USING (
  SELECT DISTINCT workspace_id, CAST(workspace_id AS STRING) AS workspace_name
  FROM system.billing.usage
) AS src
ON tgt.workspace_id = src.workspace_id
WHEN NOT MATCHED THEN INSERT (workspace_id, workspace_name)
  VALUES (src.workspace_id, src.workspace_name)""",
        ),
        (
            f"table {fq}.platform_findings",
            f"CREATE TABLE IF NOT EXISTS {fq}.platform_findings "
            f"(run_ts TIMESTAMP, area STRING, check_name STRING, resource STRING, "
            f"reason STRING, action STRING, details STRING)",
        ),
        (
            f"table {fq}.platform_digest",
            f"CREATE TABLE IF NOT EXISTS {fq}.platform_digest "
            f"(run_ts TIMESTAMP, days INT, model STRING, digest STRING, "
            f"findings_json STRING)",
        ),
        (
            f"table {fq}.warehouse_reference",
            f"CREATE TABLE IF NOT EXISTS {fq}.warehouse_reference "
            f"(workspace_id STRING, warehouse_id STRING, warehouse_name STRING)",
        ),
        (
            f"table {fq}.warehouse_reference rows",
            f"""MERGE INTO {fq}.warehouse_reference AS tgt
USING (
  SELECT
    workspace_id,
    GET_JSON_OBJECT(response.result, '$.id') AS warehouse_id,
    MAX(request_params.name) AS warehouse_name
  FROM system.access.audit
  WHERE service_name = 'databrickssql'
    AND GET_JSON_OBJECT(response.result, '$.id') IS NOT NULL
  GROUP BY workspace_id, GET_JSON_OBJECT(response.result, '$.id')
) AS src
ON tgt.workspace_id = src.workspace_id AND tgt.warehouse_id = src.warehouse_id
WHEN MATCHED THEN UPDATE SET tgt.warehouse_name = src.warehouse_name
WHEN NOT MATCHED THEN INSERT (workspace_id, warehouse_id, warehouse_name)
  VALUES (src.workspace_id, src.warehouse_id, src.warehouse_name)""",
        ),
    ]


def run_setup(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    tag_keys: list[str],
    workspace_name: str | None = None,
) -> list[str]:
    """Execute all setup statements; optionally name the current workspace."""
    done = []
    for description, sql in setup_statements(catalog, schema, tag_keys):
        run_query(w, sql, warehouse_id)
        done.append(description)
    if workspace_name:
        ws_id = w.get_workspace_id()
        run_query(
            w,
            f"UPDATE {catalog}.{schema}.workspace_reference "
            "SET workspace_name = :name WHERE workspace_id = :ws_id",
            warehouse_id,
            {"name": workspace_name, "ws_id": str(ws_id)},
        )
        done.append(f"named workspace {ws_id} -> {workspace_name}")
    return done
