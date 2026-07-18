"""Deployment-time Unity Catalog migrations on serverless Spark.

The unscheduled deployment job never starts the managed Mission Control SQL
warehouse, preserving a durable SLEEPING state across bundle deployments.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from dbx_platform.control_plane_procedures import procedure_statements
from dbx_platform.control_plane_schema import migrate_control_plane_with_spark
from dbx_platform.dashboards import setup_statements


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default="main")
    parser.add_argument("--schema", default="dbx_platform")
    parser.add_argument("--team-tags", default="team,cost-center,environment")
    parser.add_argument("--operator-group", default="dbx-platform-operators")
    parser.add_argument("--approver-group", default="dbx-platform-approvers")
    return parser


def run_migrations(
    spark,
    catalog: str,
    schema: str,
    team_tags: list[str],
    *,
    operator_group: str = "dbx-platform-operators",
    approver_group: str = "dbx-platform-approvers",
) -> list[str]:
    """Apply idempotent internal schema and dashboard-helper migrations."""

    completed: list[str] = []
    for description, sql in setup_statements(catalog, schema, team_tags):
        spark.sql(sql)
        completed.append(description)
    completed.extend(migrate_control_plane_with_spark(spark, catalog, schema))
    for description, sql in procedure_statements(
        catalog,
        schema,
        operator_group=operator_group,
        approver_group=approver_group,
    ):
        spark.sql(sql)
        completed.append(description)
    return completed


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from pyspark.sql import SparkSession

        spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
        completed = run_migrations(
            spark,
            args.catalog,
            args.schema,
            [value.strip() for value in args.team_tags.split(",") if value.strip()],
            operator_group=args.operator_group,
            approver_group=args.approver_group,
        )
        print(
            json.dumps(
                {
                    "status": "SUCCEEDED",
                    "migration_count": len(completed),
                    "migrations": completed,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except Exception as exc:
        print(f"schema migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
