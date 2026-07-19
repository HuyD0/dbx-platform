"""Canonical Unity Catalog schema for governed actions and runtime state.

Migrations run under the deployment/setup identity.  Application and executor
identities only read or append to these tables and must fail closed when the
schema is unavailable.
"""

from __future__ import annotations

import re

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import run_query

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

ACTION_REQUEST_COLUMNS = """
workspace_id STRING NOT NULL,
environment STRING NOT NULL,
action_id STRING NOT NULL,
action_type STRING NOT NULL,
status STRING NOT NULL,
plan_json STRING NOT NULL,
plan_hash STRING NOT NULL,
confirm_phrase STRING NOT NULL,
risk STRING NOT NULL,
proposer_id STRING NOT NULL,
proposer_email STRING,
created_at TIMESTAMP NOT NULL,
expires_at TIMESTAMP NOT NULL,
updated_at TIMESTAMP NOT NULL,
idempotency_key STRING NOT NULL,
terminal_reason STRING
"""

ACTION_APPROVAL_COLUMNS = """
workspace_id STRING NOT NULL,
environment STRING NOT NULL,
approval_id STRING NOT NULL,
action_id STRING NOT NULL,
plan_hash STRING NOT NULL,
decision STRING NOT NULL,
approver_id STRING NOT NULL,
approver_email STRING,
approver_role STRING NOT NULL,
confirmation STRING,
decided_at TIMESTAMP NOT NULL
"""

ACTION_EVENT_COLUMNS = """
workspace_id STRING NOT NULL,
environment STRING NOT NULL,
event_id STRING NOT NULL,
action_id STRING NOT NULL,
event_type STRING NOT NULL,
from_status STRING,
to_status STRING,
actor_id STRING,
details_json STRING NOT NULL,
event_ts TIMESTAMP NOT NULL
"""

MANAGED_RESOURCE_COLUMNS = """
workspace_id STRING NOT NULL,
environment STRING NOT NULL,
resource_id STRING NOT NULL,
resource_type STRING NOT NULL,
display_name STRING,
bundle_key STRING NOT NULL,
ownership STRING NOT NULL,
stoppable BOOLEAN NOT NULL,
protected BOOLEAN NOT NULL,
stop_order INT NOT NULL,
state STRING,
metadata_json STRING,
updated_at TIMESTAMP NOT NULL
"""

RUNTIME_STATE_COLUMNS = """
workspace_id STRING NOT NULL,
environment STRING NOT NULL,
desired_state STRING NOT NULL,
actual_state STRING NOT NULL,
prior_state_json STRING,
active_action_id STRING,
last_reconciled_at TIMESTAMP,
updated_at TIMESTAMP NOT NULL,
version BIGINT NOT NULL
"""

FINDING_COLUMNS: dict[str, str] = {
    "run_ts": "TIMESTAMP",
    "area": "STRING",
    "check_name": "STRING",
    "resource": "STRING",
    "reason": "STRING",
    "action": "STRING",
    "details": "STRING",
    "finding_id": "STRING",
    "workspace_id": "STRING",
    "environment": "STRING",
    "pillar": "STRING",
    "severity": "STRING",
    "likelihood": "STRING",
    "financial_impact_usd": "DOUBLE",
    "slo_impact": "STRING",
    "confidence": "DOUBLE",
    "owner": "STRING",
    "affected_resources_json": "STRING",
    "evidence_json": "STRING",
    "freshness_at": "TIMESTAMP",
    "first_seen_at": "TIMESTAMP",
    "last_seen_at": "TIMESTAMP",
    "state": "STRING",
    "proposed_action_type": "STRING",
    "blast_radius": "STRING",
}

MIGRATION_COLUMNS: dict[str, dict[str, str]] = {
    "azure_costs": {
        "workspace_id": "STRING",
        "environment": "STRING",
    },
    "azure_cost_details": {
        "workspace_id": "STRING",
        "environment": "STRING",
    },
    "action_approvals": {
        "workspace_id": "STRING",
        "environment": "STRING",
    },
    "action_events": {
        "workspace_id": "STRING",
        "environment": "STRING",
    },
    "platform_findings": FINDING_COLUMNS,
    "platform_digest": {
        "workspace_id": "STRING",
        "environment": "STRING",
    },
}
APPEND_ONLY_TABLES = ("action_approvals", "action_events")
TRANSACTIONAL_TABLES = ("action_requests", "action_approvals", "action_events")


def _safe_identifier(value: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Unsafe Unity Catalog identifier: {value!r}")
    return value


def create_table_statements(catalog: str, schema: str) -> list[tuple[str, str]]:
    """Return idempotent create statements shared by setup and migrations."""

    catalog = _safe_identifier(catalog)
    schema = _safe_identifier(schema)
    fq = f"`{catalog}`.`{schema}`"
    tables = {
        "action_requests": ACTION_REQUEST_COLUMNS,
        "action_approvals": ACTION_APPROVAL_COLUMNS,
        "action_events": ACTION_EVENT_COLUMNS,
        "managed_resources": MANAGED_RESOURCE_COLUMNS,
        "platform_runtime_state": RUNTIME_STATE_COLUMNS,
        "platform_findings": ",\n".join(
            f"`{name}` {data_type}" for name, data_type in FINDING_COLUMNS.items()
        ),
    }
    statements = []
    for name, columns in tables.items():
        catalog_commits = (
            " TBLPROPERTIES ('delta.feature.catalogManaged' = 'supported')"
            if name in TRANSACTIONAL_TABLES
            else ""
        )
        statements.append(
            (
                f"table {catalog}.{schema}.{name}",
                f"CREATE TABLE IF NOT EXISTS {fq}.`{name}` ({columns}) "
                f"USING DELTA{catalog_commits}",
            )
        )
    return statements


def catalog_commit_statements(catalog: str, schema: str) -> list[tuple[str, str]]:
    """Enable catalog commits required by multi-table atomic procedures."""

    catalog = _safe_identifier(catalog)
    schema = _safe_identifier(schema)
    fq = f"`{catalog}`.`{schema}`"
    return [
        (
            f"enabled catalog commits on {catalog}.{schema}.{table}",
            f"ALTER TABLE {fq}.`{table}` SET TBLPROPERTIES "
            "('delta.feature.catalogManaged' = 'supported')",
        )
        for table in TRANSACTIONAL_TABLES
    ]


def migrate_control_plane_tables(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
) -> list[str]:
    """Create durable tables and extend a legacy findings table in place."""

    statements = create_table_statements(catalog, schema)
    done: list[str] = []
    for description, sql in statements:
        run_query(w, sql, warehouse_id)
        done.append(description)

    fq = f"`{_safe_identifier(catalog)}`.`{_safe_identifier(schema)}`"
    for table, required in MIGRATION_COLUMNS.items():
        described = run_query(
            w,
            f"DESCRIBE TABLE {fq}.`{table}`",
            warehouse_id,
        )
        existing = {
            str(row.get("col_name") or "").strip("`").lower()
            for row in described
            if row.get("col_name") and not str(row["col_name"]).startswith("#")
        }
        missing = [
            f"`{name}` {data_type}"
            for name, data_type in required.items()
            if name.lower() not in existing
        ]
        if missing:
            run_query(
                w,
                f"ALTER TABLE {fq}.`{table}` "
                f"ADD COLUMNS ({', '.join(missing)})",
                warehouse_id,
            )
            done.append(f"migrated table {catalog}.{schema}.{table}")
    for table in APPEND_ONLY_TABLES:
        run_query(
            w,
            f"ALTER TABLE {fq}.`{table}` SET TBLPROPERTIES "
            "('delta.appendOnly' = 'true')",
            warehouse_id,
        )
        done.append(f"protected append-only table {catalog}.{schema}.{table}")
    for description, sql in catalog_commit_statements(catalog, schema):
        run_query(w, sql, warehouse_id)
        done.append(description)
    return done


def migrate_control_plane_with_spark(
    spark,
    catalog: str,
    schema: str,
) -> list[str]:
    """Spark equivalent used by the deployment migration job.

    This deliberately avoids the managed SQL warehouse so deployment cannot
    wake a toolkit whose durable intent is SLEEPING.
    """

    done: list[str] = []
    for description, sql in create_table_statements(catalog, schema):
        spark.sql(sql)
        done.append(description)

    fq = f"`{_safe_identifier(catalog)}`.`{_safe_identifier(schema)}`"
    for table, required in MIGRATION_COLUMNS.items():
        described = spark.sql(
            f"DESCRIBE TABLE {fq}.`{table}`"
        ).collect()
        existing = set()
        for row in described:
            values = row.asDict(recursive=True)
            name = str(values.get("col_name") or "")
            if name and not name.startswith("#"):
                existing.add(name.strip("`").lower())
        missing = [
            f"`{name}` {data_type}"
            for name, data_type in required.items()
            if name.lower() not in existing
        ]
        if missing:
            spark.sql(
                f"ALTER TABLE {fq}.`{table}` "
                f"ADD COLUMNS ({', '.join(missing)})"
            )
            done.append(f"migrated table {catalog}.{schema}.{table}")
    for table in APPEND_ONLY_TABLES:
        spark.sql(
            f"ALTER TABLE {fq}.`{table}` SET TBLPROPERTIES "
            "('delta.appendOnly' = 'true')"
        )
        done.append(f"protected append-only table {catalog}.{schema}.{table}")
    for description, sql in catalog_commit_statements(catalog, schema):
        spark.sql(sql)
        done.append(description)
    return done


def required_table_names() -> tuple[str, ...]:
    """Tables an executor must find before it can claim an action."""

    return (
        "action_requests",
        "action_approvals",
        "action_events",
        "managed_resources",
        "platform_runtime_state",
        "platform_findings",
    )
