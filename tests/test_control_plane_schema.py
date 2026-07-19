"""Schema contracts for the governed action ledger."""

from __future__ import annotations

from dbx_platform.control_plane_schema import (
    TRANSACTIONAL_TABLES,
    catalog_commit_statements,
    create_table_statements,
)


def test_action_ledger_tables_enable_catalog_commits_on_create_and_upgrade():
    create_sql = {
        description: sql
        for description, sql in create_table_statements("main", "control")
    }
    upgrades = catalog_commit_statements("main", "control")

    assert len(upgrades) == len(TRANSACTIONAL_TABLES)
    for table in TRANSACTIONAL_TABLES:
        assert (
            "'delta.feature.catalogManaged' = 'supported'"
            in create_sql[f"table main.control.{table}"]
        )
        assert any(
            f"`main`.`control`.`{table}`" in sql
            and "'delta.feature.catalogManaged' = 'supported'" in sql
            for _description, sql in upgrades
        )


def test_nontransactional_tables_do_not_require_catalog_commits():
    create_sql = {
        description: sql
        for description, sql in create_table_statements("main", "control")
    }

    assert "delta.feature.catalogManaged" not in create_sql[
        "table main.control.managed_resources"
    ]
