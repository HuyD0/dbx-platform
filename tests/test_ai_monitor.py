"""Offline tests for production AI app monitoring (pure logic only)."""

import json

import pytest
from conftest import days_ago

from dbx_platform.ai_monitor import (
    ENDPOINT_USAGE_SOURCE,
    GATEWAY_USAGE_SOURCE,
    classify_ai_monitoring,
    merge_monitoring_sql,
    retention_delete_sql,
    store_monitoring,
)
from dbx_platform.system_tables import load_query

THRESHOLDS = {
    "spike_pct": 100,
    "min_requests": 50,
    "min_error_rate_pct": 5,
    "stale_days": 30,
}


def _day(day: str, requests: int, errors: int, *, endpoint="churn-api",
         app="checkout", source=ENDPOINT_USAGE_SOURCE) -> dict:
    return {
        "usage_date": day,
        "endpoint_name": endpoint,
        "app": app,
        "entity_name": "prod.ml.churn",
        "entity_type": "CUSTOM_MODEL",
        "requests": requests,
        "errors": errors,
        "server_errors": 0,
        "input_tokens": 1000,
        "output_tokens": 500,
        "distinct_requesters": 3,
        "p95_latency_ms": None,
        "source": source,
    }


def _endpoint(name="churn-api", **overrides) -> dict:
    row = {
        "name": name,
        "creator": "ml@example.com",
        "is_system_endpoint": False,
        "created_ms": 0,
        "served_entities": [],
    }
    row.update(overrides)
    return row


# --- error-rate spike -------------------------------------------------------------

def test_steady_error_rate_not_flagged(now_ms):
    rows = [_day(f"2026-07-{d:02d}", 100, 6) for d in range(10, 18)]
    findings = classify_ai_monitoring(rows, [], [], now_ms, **THRESHOLDS)
    assert findings["ai-monitor/error-rate-spike"] == []


def test_spike_at_exact_threshold_flagged(now_ms):
    # Trailing avg 5%; latest closed day 10% = exactly (1 + 100%) x baseline.
    rows = [_day(f"2026-07-{d:02d}", 100, 5) for d in range(10, 16)]
    rows.append(_day("2026-07-16", 100, 10))
    rows.append(_day("2026-07-17", 10, 0))  # newest day: partial, ignored
    findings = classify_ai_monitoring(rows, [], [], now_ms, **THRESHOLDS)
    (spike,) = findings["ai-monitor/error-rate-spike"]
    assert spike["day"] == "2026-07-16"
    assert spike["error_rate"] == 0.1
    assert spike["severity"] == "HIGH"
    assert spike["name"] == "churn-api/checkout"


def test_partial_newest_day_never_flagged(now_ms):
    rows = [_day(f"2026-07-{d:02d}", 100, 5) for d in range(10, 17)]
    rows.append(_day("2026-07-17", 100, 90))  # disastrous but still partial
    findings = classify_ai_monitoring(rows, [], [], now_ms, **THRESHOLDS)
    assert findings["ai-monitor/error-rate-spike"] == []


def test_low_volume_below_min_requests_not_flagged(now_ms):
    rows = [_day(f"2026-07-{d:02d}", 20, 10) for d in range(10, 18)]
    findings = classify_ai_monitoring(rows, [], [], now_ms, **THRESHOLDS)
    assert findings["ai-monitor/error-rate-spike"] == []


def test_zero_baseline_new_errors_flagged(now_ms):
    rows = [_day(f"2026-07-{d:02d}", 100, 0) for d in range(10, 16)]
    rows.append(_day("2026-07-16", 100, 8))
    rows.append(_day("2026-07-17", 10, 0))
    findings = classify_ai_monitoring(rows, [], [], now_ms, **THRESHOLDS)
    assert len(findings["ai-monitor/error-rate-spike"]) == 1


def test_gateway_rows_do_not_double_count_spikes(now_ms):
    rows = [_day(f"2026-07-{d:02d}", 100, 5) for d in range(10, 18)]
    rows += [
        _day(f"2026-07-{d:02d}", 100, 90, source=GATEWAY_USAGE_SOURCE)
        for d in range(10, 18)
    ]
    findings = classify_ai_monitoring(rows, [], [], now_ms, **THRESHOLDS)
    assert findings["ai-monitor/error-rate-spike"] == []


# --- usage-tracking gap ------------------------------------------------------------

def test_billed_endpoint_without_usage_rows_flagged(now_ms):
    cost_rows = [{"endpoint_name": "silent-api", "list_cost_usd": 42.5}]
    findings = classify_ai_monitoring(
        [], [_endpoint("silent-api")], cost_rows, now_ms, **THRESHOLDS
    )
    (gap,) = findings["ai-monitor/usage-tracking-gap"]
    assert gap["name"] == "silent-api"
    assert gap["list_cost_usd"] == 42.5
    assert gap["severity"] == "MEDIUM"


def test_tracked_and_system_endpoints_not_gap_flagged(now_ms):
    rows = [_day("2026-07-16", 10, 0, endpoint="tracked-api")]
    cost_rows = [
        {"endpoint_name": "tracked-api", "list_cost_usd": 10.0},
        {"endpoint_name": "databricks-claude", "list_cost_usd": 99.0},
        {"endpoint_name": "", "list_cost_usd": 5.0},
        {"endpoint_name": "unknown-endpoint", "list_cost_usd": 5.0},
    ]
    endpoints = [
        _endpoint("tracked-api"),
        _endpoint("databricks-claude", is_system_endpoint=True),
    ]
    findings = classify_ai_monitoring(rows, endpoints, cost_rows, now_ms, **THRESHOLDS)
    assert findings["ai-monitor/usage-tracking-gap"] == []


def test_no_cost_rows_keeps_gap_check_silent(now_ms):
    findings = classify_ai_monitoring(
        [], [_endpoint("silent-api")], [], now_ms, **THRESHOLDS
    )
    assert findings["ai-monitor/usage-tracking-gap"] == []


# --- idle endpoints ----------------------------------------------------------------

def test_idle_endpoint_delegation_respects_grace(now_ms):
    rows = [_day("2026-07-16", 10, 0, endpoint="busy-api")]
    old = _endpoint("old-idle", created_ms=days_ago(90))
    young = _endpoint("young-idle", created_ms=days_ago(3))
    findings = classify_ai_monitoring(rows, [old, young], [], now_ms, **THRESHOLDS)
    idle = findings["ai-monitor/idle-endpoint"]
    assert [f["name"] for f in idle] == ["old-idle"]
    assert idle[0]["severity"] == "LOW"
    assert idle[0]["resource_type"] == "SERVING_ENDPOINT"


def test_idle_never_assessed_without_usage_telemetry(now_ms):
    old = _endpoint("old-idle", created_ms=days_ago(90))
    findings = classify_ai_monitoring([], [old], [], now_ms, **THRESHOLDS)
    assert findings["ai-monitor/idle-endpoint"] == []


def test_gap_endpoint_not_double_flagged_as_idle(now_ms):
    rows = [_day("2026-07-16", 10, 0, endpoint="busy-api")]
    silent = _endpoint("silent-api", created_ms=days_ago(90))
    cost_rows = [{"endpoint_name": "silent-api", "list_cost_usd": 42.5}]
    findings = classify_ai_monitoring(rows, [silent], cost_rows, now_ms, **THRESHOLDS)
    assert [f["name"] for f in findings["ai-monitor/usage-tracking-gap"]] == ["silent-api"]
    assert findings["ai-monitor/idle-endpoint"] == []


# --- queries ------------------------------------------------------------------------

def test_packaged_queries_target_expected_tables():
    endpoint_sql = load_query("ai_endpoint_usage_daily")
    assert "system.serving.endpoint_usage" in endpoint_sql
    assert "system.serving.served_entities" in endpoint_sql
    assert ":days" in endpoint_sql
    gateway_sql = load_query("ai_gateway_usage_daily")
    assert "system.ai_gateway.usage" in gateway_sql
    assert "p95_latency_ms" in gateway_sql


# --- merge/store -------------------------------------------------------------------

def test_merge_sql_windowed_delete_scoped_by_source():
    sql = merge_monitoring_sql("main", "dbx_platform")
    assert "ai_app_monitoring" in sql
    assert "CREATE TABLE" not in sql
    assert "WHEN NOT MATCHED BY SOURCE" in sql
    assert "t.source = :source" in sql
    assert "BETWEEN CAST(:window_start AS DATE)" in sql
    assert sql.rstrip().endswith("THEN DELETE")
    assert "usage_date" in retention_delete_sql("main", "dbx_platform")


def test_store_merges_per_source_after_one_retention_delete(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dbx_platform.ai_monitor.run_query",
        lambda _w, sql, _warehouse, params=None, **_kwargs: calls.append((sql, params))
        or [],
    )
    rows = [
        _day("2026-07-16", 100, 1),
        _day("2026-07-16", 50, 0, source=GATEWAY_USAGE_SOURCE),
    ]
    assert store_monitoring(
        object(),
        "warehouse",
        "main",
        "dbx_platform",
        rows,
        workspace_id="w1",
        environment="prod",
        window_start="2026-07-10",
        window_end="2026-07-17",
        sources=[ENDPOINT_USAGE_SOURCE, GATEWAY_USAGE_SOURCE],
    ) == 2
    assert len(calls) == 3
    assert "DELETE FROM" in calls[0][0]
    merged_sources = {params["source"] for _sql, params in calls[1:]}
    assert merged_sources == {ENDPOINT_USAGE_SOURCE, GATEWAY_USAGE_SOURCE}
    for _sql, params in calls[1:]:
        scoped = json.loads(params["rows"])
        assert all(row["source"] == params["source"] for row in scoped)


def test_store_rejects_rows_outside_window(monkeypatch):
    monkeypatch.setattr(
        "dbx_platform.ai_monitor.run_query",
        lambda *_args, **_kwargs: pytest.fail("invalid input must not write"),
    )
    with pytest.raises(ValueError, match="outside the reconciliation window"):
        store_monitoring(
            object(),
            "warehouse",
            "main",
            "dbx_platform",
            [_day("2026-07-01", 10, 0)],
            workspace_id="w1",
            environment="prod",
            window_start="2026-07-10",
            window_end="2026-07-17",
            sources=[ENDPOINT_USAGE_SOURCE],
        )


def test_store_rejects_undeclared_source(monkeypatch):
    monkeypatch.setattr(
        "dbx_platform.ai_monitor.run_query",
        lambda *_args, **_kwargs: pytest.fail("invalid input must not write"),
    )
    with pytest.raises(ValueError, match="not declared as refreshed"):
        store_monitoring(
            object(),
            "warehouse",
            "main",
            "dbx_platform",
            [_day("2026-07-16", 10, 0, source=GATEWAY_USAGE_SOURCE)],
            workspace_id="w1",
            environment="prod",
            window_start="2026-07-10",
            window_end="2026-07-17",
            sources=[ENDPOINT_USAGE_SOURCE],
        )


def test_store_failure_has_migration_guidance(monkeypatch):
    monkeypatch.setattr(
        "dbx_platform.ai_monitor.run_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(Exception("TABLE_NOT_FOUND")),
    )
    with pytest.raises(RuntimeError, match="schema_migrations"):
        store_monitoring(
            object(),
            "warehouse",
            "main",
            "dbx_platform",
            [],
            workspace_id="w1",
            environment="prod",
            window_start="2026-07-10",
            window_end="2026-07-17",
            sources=[ENDPOINT_USAGE_SOURCE],
        )
