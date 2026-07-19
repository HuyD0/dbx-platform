"""Pure-logic tests for estimator drift monitoring (no network)."""

from __future__ import annotations

import json

from dbx_platform.estimator import (
    Requirements,
    build_price_book,
    compute_matrix,
    load_rate_card,
)
from dbx_platform.estimator_monitor import (
    REPRICING_CHECK,
    classify_repricing_drift,
    fetch_saved_estimates,
)

_BASE_PRICES = {
    "aoai.gpt-4o-mini.input": 0.15,
    "aoai.gpt-4o-mini.output": 0.60,
    "aoai.gpt-4o.input": 2.50,
    "aoai.gpt-4o.output": 10.00,
    "aoai.embedding-3-small.input": 0.02,
    "search.basic.unit": 0.11,
    "search.s1.unit": 0.34,
    "vm.d8s_v5": 0.384,
    "aks.standard_uptime": 0.10,
    "storage.hot_gb_month": 0.021,
    "postgres.flex.burstable": 0.017,
    "postgres.flex.general": 0.15,
    "dbx.model_serving.dbu": 0.07,
    "dbx.fmapi.dbu": 0.07,
    "dbx.vector_search.dbu": 0.07,
    "dbx.jobs_serverless.dbu": 0.35,
    "dbx.lakebase.dbu": 0.07,
}

DOC_CHAT = Requirements(pattern="doc_chat", monthly_requests=100_000, corpus_gb=10.0)


def _book(snapshot_date: str, *, multiplier: float = 1.0, drop: set[str] | None = None):
    rows = [
        {"snapshot_date": snapshot_date, "source": "azure_retail", "rate_key": key,
         "meter_name": f"meter:{key}", "unit_price": value * multiplier, "currency": "USD"}
        for key, value in _BASE_PRICES.items()
        if key not in (drop or set())
    ]
    return build_price_book(rows, load_rate_card())


def _saved(book, *, snapshot_date: str, rigor_pct: int = 10, title: str = "Doc chat"):
    matrix = compute_matrix(DOC_CHAT, rigor_pct=rigor_pct, price_book=book)
    return {
        "estimate_id": f"est-{snapshot_date}",
        "title": title,
        "requirements_json": json.dumps({"pattern": "doc_chat", "monthly_requests": 100000,
                                         "corpus_gb": 10.0}),
        "snapshot_date": snapshot_date,
        "rate_card_version": matrix["rate_card_version"],
        "rigor_pct": rigor_pct,
        "results_json": json.dumps(matrix),
    }


def test_repricing_drift_flags_material_price_moves():
    old_book = _book("2026-05-01")
    estimate = _saved(old_book, snapshot_date="2026-05-01")
    # prices doubled since the estimate was saved
    current = _book("2026-07-14", multiplier=2.0)
    findings = classify_repricing_drift([estimate], current)
    assert len(findings) == 1
    f = findings[0]
    assert f["estimate_id"] == "est-2026-05-01"
    assert f["action"] == "re-estimate"
    assert f["cost_usd"] > 0
    assert f["old_snapshot"] == "2026-05-01" and f["current_snapshot"] == "2026-07-14"
    assert "at current prices" in f["reason"]


def test_repricing_drift_ignores_small_moves_and_same_snapshot():
    old_book = _book("2026-05-01")
    estimate = _saved(old_book, snapshot_date="2026-05-01")
    # +5% is below the 15% threshold
    assert classify_repricing_drift([estimate], _book("2026-07-14", multiplier=1.05)) == []
    # estimate priced at the current snapshot cannot have moved
    same = _saved(_book("2026-07-14"), snapshot_date="2026-07-14")
    assert classify_repricing_drift([same], _book("2026-07-14", multiplier=3.0)) == []


def test_repricing_drift_skips_scenarios_with_unpriced_components():
    old_book = _book("2026-05-01")
    estimate = _saved(old_book, snapshot_date="2026-05-01")
    # current snapshot lost the Databricks serving price entirely; that scenario
    # must be skipped, not reported as a huge negative drift.
    current = _book("2026-07-14", multiplier=2.0, drop={"dbx.model_serving.dbu"})
    findings = classify_repricing_drift([estimate], current)
    # azure scenario still priced and doubled -> exactly one finding, azure
    assert len(findings) == 1
    assert findings[0]["scenario"] == "azure"


def test_repricing_drift_reports_largest_move_and_sorts_by_dollars():
    old_book = _book("2026-05-01")
    small = _saved(old_book, snapshot_date="2026-05-01", title="Small")
    small["estimate_id"] = "small"
    small["requirements_json"] = json.dumps({"pattern": "doc_chat", "monthly_requests": 1000})
    small["results_json"] = json.dumps(
        compute_matrix(Requirements(pattern="doc_chat", monthly_requests=1000),
                       rigor_pct=10, price_book=old_book)
    )
    big = _saved(old_book, snapshot_date="2026-05-01", title="Big")
    big["estimate_id"] = "big"
    current = _book("2026-07-14", multiplier=2.0)
    findings = classify_repricing_drift([small, big], current)
    assert [f["estimate_id"] for f in findings] == ["big", "small"]  # sorted by cost_usd desc


def test_repricing_drift_skips_unparseable_rows():
    junk = {"estimate_id": "junk", "snapshot_date": "2026-05-01",
            "requirements_json": "not json", "results_json": "{}", "rigor_pct": 10}
    assert classify_repricing_drift([junk], _book("2026-07-14", multiplier=2.0)) == []


def test_fetch_saved_estimates_sql_is_scoped():
    captured = {}

    class FakeClient:
        pass

    import dbx_platform.estimator_monitor as mod

    original = mod.run_query
    try:
        mod.run_query = lambda w, sql, wh, params: captured.update(sql=sql, params=params) or []
        fetch_saved_estimates(FakeClient(), "wh", "cat", "sch",
                              workspace_id="ws-1", environment="prod", limit=50)
    finally:
        mod.run_query = original
    assert "cat.sch.estimator_estimates" in captured["sql"]
    assert "workspace_id = :workspace_id" in captured["sql"]
    assert captured["params"] == {"workspace_id": "ws-1", "environment": "prod", "limit": 50}


def test_repricing_check_key_is_cost_area():
    assert REPRICING_CHECK == "cost/estimate-repricing-drift"
    assert REPRICING_CHECK.startswith("cost/")


# --- actuals drift ------------------------------------------------------------

from dbx_platform.estimator_monitor import (  # noqa: E402
    ACTUALS_CHECK,
    classify_actuals_drift,
)


def _deployment(anchor_kind="azure_resource_group", anchor_value="rg-doc-chat",
                projected=1000.0, tier="production", scenario="azure",
                deployment_id="dep-1", estimate_id="est-1"):
    return {
        "deployment_id": deployment_id, "estimate_id": estimate_id,
        "tier": tier, "scenario": scenario, "anchor_kind": anchor_kind,
        "anchor_value": anchor_value, "monthly_projected_usd": projected,
        "currency": "USD",
    }


def test_actuals_drift_flags_material_over_and_under_spend():
    dep = _deployment(projected=1000.0)
    over = classify_actuals_drift([dep], {("azure_resource_group", "rg-doc-chat"): 1500.0})
    assert len(over) == 1
    assert over[0]["action"] == "review-estimate-vs-actual"
    assert over[0]["cost_usd"] == 500.0
    assert over[0]["resource"] == "rg-doc-chat"
    assert "Azure actual bill" in over[0]["reason"]
    assert "non-AI resources" in over[0]["reason"]  # honest caveat
    under = classify_actuals_drift([dep], {("azure_resource_group", "rg-doc-chat"): 500.0})
    assert under[0]["cost_usd"] == 500.0


def test_actuals_drift_ignores_within_threshold_and_skips_missing_actuals():
    dep = _deployment(projected=1000.0)
    assert classify_actuals_drift([dep], {("azure_resource_group", "rg-doc-chat"): 1100.0}) == []
    # anchor has no actuals yet (too new) -> skipped, not flagged
    assert classify_actuals_drift([dep], {}) == []


def test_actuals_drift_databricks_tag_basis_label():
    dep = _deployment(anchor_kind="databricks_project_tag", anchor_value="doc-chat",
                      scenario="databricks", projected=1000.0)
    findings = classify_actuals_drift([dep], {("databricks_project_tag", "doc-chat"): 2000.0})
    assert findings[0]["anchor_kind"] == "databricks_project_tag"
    assert "Databricks list cost" in findings[0]["reason"]


def test_actuals_check_key_is_cost_area():
    assert ACTUALS_CHECK == "cost/estimate-drift"


def test_fetch_active_deployments_sql_takes_latest_active_per_estimate():
    captured = {}
    import dbx_platform.estimator_monitor as mod

    original = mod.run_query
    try:
        mod.run_query = lambda w, sql, wh, params: captured.update(sql=sql) or []
        mod.fetch_active_deployments(object(), "wh", "cat", "sch",
                                     workspace_id="ws", environment="prod")
    finally:
        mod.run_query = original
    assert "estimator_deployments" in captured["sql"]
    assert "ROW_NUMBER() OVER" in captured["sql"]
    assert "rn = 1 AND active = true" in captured["sql"]


def test_fetch_anchor_actuals_routes_azure_and_tag_anchors():
    import dbx_platform.estimator_monitor as mod
    from dbx_platform import cost

    deployments = [
        _deployment(anchor_kind="azure_resource_group", anchor_value="rg-1"),
        _deployment(anchor_kind="databricks_team_tag", anchor_value="ml-team",
                    deployment_id="dep-2", estimate_id="est-2"),
    ]
    calls = []

    def fake_run_query(w, sql, wh, params):
        calls.append(("sql", params))
        return [{"actual": 42.0}]

    def fake_attribution(w, wh, dimension, days):
        calls.append(("attr", dimension))
        return [{"x_team": "ml-team", "list_cost": 99.0}]

    original_rq, original_attr = mod.run_query, cost.attribution
    try:
        mod.run_query = fake_run_query
        cost.attribution = fake_attribution
        actuals = mod.fetch_anchor_actuals(object(), "wh", "cat", "sch", deployments, days=30)
    finally:
        mod.run_query = original_rq
        cost.attribution = original_attr
    assert actuals[("azure_resource_group", "rg-1")] == 42.0
    assert actuals[("databricks_team_tag", "ml-team")] == 99.0
    assert ("attr", "team") in calls


def test_deployments_ddl_is_append_only():
    from dbx_platform.estimator import create_deployments_table_sql

    ddl = create_deployments_table_sql("cat", "sch")
    assert "cat.sch.estimator_deployments" in ddl
    assert "'delta.appendOnly' = 'true'" in ddl
    for column in ("anchor_kind STRING", "monthly_projected_usd DOUBLE", "active BOOLEAN"):
        assert column in ddl


# --- CLI drift-check ----------------------------------------------------------


def test_cli_drift_check_is_wired_with_thresholds():
    from dbx_platform import cli

    parser = cli.build_parser()
    args = parser.parse_args(
        ["estimator", "drift-check", "--days", "14",
         "--reprice-threshold", "20", "--actuals-threshold", "40"]
    )
    assert args.func is cli.cmd_estimator_drift_check
    assert args.days == 14
    assert args.reprice_threshold == 20.0
    assert args.actuals_threshold == 40.0
    assert args.no_store is False


def test_cli_drift_check_refuses_without_governed_context(monkeypatch):
    from unittest.mock import MagicMock

    from dbx_platform import approved_job, cli

    monkeypatch.setattr(cli, "get_client", lambda profile: MagicMock())

    def _reject(*args, **kwargs):
        raise approved_job.ApprovalGateError("no verified executor context")

    monkeypatch.setattr(approved_job, "verify_governed_write_launch", _reject)
    parser = cli.build_parser()
    args = parser.parse_args(["estimator", "drift-check"])
    assert args.func(args) == 2


def test_cli_drift_check_returns_nonzero_when_drift_found(monkeypatch):
    from unittest.mock import MagicMock

    from dbx_platform import cli, estimator_monitor

    monkeypatch.setattr(cli, "get_client", lambda profile: MagicMock())
    monkeypatch.setattr(cli, "_verify_governed_write", lambda *a, **k: True)
    monkeypatch.setattr(
        estimator_monitor, "run_drift_check",
        lambda *a, **k: {estimator_monitor.REPRICING_CHECK: [{"resource": "e1"}],
                         estimator_monitor.ACTUALS_CHECK: []},
    )
    monkeypatch.setattr(estimator_monitor, "store_findings", lambda *a, **k: 1)
    parser = cli.build_parser()
    args = parser.parse_args(["estimator", "drift-check"])
    assert args.func(args) == 1  # drift found -> job fails so the alert fires


def test_cli_drift_check_returns_zero_when_clean(monkeypatch):
    from unittest.mock import MagicMock

    from dbx_platform import cli, estimator_monitor

    monkeypatch.setattr(cli, "get_client", lambda profile: MagicMock())
    monkeypatch.setattr(cli, "_verify_governed_write", lambda *a, **k: True)
    monkeypatch.setattr(
        estimator_monitor, "run_drift_check",
        lambda *a, **k: {estimator_monitor.REPRICING_CHECK: [],
                         estimator_monitor.ACTUALS_CHECK: []},
    )
    monkeypatch.setattr(estimator_monitor, "store_findings", lambda *a, **k: 0)
    parser = cli.build_parser()
    args = parser.parse_args(["estimator", "drift-check"])
    assert args.func(args) == 0
