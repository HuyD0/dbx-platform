from dbx_platform.azure_cost import (
    build_query_body,
    classify_azure_spend,
    create_table_sql,
    merge_costs_sql,
    parse_query_result,
    report_sql,
    service_bucket,
)

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
    cols = cols or ["Cost", "UsageDate", "ServiceName", "ResourceGroupName", "Currency"]
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


# --- SQL builders ---------------------------------------------------------------

def test_query_body_shape():
    body = build_query_body("2026-07-01", "2026-07-03")
    assert body["dataset"]["granularity"] == "Daily"
    names = [g["name"] for g in body["dataset"]["grouping"]]
    assert names == ["ServiceName", "ResourceGroupName"]


def test_merge_sql_targets_table_and_binds_rows_param():
    sql = merge_costs_sql("main", "dbx_platform")
    assert "MERGE INTO main.dbx_platform.azure_costs" in sql
    assert ":rows" in sql
    assert "t.usage_date = s.usage_date" in sql


def test_create_table_sql_has_bucket_column():
    assert "service_bucket STRING" in create_table_sql("main", "dbx_platform")


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
