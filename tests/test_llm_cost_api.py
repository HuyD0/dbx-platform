"""Focused tests for the persisted-ledger LLM Cost & Value API."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "platform-console"
sys.path.insert(0, str(APP_DIR))

from backend import cache  # noqa: E402
from backend.routers import llm_cost as router  # noqa: E402


def test_request_path_reads_only_scoped_platform_ledgers(monkeypatch):
    calls: list[tuple] = []
    workspace = object()
    settings = SimpleNamespace(
        dashboard_catalog="main",
        dashboard_schema="dbx_platform",
    )

    monkeypatch.setattr(router.deps, "control_plane_scope", lambda: ("w-123", "prod"))
    monkeypatch.setattr(router.deps, "get_ws", lambda: workspace)
    monkeypatch.setattr(router.deps, "warehouse_id", lambda: "warehouse")
    monkeypatch.setattr(router.deps, "get_settings", lambda: settings)

    def read_cost(*args):
        calls.append(("cost", *args[4:]))
        return []

    def read_usage(*args):
        calls.append(("usage", *args[4:]))
        return []

    def read_health(*args):
        calls.append(("health", *args[4:]))
        return [
            {
                "source": "Databricks hosted model billing",
                "status": "available",
                "freshness": "hourly",
                "retention_days": 400,
                "cost_basis": "DATABRICKS_LIST",
                "row_count": 0,
            }
        ]

    def read_budgets(*args):
        calls.append(("budgets", *args[4:]))
        return []

    monkeypatch.setattr(router.llm_cost, "read_llm_cost_daily", read_cost)
    monkeypatch.setattr(router.llm_cost, "read_llm_usage_hourly", read_usage)
    monkeypatch.setattr(router.llm_cost, "read_llm_source_health", read_health)
    monkeypatch.setattr(router.llm_cost, "budget_rows", read_budgets)

    # Any request-time source probe is a regression: source queries belong to
    # the scheduled rollup, never the web process.
    for name in (
        "databricks_cost",
        "gateway_usage",
        "endpoint_usage",
        "external_model_spend",
        "azure_actual_cost",
    ):
        monkeypatch.setattr(
            router.llm_cost,
            name,
            lambda *_args, _name=name, **_kwargs: (_ for _ in ()).throw(
                AssertionError(f"request path called live source {_name}")
            ),
        )

    cache.clear()
    value, _as_of, hit = router._ledger(30)
    assert hit is False
    assert value["coverage"][0]["row_count"] == 0
    assert calls == [
        ("cost", "w-123", "prod", 30),
        ("usage", "w-123", "prod", 30),
        ("health", "w-123", "prod"),
        ("budgets", "w-123", "prod"),
    ]

    _value, _as_of, hit = router._ledger(30)
    assert hit is True
    assert len(calls) == 4


def test_ledger_cache_key_includes_workspace_and_environment(monkeypatch):
    scope = ["workspace-a", "dev"]
    reads: list[tuple[str, str]] = []
    settings = SimpleNamespace(
        dashboard_catalog="main",
        dashboard_schema="dbx_platform",
    )
    monkeypatch.setattr(router.deps, "control_plane_scope", lambda: tuple(scope))
    monkeypatch.setattr(router.deps, "get_ws", object)
    monkeypatch.setattr(router.deps, "warehouse_id", lambda: "warehouse")
    monkeypatch.setattr(router.deps, "get_settings", lambda: settings)
    monkeypatch.setattr(
        router.llm_cost,
        "read_llm_cost_daily",
        lambda *_args: reads.append((scope[0], scope[1])) or [],
    )
    monkeypatch.setattr(router.llm_cost, "read_llm_usage_hourly", lambda *_args: [])
    monkeypatch.setattr(router.llm_cost, "read_llm_source_health", lambda *_args: [])
    monkeypatch.setattr(router.llm_cost, "budget_rows", lambda *_args: [])

    cache.clear()
    router._ledger(30)
    scope[:] = ["workspace-b", "prod"]
    router._ledger(30)
    assert reads == [("workspace-a", "dev"), ("workspace-b", "prod")]


def test_missing_persisted_source_health_is_not_reported_as_zero_spend(
    monkeypatch,
):
    settings = SimpleNamespace(
        dashboard_catalog="main",
        dashboard_schema="dbx_platform",
    )
    monkeypatch.setattr(router.deps, "control_plane_scope", lambda: ("w", "prod"))
    monkeypatch.setattr(router.deps, "get_ws", object)
    monkeypatch.setattr(router.deps, "warehouse_id", lambda: "warehouse")
    monkeypatch.setattr(router.deps, "get_settings", lambda: settings)
    monkeypatch.setattr(
        router.llm_cost,
        "read_llm_cost_daily",
        lambda *_args: [],
    )
    monkeypatch.setattr(router.llm_cost, "read_llm_usage_hourly", lambda *_args: [])
    monkeypatch.setattr(router.llm_cost, "read_llm_source_health", lambda *_args: [])
    monkeypatch.setattr(router.llm_cost, "budget_rows", lambda *_args: [])

    cache.clear()
    value, _as_of, _hit = router._ledger(30)
    health = next(row for row in value["coverage"] if row["source"] == "LLM rollup source health")
    assert health["status"] == "unavailable"
    assert health["freshness"] == "never"
