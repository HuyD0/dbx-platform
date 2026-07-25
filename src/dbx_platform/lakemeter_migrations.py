"""Governed LakeMeter schema and pricing reconciliation.

This module is executed only by the unscheduled LakeMeter migration Job. The
Platform Console imports none of it and never performs runtime DDL.
"""

from __future__ import annotations

import argparse
import ast
import csv
import gzip
import io
import json
import sys
from collections.abc import Sequence
from importlib import resources
from pathlib import Path
from typing import Any

from databricks.sdk import WorkspaceClient

SCHEMA = "lakemeter"

PRICING_LOADS = (
    (
        "dbu-rates.csv.gz",
        "sync_pricing_dbu_rates",
        (
            "sku_name",
            "cloud",
            "tier",
            "product_type",
            "sku_region",
            "region",
            "usage_unit",
            "price_per_dbu",
            "currency_code",
            "pricing_type",
            "fetched_at",
        ),
    ),
    (
        "instance-dbu-rates.csv.gz",
        "sync_ref_instance_dbu_rates",
        (
            "cloud",
            "instance_type",
            "vcpus",
            "memory_gb",
            "dbu_rate",
            "instance_family",
            "is_active",
            "source",
        ),
    ),
    (
        "dbu-multipliers.csv.gz",
        "sync_ref_dbu_multipliers",
        ("cloud", "sku_type", "feature", "multiplier", "category"),
    ),
    (
        "dbsql-rates.csv.gz",
        "sync_product_dbsql_rates",
        (
            "cloud",
            "warehouse_type",
            "warehouse_size",
            "sku_product_type",
            "dbu_per_hour",
            "includes_compute",
        ),
    ),
    (
        "dbsql-warehouse-config.csv.gz",
        "sync_ref_dbsql_warehouse_config",
        (
            "cloud",
            "warehouse_size",
            "worker_count",
            "driver_instance_type",
            "worker_instance_type",
            "warehouse_type",
        ),
    ),
    (
        "serverless-rates.csv.gz",
        "sync_product_serverless_rates",
        (
            "cloud",
            "product",
            "size_or_model",
            "rate_type",
            "dbu_rate",
            "input_divisor",
            "is_hourly",
            "sku_product_type",
            "description",
        ),
    ),
    (
        "fmapi-databricks-rates.csv.gz",
        "sync_product_fmapi_databricks",
        (
            "cloud",
            "model",
            "rate_type",
            "dbu_rate",
            "input_divisor",
            "is_hourly",
            "sku_product_type",
        ),
    ),
    (
        "fmapi-proprietary-rates.csv.gz",
        "sync_product_fmapi_proprietary",
        (
            "provider",
            "model",
            "endpoint_type",
            "context_length",
            "rate_type",
            "dbu_rate",
            "input_divisor",
            "is_hourly",
            "sku_product_type",
            "cloud",
        ),
    ),
    (
        "vm-costs.csv.gz",
        "sync_pricing_vm_costs",
        (
            "cloud",
            "region",
            "instance_type",
            "pricing_tier",
            "payment_option",
            "cost_per_hour",
            "currency",
            "source",
            "fetched_at",
        ),
    ),
    (
        "sku-region-map.csv.gz",
        "sync_ref_sku_region_map",
        ("cloud", "sku_region", "region_code"),
    ),
)

FLOAT_COLUMNS = frozenset(
    {
        "price_per_dbu",
        "vcpus",
        "memory_gb",
        "dbu_rate",
        "dbu_per_hour",
        "cost_per_hour",
        "multiplier",
    }
)
BOOL_COLUMNS = frozenset({"is_active", "includes_compute", "is_hourly"})


class MigrationError(RuntimeError):
    """LakeMeter migration failed closed."""


def _vendor_file(*parts: str):
    packaged = resources.files("dbx_platform").joinpath("lakemeter_vendor", *parts)
    if packaged.is_file() or packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2].joinpath("vendor", "lakemeter", *parts)


def _pricing_file(name: str):
    return resources.files("dbx_platform").joinpath("lakemeter_assets", "pricing", name)


def _assignment(source: str, function_name: str, variable_name: str) -> Any:
    tree = ast.parse(source)
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function_name
        ),
        None,
    )
    if function is None:
        raise MigrationError(f"Upstream function is missing: {function_name}")
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == variable_name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise MigrationError(
        f"Upstream migration input is missing: {function_name}.{variable_name}"
    )


def _sync_table_sql(source: str) -> tuple[str, ...]:
    tree = ast.parse(source)
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "_create_sync_tables"
        ),
        None,
    )
    if function is None:
        raise MigrationError("Upstream sync table migration is missing.")
    statements: list[str] = []
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            statements.append(node.args[0].value)
    if not statements:
        raise MigrationError("Upstream sync table SQL is empty.")
    return tuple(statements)


def _function_sql() -> tuple[str, ...]:
    statements: list[str] = []
    function_dir = _vendor_file("scripts", "functions")
    for source_file in sorted(function_dir.iterdir(), key=lambda item: item.name):
        if source_file.suffix != ".py":
            continue
        tree = ast.parse(source_file.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "create_function_sql"
                for target in node.targets
            ):
                continue
            sql_text = ast.literal_eval(node.value)
            if not isinstance(sql_text, str) or "CREATE OR REPLACE FUNCTION" not in sql_text:
                raise MigrationError(f"Invalid function migration in {source_file.name}")
            statements.append(sql_text)
    if len(statements) < 10:
        raise MigrationError("Pinned LakeMeter function set is incomplete.")
    return tuple(statements)


def _cast_value(column: str, value: str | None) -> object:
    if not value:
        if column in FLOAT_COLUMNS:
            return 0.0
        if column in BOOL_COLUMNS:
            return False
        return ""
    if column in FLOAT_COLUMNS:
        return float(value)
    if column in BOOL_COLUMNS:
        return value.lower() in {"true", "1", "yes"}
    return value


def _load_pricing(cursor) -> dict[str, int]:
    from psycopg2 import sql
    from psycopg2.extras import execute_values

    counts: dict[str, int] = {}
    for filename, table, columns in PRICING_LOADS:
        raw = gzip.decompress(_pricing_file(filename).read_bytes()).decode("utf-8")
        rows = [
            tuple(_cast_value(column, row.get(column)) for column in columns)
            for row in csv.DictReader(io.StringIO(raw))
        ]
        if not rows:
            raise MigrationError(f"Pinned pricing file is empty: {filename}")
        cursor.execute(
            sql.SQL("TRUNCATE TABLE {}.{}").format(
                sql.Identifier(SCHEMA),
                sql.Identifier(table),
            )
        )
        insert = sql.SQL("INSERT INTO {}.{} ({}) VALUES %s").format(
            sql.Identifier(SCHEMA),
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
        )
        execute_values(cursor, insert.as_string(cursor), rows, page_size=2000)
        counts[table] = len(rows)
    return counts


def _apply_schema(cursor) -> None:
    installer = _vendor_file("scripts", "install_lakemeter.py").read_text(encoding="utf-8")
    for statement in _assignment(installer, "_create_tables_inline", "table_stmts"):
        cursor.execute(statement)
    for statement in _sync_table_sql(installer):
        cursor.execute(statement)

    # v0.1.0's model uses this field but its installer omits it. Keeping this
    # additive correction in the adapter is intentional and compatibility-tested.
    cursor.execute(
        "ALTER TABLE lakemeter.estimates "
        "ADD COLUMN IF NOT EXISTS display_order INT DEFAULT 0"
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS lakemeter.integration_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS lakemeter.sku_discount_mapping (
            sku TEXT PRIMARY KEY,
            sku_display_name TEXT,
            discount_category TEXT NOT NULL
                CHECK (discount_category IN ('dbu','storage','support','network','excluded')),
            cross_service_eligible BOOLEAN NOT NULL DEFAULT TRUE,
            notes TEXT
        )
        """
    )

    workload_types = _module_assignment(
        _vendor_file("backend", "app", "routes", "workload_types.py").read_text(
            encoding="utf-8"
        ),
        "DEFAULT_WORKLOAD_TYPES",
    )
    workload_columns = tuple(workload_types[0])
    from psycopg2 import sql
    from psycopg2.extras import execute_values

    workload_insert = sql.SQL(
        "INSERT INTO lakemeter.ref_workload_types ({}) VALUES %s "
        "ON CONFLICT (workload_type) DO UPDATE SET {}"
    ).format(
        sql.SQL(", ").join(map(sql.Identifier, workload_columns)),
        sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(column), sql.Identifier(column))
            for column in workload_columns
            if column != "workload_type"
        ),
    )
    execute_values(
        cursor,
        workload_insert.as_string(cursor),
        [tuple(item[column] for column in workload_columns) for item in workload_types],
    )

    cloud_tiers = _assignment(installer, "_create_tables_inline", "cloud_tier_seeds")
    execute_values(
        cursor,
        """
        INSERT INTO lakemeter.ref_cloud_tiers
          (cloud, tier, display_name, description, display_order, is_active)
        VALUES %s
        ON CONFLICT (cloud, tier) DO UPDATE SET
          display_name = EXCLUDED.display_name,
          description = EXCLUDED.description,
          display_order = EXCLUDED.display_order,
          is_active = EXCLUDED.is_active
        """,
        cloud_tiers,
    )


def _module_assignment(source: str, variable_name: str) -> Any:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == variable_name
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise MigrationError(f"Upstream module assignment is missing: {variable_name}")


def _apply_reference_data(cursor) -> dict[str, int]:
    counts = _load_pricing(cursor)
    cursor.execute(
        """
        INSERT INTO lakemeter.sku_discount_mapping
          (sku, sku_display_name, discount_category)
        SELECT DISTINCT sku_name, sku_name, 'dbu'
        FROM lakemeter.sync_pricing_dbu_rates
        WHERE sku_name IS NOT NULL AND sku_name <> ''
        ON CONFLICT (sku) DO NOTHING
        """
    )
    for statement in _function_sql():
        cursor.execute(statement)
    return counts


def _grant_app_access(cursor, database: str, app_role: str) -> None:
    from psycopg2 import sql

    role = sql.Identifier(app_role)
    cursor.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database),
            role,
        )
    )
    cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA lakemeter TO {}").format(role))
    cursor.execute(
        sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(
            sql.Identifier(database),
            role,
        )
    )
    cursor.execute(sql.SQL("REVOKE CREATE ON SCHEMA lakemeter FROM {}").format(role))
    cursor.execute(
        sql.SQL(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
            "IN SCHEMA lakemeter TO {}"
        ).format(role)
    )
    cursor.execute(
        sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA lakemeter TO {}").format(
            role
        )
    )
    cursor.execute(
        sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA lakemeter TO {}").format(role)
    )


def _identity_matches(me: object, expected: str) -> bool:
    identities = {
        str(getattr(me, "id", "") or ""),
        str(getattr(me, "user_name", "") or ""),
        str(getattr(me, "application_id", "") or ""),
    }
    return expected in identities


def _connect(w: WorkspaceClient, endpoint_name: str, database: str, user: str):
    import psycopg2

    endpoint = w.postgres.get_endpoint(endpoint_name)
    host = getattr(getattr(endpoint, "status", None), "hosts", None)
    host_name = str(getattr(host, "host", "") or "")
    credential = w.postgres.generate_database_credential(endpoint_name)
    token = str(credential.token or "")
    if not host_name or not token:
        raise MigrationError("Lakebase endpoint returned no host or OAuth credential.")
    return psycopg2.connect(
        host=host_name,
        port=5432,
        dbname=database,
        user=user,
        password=token,
        sslmode="require",
        connect_timeout=15,
        application_name="dbx-platform-lakemeter-migration",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--expected-executor", required=True)
    parser.add_argument("--schema-version", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        w = WorkspaceClient()
        if not _identity_matches(w.current_user.me(), args.expected_executor):
            raise MigrationError(
                "Current job identity is not the configured LakeMeter migration executor."
            )
        app = w.apps.get(args.app_name)
        app_role = str(app.service_principal_client_id or "")
        if not app_role:
            raise MigrationError("Platform Console service principal is unavailable.")

        lock = json.loads(
            resources.files("dbx_platform")
            .joinpath("lakemeter_assets", "pricing", "manifest.json")
            .read_text(encoding="utf-8")
        )
        connection = _connect(w, args.endpoint, args.database, args.expected_executor)
        try:
            with connection:
                with connection.cursor() as cursor:
                    _apply_schema(cursor)
                    counts = _apply_reference_data(cursor)
                    _grant_app_access(cursor, args.database, app_role)
                    metadata = {
                        "schema_version": str(args.schema_version),
                        "pricing_version": str(lock.get("generated_at", "unknown")),
                    }
                    for key, value in metadata.items():
                        cursor.execute(
                            """
                            INSERT INTO lakemeter.integration_metadata (key, value)
                            VALUES (%s, %s)
                            ON CONFLICT (key) DO UPDATE
                            SET value = EXCLUDED.value, updated_at = NOW()
                            """,
                            (key, value),
                        )
                    cursor.execute("SELECT COUNT(*) FROM lakemeter.ref_workload_types")
                    if int(cursor.fetchone()[0]) < 10:
                        raise MigrationError("Workload reference data verification failed.")
                    cursor.execute(
                        "SELECT lakemeter.calculate_hours_per_month(%s,%s,%s,%s,%s,%s)",
                        ("JOBS", 1, 60, 30, None, None),
                    )
                    if float(cursor.fetchone()[0]) != 30.0:
                        raise MigrationError("LakeMeter calculation function smoke test failed.")
        finally:
            connection.close()

        print(
            json.dumps(
                {
                    "status": "SUCCEEDED",
                    "schema_version": args.schema_version,
                    "pricing_version": lock.get("generated_at", "unknown"),
                    "pricing_rows": counts,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error": type(exc).__name__,
                    "detail": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
