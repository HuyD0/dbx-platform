"""Offline tests for the estimator assistant tools (no network, no langchain)."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace


class _FakeTool:
    def __init__(self, function):
        self.function = function
        self.__name__ = getattr(function, "__name__", "tool")

    def invoke(self, arguments):
        return self.function(**arguments)


def _load_tools(monkeypatch, request):
    langchain_core = ModuleType("langchain_core")
    langchain_tools = ModuleType("langchain_core.tools")
    langchain_tools.tool = _FakeTool
    langchain_core.tools = langchain_tools
    monkeypatch.setitem(sys.modules, "langchain_core", langchain_core)
    monkeypatch.setitem(sys.modules, "langchain_core.tools", langchain_tools)
    sys.modules.pop("dbx_platform.platform_agent.tools", None)
    tools = importlib.import_module("dbx_platform.platform_agent.tools")
    request.addfinalizer(
        lambda: sys.modules.pop("dbx_platform.platform_agent.tools", None)
    )
    return tools


_SNAPSHOT_PRICES = {
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


def _snapshot_rows():
    return [
        {"snapshot_date": "2026-07-14", "source": "azure_retail", "rate_key": key,
         "meter_name": f"meter:{key}", "unit_price": value, "currency": "USD"}
        for key, value in _SNAPSHOT_PRICES.items()
    ]


def test_list_solution_patterns_is_pure_and_cites_the_catalog(monkeypatch, request):
    tools = _load_tools(monkeypatch, request)
    result = tools.list_solution_patterns.invoke({})
    assert "doc_chat" in result and "agent_workflow" in result
    assert "EVIDENCE:tool=list_solution_patterns;source=estimator_data/patterns.json" in result


def test_estimate_solution_cost_runs_the_engine_and_cites_snapshot(monkeypatch, request):
    tools = _load_tools(monkeypatch, request)
    settings = SimpleNamespace(
        warehouse_id="warehouse-1", environment="prod",
        dashboard_catalog="cat", dashboard_schema="sch",
    )
    monkeypatch.setattr(tools, "_client_factory", lambda: SimpleNamespace())
    monkeypatch.setattr(tools, "_settings_factory", lambda: settings)
    monkeypatch.setattr(
        tools.estimator_pricing, "read_latest_snapshot",
        lambda *a, **k: _snapshot_rows(),
    )
    tools._client.cache_clear()
    request.addfinalizer(tools._client.cache_clear)

    result = tools.estimate_solution_cost.invoke(
        {"requirements_json": '{"pattern": "doc_chat", "monthly_requests": 100000}',
         "rigor_pct": 25}
    )
    assert "tier=production" in result
    assert "scenario=azure" in result
    assert "prod_monthly_usd=" in result
    assert "engine v1" in result and "snapshot_date=2026-07-14" in result


def test_estimate_solution_cost_rejects_bad_json_without_touching_the_warehouse(
    monkeypatch, request
):
    tools = _load_tools(monkeypatch, request)

    def _boom(*args, **kwargs):
        raise AssertionError("must not read the snapshot for invalid input")

    monkeypatch.setattr(tools.estimator_pricing, "read_latest_snapshot", _boom)
    assert "JSON object" in tools.estimate_solution_cost.invoke(
        {"requirements_json": "not json"}
    )
    assert "not valid" in tools.estimate_solution_cost.invoke(
        {"requirements_json": '{"pattern": "nope", "monthly_requests": 1}'}
    )


def test_estimate_solution_cost_degrades_without_a_snapshot(monkeypatch, request):
    tools = _load_tools(monkeypatch, request)
    settings = SimpleNamespace(
        warehouse_id="w", environment="prod", dashboard_catalog="c", dashboard_schema="s"
    )
    monkeypatch.setattr(tools, "_client_factory", lambda: SimpleNamespace())
    monkeypatch.setattr(tools, "_settings_factory", lambda: settings)
    monkeypatch.setattr(tools.estimator_pricing, "read_latest_snapshot", lambda *a, **k: [])
    tools._client.cache_clear()
    request.addfinalizer(tools._client.cache_clear)
    result = tools.estimate_solution_cost.invoke(
        {"requirements_json": '{"pattern": "doc_chat", "monthly_requests": 10}'}
    )
    assert "estimator-prices-pull" in result


def test_new_tools_are_in_all_tools(monkeypatch, request):
    tools = _load_tools(monkeypatch, request)
    names = {getattr(t, "__name__", "") for t in tools.ALL_TOOLS}
    assert {"list_solution_patterns", "estimate_solution_cost"} <= names
