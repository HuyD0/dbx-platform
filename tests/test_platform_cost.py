from datetime import date, timedelta

import pytest

from dbx_platform.platform_cost import (
    build_overview,
    scoped_daily_sql,
    service_category,
)


def _row(day: date, service: str, cost: float, currency: str = "CAD") -> dict:
    return {
        "usage_date": day.isoformat(),
        "service_name": service,
        "resource_group": "rg-databricks-dbx-dev",
        "service_bucket": "databricks" if "Databricks" in service else "other",
        "cost": cost,
        "currency": currency,
        "ingested_at": f"{day.isoformat()}T07:00:00Z",
    }


def test_service_category_normalizes_platform_services():
    assert service_category("Azure Databricks") == "Databricks"
    assert service_category("Virtual Machines") == "Compute"
    assert service_category("NAT Gateway") == "Network"
    assert service_category("Azure OpenAI") == "AI / Foundry"
    assert service_category("Log Analytics") == "Monitoring"


def test_scoped_daily_sql_binds_scope_and_resource_groups():
    sql, params = scoped_daily_sql(
        "catalog",
        "schema",
        ["platform-rg", "managed-rg"],
    )

    assert "workspace_id = :workspace_id" in sql
    assert "environment = :environment" in sql
    assert "LOWER(resource_group) IN (:resource_group_0, :resource_group_1)" in sql
    assert params == {
        "resource_group_0": "platform-rg",
        "resource_group_1": "managed-rg",
    }


def test_scoped_daily_sql_rejects_an_unbounded_total():
    with pytest.raises(ValueError, match="resource group"):
        scoped_daily_sql("catalog", "schema", [])


def test_overview_keeps_azure_actual_and_databricks_list_non_additive():
    start = date(2026, 6, 1)
    azure_rows = []
    for offset in range(60):
        day = start + timedelta(days=offset)
        azure_rows.extend(
            [
                _row(day, "Azure Databricks", 10),
                _row(day, "Storage", 2),
            ]
        )
    databricks_rows = [
        {
            "workspace_id": "740",
            "sku_name": "JOBS_COMPUTE",
            "list_cost_usd": 75,
        },
        {
            "workspace_id": "another-workspace",
            "sku_name": "SQL",
            "list_cost_usd": 900,
        },
    ]

    result = build_overview(
        azure_rows,
        databricks_rows,
        days=30,
        workspace_id="740",
        environment="prod",
        resource_groups=["rg-databricks-dbx-dev"],
    )

    assert result["totals"] == [
        {
            "currency": "CAD",
            "cost": 360.0,
            "previous_period_cost": 360.0,
            "period_delta_pct": 0.0,
            "cost_basis": "AZURE_ACTUAL",
        }
    ]
    assert sum(component["cost"] for component in result["components"]) == 360
    assert result["databricks_list"]["cost"] == 75
    assert result["databricks_list"]["currency"] == "USD"
    assert result["databricks_list"]["cost_basis"] == "DATABRICKS_LIST"
    assert result["databricks_list"]["additive_to_total"] is False


def test_overview_never_combines_currencies():
    start = date(2026, 7, 1)
    rows = [
        _row(start + timedelta(days=offset), "Azure Databricks", 10, "CAD")
        for offset in range(10)
    ]
    rows.extend(
        _row(start + timedelta(days=offset), "Storage", 5, "USD")
        for offset in range(10)
    )

    result = build_overview(
        rows,
        [],
        days=7,
        workspace_id="740",
        environment="prod",
        resource_groups=["platform-rg"],
    )

    assert {(row["currency"], row["cost"]) for row in result["totals"]} == {
        ("CAD", 70.0),
        ("USD", 35.0),
    }


def test_overview_detects_daily_spike_and_seven_day_acceleration():
    start = date(2026, 7, 1)
    rows = []
    for offset in range(22):
        cost = 10 if offset < 14 else 25
        if offset == 20:
            cost = 80
        rows.append(_row(start + timedelta(days=offset), "Azure Databricks", cost))

    result = build_overview(
        rows,
        [],
        days=21,
        workspace_id="740",
        environment="prod",
        resource_groups=["platform-rg"],
        spike_pct=50,
        spike_min_cost=10,
        acceleration_pct=30,
        acceleration_min_cost=25,
    )

    signals = {anomaly["signal"] for anomaly in result["anomalies"]}
    assert "Daily spike" in signals
    assert "7-day acceleration" in signals
    assert all(anomaly["cost_basis"] == "AZURE_ACTUAL" for anomaly in result["anomalies"])
