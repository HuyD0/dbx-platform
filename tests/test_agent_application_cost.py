"""Offline contract tests for the read-only application-cost agent tool."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType, SimpleNamespace


class _FakeTool:
    def __init__(self, function):
        self.function = function

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


def test_application_cost_tool_uses_shared_attribution_and_separate_ledgers(
    monkeypatch,
    request,
):
    tools = _load_tools(monkeypatch, request)
    workspace = SimpleNamespace(get_workspace_id=lambda: "workspace-1")
    settings = SimpleNamespace(
        warehouse_id="warehouse-1",
        dashboard_catalog="platform",
        dashboard_schema="ops",
        environment="prod",
        azure_subscription_id="sub-1",
        application_tag_key_list=lambda: ["application", "app", "project"],
    )
    monkeypatch.setattr(tools, "_client_factory", lambda: workspace)
    monkeypatch.setattr(tools, "_settings_factory", lambda: settings)
    tools._client.cache_clear()
    request.addfinalizer(tools._client.cache_clear)

    captured = {}

    def read(*args, **kwargs):
        captured.update(kwargs)
        return ([{"application_key": "learn-app"}], [{"source": "azure"}])

    def profile(application_key, evidence, *, source_health, days):
        assert application_key == "Learn App"
        assert evidence == [{"application_key": "learn-app"}]
        assert source_health == [{"source": "azure"}]
        assert days == 30
        return {
            "application": {"application_key": "learn-app"},
            "ledgers": [
                {
                    "source": "azure",
                    "amount": 12.5,
                    "currency": "CAD",
                    "pricing_basis": "AZURE_ACTUAL",
                    "coverage_start": "2026-07-01",
                    "coverage_end": "2026-07-25",
                    "freshness": "2026-07-25T12:00:00Z",
                },
                {
                    "source": "databricks",
                    "amount": 4.0,
                    "currency": "USD",
                    "pricing_basis": "DATABRICKS_LIST",
                    "coverage_start": "2026-07-01",
                    "coverage_end": "2026-07-25",
                    "freshness": "2026-07-25T11:00:00Z",
                },
            ],
            "unallocated": [],
            "tag_alignment": [{"status": "matched"}],
            "source_health": [],
        }

    monkeypatch.setattr(tools.application_cost, "read_application_evidence", read)
    monkeypatch.setattr(tools.application_cost, "build_profile", profile)

    result = tools.get_application_cost.invoke(
        {"application_key": "Learn App", "days": 30}
    )

    assert captured["workspace_id"] == "workspace-1"
    assert captured["environment"] == "prod"
    assert "amount=12.5, currency=CAD, pricing_basis=AZURE_ACTUAL" in result
    assert "amount=4.0, currency=USD, pricing_basis=DATABRICKS_LIST" in result
    assert "tool=get_application_cost" in result
    assert "observed_at=2026-07-25T12:00:00Z" in result
    assert "total=" not in result
    assert tools.get_application_cost in tools.ALL_TOOLS
