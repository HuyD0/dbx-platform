import json
from datetime import date

import pytest

from dbx_platform.llm_cost import (
    azure_actual_cost,
    breakdown,
    coverage_record,
    create_ledger_table_statements,
    efficiency,
    evaluate_budgets,
    mask_identity,
    merge_cost_rows_sql,
    merge_source_health_sql,
    merge_usage_rows_sql,
    normalize_cost_rows,
    normalize_usage_rows,
    read_llm_cost_daily,
    read_llm_source_health,
    read_llm_usage_hourly,
    store_ledger,
    store_source_health,
    summarize,
    time_series,
    tokenomics_lens,
)


def _cost(**overrides):
    base = {
        "usage_date": "2026-07-01",
        "workspace_id": "w1",
        "provider": "anthropic",
        "model": "claude-opus",
        "endpoint": "chat",
        "principal": "person@example.com",
        "team": "platform",
        "use_case": "assistant",
        "cost": 12.5,
        "currency": "USD",
    }
    return {**base, **overrides}


def _usage(**overrides):
    base = {
        "usage_date": "2026-07-01",
        "workspace_id": "w1",
        "provider": "anthropic",
        "model": "claude-opus",
        "endpoint": "chat",
        "principal": "person@example.com",
        "team": "platform",
        "use_case": "assistant",
        "requests": 10,
        "invocations": 11,
        "input_tokens": 1000,
        "output_tokens": 200,
        "cached_tokens": 100,
        "reasoning_tokens": 50,
        "errors": 1,
        "retries": 1,
        "p95_latency_ms": 450,
    }
    return {**base, **overrides}


def test_normalize_cost_preserves_basis_and_currency():
    rows = normalize_cost_rows([_cost()], "billing", "DATABRICKS_LIST")
    assert rows[0]["cost_basis"] == "DATABRICKS_LIST"
    assert rows[0]["currency"] == "USD"
    assert rows[0]["source"] == "billing"


def test_normalize_cost_never_invents_usd_for_missing_currency():
    rows = normalize_cost_rows(
        [_cost(currency="")],
        "azure_costs",
        "AZURE_ACTUAL",
    )

    assert rows[0]["currency"] == "UNKNOWN"


def test_azure_actual_cost_labels_coarse_fallback_as_partial(monkeypatch):
    calls = 0

    def query(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("detail table unavailable")
        return [_cost()]

    monkeypatch.setattr("dbx_platform.llm_cost.run_query", query)

    result = azure_actual_cost(
        object(),
        "warehouse",
        "main",
        "dbx_platform",
        3,
        workspace_id="w1",
        environment="prod",
    )

    assert result.rows == [_cost()]
    assert result.status == "partial"
    assert "resource/meter/use-case attribution is unavailable" in result.notes


def test_normalize_cost_rejects_unknown_basis():
    with pytest.raises(ValueError, match="unsupported cost basis"):
        normalize_cost_rows([_cost()], "billing", "MIXED_TOTAL")


def test_normalize_usage_preserves_unavailable_preview_metrics():
    row = _usage()
    row.pop("cached_tokens")
    row.pop("reasoning_tokens")
    normalized = normalize_usage_rows([row], "legacy")[0]
    assert normalized["cached_tokens"] is None
    assert normalized["reasoning_tokens"] is None
    assert normalized["usage_hour"] == "2026-07-01T00:00:00+00:00"


def test_normalize_usage_does_not_invent_success_or_error_metrics():
    row = _usage()
    row.pop("errors")
    normalized = normalize_usage_rows([row], "legacy")[0]
    assert normalized["successful_requests"] is None
    assert normalized["errors"] is None


def test_normalization_forces_current_deployment_scope():
    cost = normalize_cost_rows(
        [_cost(workspace_id="stale", environment="old")],
        "billing",
        "DATABRICKS_LIST",
        workspace_id="w-current",
        environment="prod",
    )[0]
    usage = normalize_usage_rows(
        [_usage(workspace_id="stale", environment="old")],
        "usage",
        workspace_id="w-current",
        environment="prod",
    )[0]
    assert (cost["workspace_id"], cost["environment"]) == ("w-current", "prod")
    assert (usage["workspace_id"], usage["environment"]) == ("w-current", "prod")


def test_summary_does_not_mix_financial_bases():
    costs = normalize_cost_rows(
        [_cost(cost=10)], "billing", "DATABRICKS_LIST"
    ) + normalize_cost_rows([_cost(cost=8)], "azure", "AZURE_ACTUAL")
    usage = normalize_usage_rows([_usage(requests=9)], "usage")
    result = summarize(costs, usage, 30, today=date(2026, 7, 15))
    assert [(t["cost_basis"], t["cost"]) for t in result["totals"]] == [
        ("AZURE_ACTUAL", 8.0),
        ("DATABRICKS_LIST", 10.0),
    ]
    assert result["forecast"] is None
    assert len(result["forecasts"]) == 2
    assert result["cost_per_request"] is None


def test_summary_single_basis_calculates_unit_economics():
    costs = normalize_cost_rows([_cost(cost=12)], "billing", "DATABRICKS_LIST")
    usage = normalize_usage_rows([_usage(requests=4, input_tokens=800, output_tokens=200)], "usage")
    result = summarize(costs, usage, 30, today=date(2026, 7, 15))
    assert result["cost_per_request"] == 3.0
    assert result["cost_per_million_tokens"] == 12000.0
    assert result["forecast"]["method"] == "month-to-date run rate"


def test_summary_mtd_excludes_prior_month_cost_and_usage():
    costs = normalize_cost_rows(
        [
            _cost(usage_date="2026-06-30", cost=100),
            _cost(usage_date="2026-07-01", cost=12),
        ],
        "billing",
        "DATABRICKS_LIST",
    )
    usage = normalize_usage_rows(
        [
            _usage(usage_date="2026-06-30", requests=100),
            _usage(usage_date="2026-07-01", requests=4),
        ],
        "usage",
    )
    result = summarize(costs, usage, 30, today=date(2026, 7, 15))
    assert result["totals"][0]["cost"] == 12
    assert result["requests"] == 4
    assert result["period"]["from"] == "2026-07-01"
    assert result["forecast"]["month_end"] == 24.8


def test_summary_compares_same_elapsed_days_without_mixing_basis():
    costs = normalize_cost_rows(
        [
            _cost(usage_date="2026-06-01", cost=5),
            _cost(usage_date="2026-06-15", cost=5),
            _cost(usage_date="2026-06-30", cost=100),
            _cost(usage_date="2026-07-01", cost=12),
            _cost(usage_date="2026-07-15", cost=8),
        ],
        "billing",
        "DATABRICKS_LIST",
    )
    result = summarize(costs, [], 30, today=date(2026, 7, 15))
    total = result["totals"][0]
    assert total["cost"] == 20
    assert total["previous_period_cost"] == 10
    assert total["period_delta_pct"] == 100
    assert total["comparison_to"] == "2026-06-15"


def test_tokenomics_lens_keeps_unit_costs_separate_and_flags_context_tax():
    costs = normalize_cost_rows(
        [_cost(cost=20)],
        "system.billing.usage",
        "DATABRICKS_LIST",
    ) + normalize_cost_rows([_cost(cost=16)], "azure_cost_details", "AZURE_ACTUAL")
    usage = normalize_usage_rows(
        [
            _usage(
                requests=2,
                input_tokens=20_000,
                output_tokens=10_000,
                cached_tokens=500,
                reasoning_tokens=100,
            )
        ],
        "gateway",
    )

    result = tokenomics_lens(costs, usage)

    assert [
        (row["cost_basis"], row["cost_per_1m_total_tokens"]) for row in result["unit_costs"]
    ] == [
        ("AZURE_ACTUAL", 533.33),
        ("DATABRICKS_LIST", 666.67),
    ]
    assert result["scope"]["description"] == (
        "Workspace-level LLM ledger coverage, not only platform-console app traffic"
    )
    assert result["scope"]["cost_sources"] == ["azure_cost_details", "system.billing.usage"]
    assert result["scope"]["usage_sources"] == ["gateway"]
    assert result["metrics"]["avg_input_tokens_per_request"] == 10_000
    assert result["metrics"]["output_token_share"] == 0.3333
    assert result["recommendations"][0]["type"] == "context-window-tax"


def test_time_series_avoids_false_usage_allocation():
    costs = normalize_cost_rows(
        [_cost(cost=10)], "billing", "DATABRICKS_LIST"
    ) + normalize_cost_rows([_cost(cost=8)], "azure", "AZURE_ACTUAL")
    usage = normalize_usage_rows([_usage()], "usage")
    rows = time_series(costs, usage)
    usage_only = [row for row in rows if row["cost_basis"] == "USAGE_ONLY"]
    assert len(usage_only) == 1
    assert usage_only[0]["requests"] == 10
    assert all(row["requests"] == 0 for row in rows if row["cost_basis"] != "USAGE_ONLY")


def test_breakdown_masks_requester_identity():
    costs = normalize_cost_rows([_cost()], "billing", "DATABRICKS_LIST")
    usage = normalize_usage_rows([_usage()], "usage")
    rows = breakdown(costs, usage, "principal")
    assert rows[0]["key"].startswith("user-")
    assert "example.com" not in rows[0]["key"]


def test_breakdown_dimension_is_allowlisted():
    with pytest.raises(ValueError, match="dimension must be"):
        breakdown([], [], "cost; DROP TABLE x")


def test_efficiency_finds_retry_cache_and_attribution_issues():
    costs = normalize_cost_rows(
        [_cost(endpoint="unallocated", cost=20)], "billing", "DATABRICKS_LIST"
    )
    usage = normalize_usage_rows(
        [
            _usage(
                requests=100,
                invocations=100,
                retries=15,
                input_tokens=200_000,
                cached_tokens=1_000,
            )
        ],
        "usage",
    )
    result = efficiency(costs, usage)
    assert result["retry_rate"] == 0.15
    assert {r["type"] for r in result["recommendations"]} == {
        "retry-storm",
        "low-cache-use",
        "unallocated-spend",
    }
    assert all(r["requires_approval"] for r in result["recommendations"])


def test_mask_identity_is_stable_and_handles_unallocated():
    assert mask_identity("person@example.com") == mask_identity("person@example.com")
    assert mask_identity("person@example.com").startswith("user-")
    assert mask_identity("service-principal-id").startswith("principal-")
    assert mask_identity("unallocated") == "unallocated"


def test_coverage_record_keeps_financial_basis_explicit():
    record = coverage_record(
        "Azure",
        "available",
        freshness="daily",
        retention_days=400,
        cost_basis="AZURE_ACTUAL",
    )
    assert record["cost_basis"] == "AZURE_ACTUAL"
    assert record["retention_days"] == 400


def test_ledger_ddl_creates_cost_usage_budget_and_source_health_tables():
    statements = create_ledger_table_statements("main", "dbx_platform")
    sql = "\n".join(text for _, text in statements)
    assert "main.dbx_platform.llm_cost_daily" in sql
    assert "main.dbx_platform.llm_usage_hourly" in sql
    assert "main.dbx_platform.llm_budgets" in sql
    assert "main.dbx_platform.llm_source_health" in sql
    assert "cost_basis STRING" in sql
    assert "warning_pct INT" in sql
    assert " DEFAULT " not in sql
    assert "coverage_start DATE" in sql
    assert "last_success_at TIMESTAMP" in sql


def test_persisted_ledger_reads_are_exactly_workspace_scoped(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dbx_platform.llm_cost.run_query",
        lambda _w, sql, _warehouse, params=None, **kwargs: (
            calls.append((sql, params, kwargs)) or []
        ),
    )

    read_llm_cost_daily(object(), "warehouse", "main", "dbx_platform", "w1", "prod", 400)
    read_llm_usage_hourly(object(), "warehouse", "main", "dbx_platform", "w1", "prod", 400)
    read_llm_source_health(object(), "warehouse", "main", "dbx_platform", "w1", "prod")

    assert len(calls) == 3
    assert all("workspace_id = :workspace_id" in sql for sql, _, _ in calls)
    assert all("environment = :environment" in sql for sql, _, _ in calls)
    assert all(params["workspace_id"] == "w1" for _, params, _ in calls)
    assert all(params["environment"] == "prod" for _, params, _ in calls)
    assert "llm_cost_daily" in calls[0][0]
    assert "llm_usage_hourly" in calls[1][0]
    assert "llm_source_health" in calls[2][0]
    assert calls[1][1]["lookback_days"] == 89


def test_source_health_persists_zero_rows_and_forces_current_scope(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dbx_platform.llm_cost.run_query",
        lambda _w, sql, _warehouse, params=None, **_kwargs: calls.append((sql, params)) or [],
    )
    count = store_source_health(
        object(),
        "warehouse",
        "main",
        "dbx_platform",
        [
            {
                "workspace_id": "wrong",
                "environment": "wrong",
                "source_key": "gateway-usage",
                "source": "Model request usage",
                "source_type": "usage",
                "status": "available",
                "freshness": "hourly",
                "retention_days": 90,
                "coverage_start": "2026-07-15",
                "coverage_end": "2026-07-17",
                "row_count": 0,
                "available_metrics": ["requests"],
            }
        ],
        workspace_id="w1",
        environment="prod",
    )

    assert count == 1
    assert len(calls) == 1
    rows = json.loads(calls[0][1]["rows"])
    assert rows[0]["workspace_id"] == "w1"
    assert rows[0]["environment"] == "prod"
    assert rows[0]["row_count"] == 0
    assert json.loads(rows[0]["available_metrics_json"]) == ["requests"]


def test_source_health_merge_preserves_last_success_when_source_is_unavailable():
    sql = merge_source_health_sql("main", "dbx_platform")
    assert "t.workspace_id = s.workspace_id" in sql
    assert "t.environment = s.environment" in sql
    assert "t.source_key = s.source_key" in sql
    assert "ELSE t.last_success_at" in sql


def test_ledger_merges_are_idempotent_on_attribution_dimensions():
    cost_sql = merge_cost_rows_sql("main", "dbx_platform")
    usage_sql = merge_usage_rows_sql("main", "dbx_platform")
    assert "t.cost_basis = s.cost_basis" in cost_sql
    assert "t.source = s.source" in cost_sql
    assert "t.usage_hour = s.usage_hour" in usage_sql
    assert "WHEN MATCHED THEN UPDATE" in cost_sql
    assert "WHEN MATCHED THEN UPDATE" in usage_sql
    assert "WHEN NOT MATCHED BY SOURCE" in cost_sql
    assert "t.workspace_id = :workspace_id" in cost_sql
    assert "t.environment = :environment" in cost_sql
    assert "t.source = :source" in cost_sql
    assert "t.cost_basis = :cost_basis" in cost_sql
    assert "t.usage_date BETWEEN CAST(:window_start AS DATE)" in cost_sql
    assert "WHEN NOT MATCHED BY SOURCE" in usage_sql
    assert "t.usage_hour < DATE_ADD(CAST(:window_end AS DATE), 1)" in usage_sql


def test_store_ledger_reconciles_declared_scopes_in_atomic_merges(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dbx_platform.llm_cost.run_query",
        lambda _w, sql, _warehouse, params=None, **_kwargs: calls.append((sql, params)) or [],
    )
    cost_rows = normalize_cost_rows(
        [_cost(usage_date="2026-07-16")],
        "system.billing.usage",
        "DATABRICKS_LIST",
        environment="prod",
        workspace_id="w1",
    )
    usage_rows = normalize_usage_rows(
        [_usage(usage_date="2026-07-16")],
        "system.ai_gateway.usage",
        environment="prod",
        workspace_id="w1",
    )

    result = store_ledger(
        object(),
        "warehouse",
        "main",
        "dbx_platform",
        cost_rows,
        usage_rows,
        window_start="2026-07-14",
        window_end="2026-07-17",
        cost_scopes=[
            {
                "workspace_id": "w1",
                "environment": "prod",
                "source": "system.billing.usage",
                "cost_basis": "DATABRICKS_LIST",
            }
        ],
        usage_scopes=[
            {
                "workspace_id": "w1",
                "environment": "prod",
                "source": "system.ai_gateway.usage",
            }
        ],
    )

    assert result == {"cost_rows": 1, "usage_rows": 1}
    assert len(calls) == 4  # two retention DML statements + two scoped MERGEs
    merge_calls = [(sql, params) for sql, params in calls if sql.startswith("MERGE")]
    assert len(merge_calls) == 2
    assert all("CREATE TABLE" not in sql for sql, _ in calls)
    assert all(params["workspace_id"] == "w1" for _, params in merge_calls)
    assert all(params["environment"] == "prod" for _, params in merge_calls)


def test_store_ledger_empty_successful_scope_removes_withdrawn_rows(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dbx_platform.llm_cost.run_query",
        lambda _w, sql, _warehouse, params=None, **_kwargs: calls.append((sql, params)) or [],
    )

    store_ledger(
        object(),
        "warehouse",
        "main",
        "dbx_platform",
        [],
        [],
        window_start="2026-07-14",
        window_end="2026-07-17",
        cost_scopes=[
            {
                "workspace_id": "w1",
                "environment": "prod",
                "source": "system.ai_gateway.external_model_spend",
                "cost_basis": "PROVIDER_ESTIMATE",
            }
        ],
        usage_scopes=[],
    )

    merge_calls = [(sql, params) for sql, params in calls if sql.startswith("MERGE")]
    assert len(merge_calls) == 1
    sql, params = merge_calls[0]
    assert "WHEN NOT MATCHED BY SOURCE" in sql
    assert params["rows"] == "[]"
    assert params["source"] == "system.ai_gateway.external_model_spend"
    assert params["cost_basis"] == "PROVIDER_ESTIMATE"


def test_store_ledger_does_not_reconcile_unavailable_undeclared_source(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "dbx_platform.llm_cost.run_query",
        lambda _w, sql, _warehouse, params=None, **_kwargs: calls.append((sql, params)) or [],
    )

    store_ledger(
        object(),
        "warehouse",
        "main",
        "dbx_platform",
        [],
        [],
        window_start="2026-07-14",
        window_end="2026-07-17",
        cost_scopes=[],
        usage_scopes=[],
    )

    assert not [sql for sql, _ in calls if sql.startswith("MERGE")]


def test_store_ledger_rejects_undeclared_workspace_without_writing(monkeypatch):
    monkeypatch.setattr(
        "dbx_platform.llm_cost.run_query",
        lambda *_args, **_kwargs: pytest.fail("invalid scope must not write"),
    )
    rows = normalize_cost_rows(
        [_cost(usage_date="2026-07-16")],
        "system.billing.usage",
        "DATABRICKS_LIST",
        environment="prod",
        workspace_id="w2",
    )
    with pytest.raises(ValueError, match="undeclared reconciliation scopes"):
        store_ledger(
            object(),
            "warehouse",
            "main",
            "dbx_platform",
            rows,
            [],
            window_start="2026-07-14",
            window_end="2026-07-17",
            cost_scopes=[
                {
                    "workspace_id": "w1",
                    "environment": "prod",
                    "source": "system.billing.usage",
                    "cost_basis": "DATABRICKS_LIST",
                }
            ],
            usage_scopes=[],
        )


def test_store_ledger_storage_failure_has_migration_guidance(monkeypatch):
    monkeypatch.setattr(
        "dbx_platform.llm_cost.run_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(Exception("TABLE_NOT_FOUND")),
    )
    with pytest.raises(RuntimeError, match="schema_migrations"):
        store_ledger(
            object(),
            "warehouse",
            "main",
            "dbx_platform",
            [],
            [],
            window_start="2026-07-14",
            window_end="2026-07-17",
            cost_scopes=[],
            usage_scopes=[],
        )


def test_budgets_match_scope_currency_and_financial_basis():
    costs = normalize_cost_rows(
        [_cost(provider="anthropic", cost=90)], "billing", "DATABRICKS_LIST"
    ) + normalize_cost_rows([_cost(provider="anthropic", cost=70)], "azure", "AZURE_ACTUAL")
    budgets = [
        {
            "budget_id": "b1",
            "scope_type": "provider",
            "scope_value": "anthropic",
            "cost_basis": "DATABRICKS_LIST",
            "currency": "USD",
            "amount": 100,
            "warning_pct": 80,
            "critical_pct": 100,
        }
    ]
    result = evaluate_budgets(budgets, costs)[0]
    assert result["spend"] == 90
    assert result["consumed_pct"] == 90
    assert result["threshold_state"] == "WARNING"


def test_budget_evaluation_excludes_other_calendar_months():
    costs = normalize_cost_rows(
        [
            _cost(usage_date="2026-06-30", cost=75),
            _cost(usage_date="2026-07-01", cost=25),
        ],
        "billing",
        "DATABRICKS_LIST",
    )
    budgets = [
        {
            "budget_id": "b1",
            "scope_type": "provider",
            "scope_value": "anthropic",
            "cost_basis": "DATABRICKS_LIST",
            "month": "2026-07-01",
            "currency": "USD",
            "amount": 100,
            "warning_pct": 80,
            "critical_pct": 100,
        }
    ]
    result = evaluate_budgets(
        budgets,
        costs,
        today=date(2026, 7, 17),
    )[0]
    assert result["spend"] == 25
    assert result["consumed_pct"] == 25
