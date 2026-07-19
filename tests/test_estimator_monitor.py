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
