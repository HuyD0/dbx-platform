from datetime import date
from types import SimpleNamespace

from dbx_platform.platform_agent import tools


def test_llm_cost_tool_excludes_foreign_workspace_rows(monkeypatch, request):
    current_workspace = "workspace-current"
    foreign_workspace = "workspace-foreign"
    account_cost_rows = [
        {
            "usage_date": date.today().isoformat(),
            "workspace_id": current_workspace,
            "provider": "databricks",
            "model": "current-model",
            "cost": 7,
            "currency": "USD",
        },
        {
            "usage_date": date.today().isoformat(),
            "workspace_id": foreign_workspace,
            "provider": "databricks",
            "model": "foreign-model",
            "cost": 999,
            "currency": "USD",
        },
    ]
    account_usage_rows = [
        {
            "usage_date": date.today().isoformat(),
            "workspace_id": current_workspace,
            "provider": "databricks",
            "model": "current-model",
            "requests": 3,
            "input_tokens": 30,
            "output_tokens": 10,
        },
        {
            "usage_date": date.today().isoformat(),
            "workspace_id": foreign_workspace,
            "provider": "databricks",
            "model": "foreign-model",
            "requests": 999,
            "input_tokens": 9990,
            "output_tokens": 9990,
        },
    ]
    scopes = []

    def scoped_cost(
        _workspace,
        _warehouse,
        _days,
        *,
        gateway_enriched,
        workspace_id,
    ):
        scopes.append(("cost", gateway_enriched, workspace_id))
        return [
            row for row in account_cost_rows
            if row["workspace_id"] == workspace_id
        ]

    def scoped_usage(_workspace, _warehouse, _days, *, workspace_id):
        scopes.append(("usage", workspace_id))
        return [
            row for row in account_usage_rows
            if row["workspace_id"] == workspace_id
        ]

    client = SimpleNamespace(get_workspace_id=lambda: current_workspace)
    settings = SimpleNamespace(warehouse_id="warehouse-1", environment="prod")
    monkeypatch.setattr(tools, "_client_factory", lambda: client)
    monkeypatch.setattr(tools, "_settings_factory", lambda: settings)
    monkeypatch.setattr(tools.llm_cost, "databricks_cost", scoped_cost)
    monkeypatch.setattr(tools.llm_cost, "gateway_usage", scoped_usage)
    tools._client.cache_clear()
    request.addfinalizer(tools._client.cache_clear)

    result = tools.get_llm_cost_and_efficiency.invoke({"days": 30})

    assert scopes == [
        ("cost", True, current_workspace),
        ("usage", current_workspace),
    ]
    assert "cost=7.0" in result
    assert "requests=3" in result
    assert "foreign-model" not in result
    assert "999" not in result
