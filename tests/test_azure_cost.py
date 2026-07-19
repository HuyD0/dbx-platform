import json
from datetime import date

import pytest

from dbx_platform.azure_cost import (
    build_detail_query_body,
    build_query_body,
    classify_azure_spend,
    create_detail_table_sql,
    create_table_sql,
    inclusive_date_window,
    merge_costs_sql,
    merge_detail_costs_sql,
    parse_detail_query_result,
    parse_query_result,
    report_sql,
    service_bucket,
    store_costs,
    store_detail_costs,
)
from dbx_platform.control_plane_schema import MIGRATION_COLUMNS


def test_inclusive_date_window_has_exact_requested_days():
    start, end = inclusive_date_window(date(2026, 7, 19), 365)
    assert start == date(2025, 7, 20)
    assert end == date(2026, 7, 19)
    assert (end - start).days + 1 == 365


def test_inclusive_date_window_rejects_nonpositive_days():
    with pytest.raises(ValueError, match="at least 1"):
        inclusive_date_window(date(2026, 7, 19), 0)


# --- service_bucket -------------------------------------------------------------


def test_databricks_bucket():
    assert service_bucket("Azure Databricks") == "databricks"


def test_foundry_buckets():
    for name in ("Cognitive Services", "Azure OpenAI Service", "Azure AI Foundry",
                 "Azure Machine Learning", "Azure AI Services"):
        assert service_bucket(name) == "foundry_ai", name


def test_search_bucket_not_swallowed_by_cognitive():
    # "Azure Cognitive Search" must land in search, not foundry_ai.
    assert service_bucket("Azure Cognitive Search") == "search"
    assert service_bucket("Azure AI Search") == "search"


def test_storage_bucket():
    assert service_bucket("Storage") == "storage"


def test_unknown_and_empty_are_other():
    assert service_bucket("Virtual Machines") == "other"
    assert service_bucket("") == "other"
    assert service_bucket(None) == "other"


# --- parse_query_result ---------------------------------------------------------

def _page(rows, cols=None):
    cols = cols or ["PreTaxCost", "UsageDate", "ServiceName", "ResourceGroup", "Currency"]
    return {"properties": {"columns": [{"name": c} for c in cols], "rows": rows}}


def test_parse_flattens_and_buckets():
    rows = parse_query_result(
        [_page([[12.5, 20260701, "Azure Databricks", "rg-data", "USD"]])]
    )
    assert rows == [
        {"usage_date": "2026-07-01", "service_name": "Azure Databricks",
         "resource_group": "rg-data", "service_bucket": "databricks",
         "cost": 12.5, "currency": "USD"}
    ]


def test_parse_handles_multiple_pages_and_missing_values():
    pages = [
        _page([[1.0, 20260701, "Storage", "rg-a", "USD"]]),
        _page([[None, 20260702, None, None, None]]),
    ]
    rows = parse_query_result(pages)
    assert len(rows) == 2
    assert rows[1]["cost"] == 0.0
    assert rows[1]["service_bucket"] == "other"


def test_parse_empty_payload():
    assert parse_query_result([{"properties": {}}]) == []


def test_parse_detail_extracts_resource_and_meter():
    resource_id = (
        "/subscriptions/sub/resourceGroups/rg-ai/providers/"
        "Microsoft.CognitiveServices/accounts/aoai-prod"
    )
    page = _page(
        [[4.25, 20260701, resource_id, "gpt-5 input tokens", "CAD"]],
        ["Cost", "UsageDate", "ResourceId", "Meter", "Currency"],
    )
    assert parse_detail_query_result([page]) == [
        {
            "usage_date": "2026-07-01",
            "resource_id": resource_id,
            "resource_group": "rg-ai",
            "resource_type": "Microsoft.CognitiveServices/accounts",
            "meter_name": "gpt-5 input tokens",
            "service_bucket": "foundry_ai",
            "cost": 4.25,
            "currency": "CAD",
        }
    ]


# --- SQL builders ---------------------------------------------------------------

def test_query_body_shape():
    body = build_query_body("2026-07-01", "2026-07-03")
    assert body["type"] == "Usage"
    assert body["dataset"]["granularity"] == "Daily"
    assert body["dataset"]["aggregation"]["totalCost"]["name"] == "PreTaxCost"
    names = [g["name"] for g in body["dataset"]["grouping"]]
    assert names == ["ServiceName", "ResourceGroup"]


def test_detail_query_uses_resource_and_meter_dimensions():
    body = build_detail_query_body("2026-07-01", "2026-07-03")
    assert body["type"] == "Usage"
    assert body["dataset"]["aggregation"]["totalCost"]["name"] == "PreTaxCost"
    names = [g["name"] for g in body["dataset"]["grouping"]]
    assert names == ["ResourceId", "Meter"]


def test_merge_sql_targets_table_and_binds_rows_param():
    sql = merge_costs_sql("main", "dbx_platform")
    assert "MERGE INTO main.dbx_platform.azure_costs" in sql
    assert ":rows" in sql
    assert "t.usage_date = s.usage_date" in sql
    assert "t.workspace_id = :workspace_id" in sql
    assert "t.environment = :environment" in sql
    assert "t.currency = s.currency" in sql
    assert "WHEN NOT MATCHED BY SOURCE" in sql
    assert "t.usage_date BETWEEN CAST(:window_start AS DATE)" in sql


def test_detail_merge_uses_resource_meter_key():
    sql = merge_detail_costs_sql("main", "dbx_platform")
    assert "MERGE INTO main.dbx_platform.azure_cost_details" in sql
    assert "t.resource_id = s.resource_id" in sql
    assert "t.meter_name = s.meter_name" in sql
    assert "t.workspace_id = :workspace_id" in sql
    assert "WHEN NOT MATCHED BY SOURCE" in sql


def test_create_table_sql_has_bucket_column():
    assert "service_bucket STRING" in create_table_sql("main", "dbx_platform")
    assert "resource_id STRING" in create_detail_table_sql("main", "dbx_platform")
    assert "workspace_id STRING" in create_table_sql("main", "dbx_platform")
    assert "environment STRING" in create_detail_table_sql("main", "dbx_platform")


def test_migration_extends_legacy_azure_tables_with_deployment_scope():
    for table in ("azure_costs", "azure_cost_details"):
        assert MIGRATION_COLUMNS[table] == {
            "workspace_id": "STRING",
            "environment": "STRING",
        }


@pytest.mark.parametrize(
    ("writer", "table_fragment"),
    [
        (store_costs, "azure_costs"),
        (store_detail_costs, "azure_cost_details"),
    ],
)
def test_store_reconciles_empty_window_once_without_ddl(
    monkeypatch, writer, table_fragment
):
    calls = []
    monkeypatch.setattr(
        "dbx_platform.azure_cost.run_query",
        lambda _w, sql, _warehouse, params=None, **_kwargs: calls.append(
            (sql, params)
        )
        or [],
    )

    assert writer(
        object(),
        "warehouse",
        "main",
        "dbx_platform",
        [],
        workspace_id="w1",
        environment="prod",
        window_start="2026-07-14",
        window_end="2026-07-17",
    ) == 0

    assert len(calls) == 1
    sql, params = calls[0]
    assert table_fragment in sql
    assert "WHEN NOT MATCHED BY SOURCE" in sql
    assert "CREATE TABLE" not in sql
    assert json.loads(params["rows"]) == []
    assert params["workspace_id"] == "w1"
    assert params["environment"] == "prod"
    assert params["window_start"] == "2026-07-14"
    assert params["window_end"] == "2026-07-17"


def test_store_uses_one_atomic_merge_for_large_late_adjustment_window(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dbx_platform.azure_cost.run_query",
        lambda _w, sql, _warehouse, params=None, **_kwargs: calls.append(
            (sql, params)
        )
        or [],
    )
    rows = [
        {
            "usage_date": "2026-07-16",
            "service_name": f"service-{index}",
            "resource_group": "rg",
            "service_bucket": "other",
            "cost": 1.0,
            "currency": "CAD",
        }
        for index in range(2001)
    ]

    store_costs(
        object(),
        "warehouse",
        "main",
        "dbx_platform",
        rows,
        workspace_id="w1",
        environment="prod",
        window_start="2026-07-14",
        window_end="2026-07-17",
    )

    assert len(calls) == 1
    assert len(json.loads(calls[0][1]["rows"])) == 2001


def test_store_rejects_rows_outside_reprocessed_window(monkeypatch):
    monkeypatch.setattr(
        "dbx_platform.azure_cost.run_query",
        lambda *_args, **_kwargs: pytest.fail("invalid input must not write"),
    )
    with pytest.raises(ValueError, match="outside the reconciliation window"):
        store_costs(
            object(),
            "warehouse",
            "main",
            "dbx_platform",
            [
                {
                    "usage_date": "2026-07-13",
                    "service_name": "Azure Databricks",
                    "resource_group": "rg",
                    "service_bucket": "databricks",
                    "cost": 1.0,
                    "currency": "CAD",
                }
            ],
            workspace_id="w1",
            environment="prod",
            window_start="2026-07-14",
            window_end="2026-07-17",
        )


def test_store_failure_has_migration_guidance(monkeypatch):
    monkeypatch.setattr(
        "dbx_platform.azure_cost.run_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(Exception("TABLE_NOT_FOUND")),
    )
    with pytest.raises(RuntimeError, match="schema_migrations"):
        store_costs(
            object(),
            "warehouse",
            "main",
            "dbx_platform",
            [],
            workspace_id="w1",
            environment="prod",
            window_start="2026-07-14",
            window_end="2026-07-17",
        )


def test_report_sql_whitelists_dimension():
    assert "GROUP BY service_bucket" in report_sql("main", "dbx_platform", "bucket")
    try:
        report_sql("main", "dbx_platform", "usage_date; DROP TABLE x")
    except ValueError:
        pass
    else:
        raise AssertionError("unexpected dimension must raise")


# --- classify_azure_spend -------------------------------------------------------

def _daily(bucket, day_costs):
    return [{"usage_date": d, "service_bucket": bucket, "cost": c}
            for d, c in day_costs]


def _steady(bucket, cost, spike=None):
    days = [f"2026-07-{d:02d}" for d in range(1, 11)]
    costs = [cost] * 10
    if spike is not None:
        costs[-2] = spike  # latest CLOSED day (last day is treated as partial)
    return _daily(bucket, zip(days, costs, strict=True))


def test_steady_spend_not_flagged():
    assert classify_azure_spend(_steady("databricks", 100.0), 50, 10) == []


def test_spike_flagged():
    findings = classify_azure_spend(_steady("databricks", 100.0, spike=200.0), 50, 10)
    assert [f["action"] for f in findings] == ["investigate-spend-spike"]
    assert findings[0]["service_bucket"] == "databricks"


def test_spike_below_min_cost_not_flagged():
    findings = classify_azure_spend(_steady("storage", 0.05, spike=0.2), 50, 10)
    assert findings == []


def test_spike_at_threshold_boundary():
    # exactly +50% with threshold 50 counts (>=)
    findings = classify_azure_spend(_steady("search", 100.0, spike=150.0), 50, 10)
    assert len(findings) == 1


def test_too_little_history_returns_nothing():
    rows = _daily("databricks", [("2026-07-01", 100.0), ("2026-07-02", 900.0)])
    assert classify_azure_spend(rows, 50, 10) == []


def test_findings_ranked_by_cost():
    rows = (_steady("databricks", 10.0, spike=100.0)
            + _steady("foundry_ai", 100.0, spike=1000.0))
    findings = classify_azure_spend(rows, 50, 5)
    assert [f["service_bucket"] for f in findings] == ["foundry_ai", "databricks"]


def test_report_detail_sql_whitelists_dimension_and_bucket():
    from dbx_platform.azure_cost import report_detail_sql

    sql = report_detail_sql("main", "dbx_platform", "meter")
    assert "azure_cost_details" in sql
    assert "GROUP BY meter_name, service_bucket" in sql
    with pytest.raises(ValueError):
        report_detail_sql("main", "dbx_platform", "usage_date; DROP TABLE x")
    with pytest.raises(ValueError):
        report_detail_sql("main", "dbx_platform", "meter", bucket="foundry'; --")


def test_report_detail_sql_resource_dimension_carries_group_and_type():
    from dbx_platform.azure_cost import report_detail_sql

    sql = report_detail_sql("main", "dbx_platform", "resource")
    assert "resource_id, resource_group, resource_type" in sql


def test_report_detail_sql_binds_bucket_filter():
    from dbx_platform.azure_cost import report_detail_sql

    sql = report_detail_sql("main", "dbx_platform", "meter", bucket="foundry_ai")
    assert "service_bucket = :bucket" in sql
    assert "'foundry_ai'" not in sql
