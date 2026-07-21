import inspect
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dbx_platform import (
    ai_catalog,
    ai_monitor,
    azure_cost,
    dashboards,
    forecast_features,
    forecast_infer,
    llm_cost,
    release,
)
from dbx_platform.cli import (
    check_apply,
    cmd_dashboards_setup,
    cmd_llm_cost_rollup,
    cmd_publish_wheel,
    main,
)

EXECUTOR_POLICY = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "verify_executor_identity.sh"
)


def _executor_policy(**values: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(values)
    return subprocess.run(
        ["bash", str(EXECUTOR_POLICY)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_shared_executor_bootstrap_requires_proposal_only_exception():
    common = {
        "BUNDLE_VAR_runtime_executor_service_principal_name": "shared",
        "BUNDLE_VAR_action_executor_service_principal_name": "shared",
    }
    assert _executor_policy(**common).returncode == 1
    assert (
        _executor_policy(
            **common,
            DBX_PLATFORM_ALLOW_SHARED_EXECUTOR_SP="true",
            BUNDLE_VAR_actions_enabled="true",
        ).returncode
        == 1
    )
    allowed = _executor_policy(
        **common,
        DBX_PLATFORM_ALLOW_SHARED_EXECUTOR_SP="true",
        BUNDLE_VAR_actions_enabled="false",
    )
    assert allowed.returncode == 0
    assert "actions remain disabled" in allowed.stdout


def test_distinct_executor_identities_need_no_bootstrap_exception():
    result = _executor_policy(
        BUNDLE_VAR_runtime_executor_service_principal_name="runtime",
        BUNDLE_VAR_action_executor_service_principal_name="action",
        BUNDLE_VAR_actions_enabled="true",
        BUNDLE_VAR_approver_group_id="152821564284515",
    )
    assert result.returncode == 0


def test_enabled_actions_require_exact_numeric_approver_group_id():
    common = {
        "BUNDLE_VAR_runtime_executor_service_principal_name": "runtime",
        "BUNDLE_VAR_action_executor_service_principal_name": "action",
        "BUNDLE_VAR_actions_enabled": "true",
    }

    assert _executor_policy(**common).returncode == 1
    assert (
        _executor_policy(
            **common,
            BUNDLE_VAR_approver_group_id="dbx-platform-approvers",
        ).returncode
        == 1
    )


@pytest.mark.parametrize(
    "yes",
    [False, True],
)
def test_legacy_cli_apply_can_never_authorize_a_mutation(
    yes: bool,
    monkeypatch,
):
    monkeypatch.setenv("DBX_PLATFORM_CONFIRM", "true")
    with pytest.raises(SystemExit) as exc:
        check_apply(SimpleNamespace(apply=True, yes=yes))
    assert exc.value.code == 2


def test_legacy_cli_planners_remain_dry_run():
    assert check_apply(SimpleNamespace(apply=False, yes=False)) is False


@pytest.mark.parametrize("command", [cmd_dashboards_setup, cmd_publish_wheel])
def test_direct_stateful_utility_commands_are_disabled(command):
    assert command(SimpleNamespace()) == 2


def test_dashboard_and_volume_library_entrypoints_are_also_disabled():
    workspace = MagicMock()
    with pytest.raises(RuntimeError, match="dashboard setup is disabled"):
        dashboards.run_setup(
            workspace,
            "warehouse",
            "main",
            "dbx_platform",
            ["team"],
        )
    with pytest.raises(RuntimeError, match="wheel publication is disabled"):
        release.publish_wheel(workspace, "/Volumes/main/dbx_platform/wheels")
    with pytest.raises(RuntimeError, match="LLM ledger setup is disabled"):
        llm_cost.setup_ledger_tables(
            workspace,
            "warehouse",
            "main",
            "dbx_platform",
        )
    workspace.statement_execution.execute_statement.assert_not_called()
    workspace.files.upload.assert_not_called()


def test_scheduled_store_functions_never_execute_ddl():
    for function in (
        ai_catalog.store_catalog,
        ai_catalog.store_access,
        ai_monitor.store_monitoring,
        azure_cost.store_costs,
        azure_cost.store_detail_costs,
        forecast_features.store_features,
        forecast_infer.store_forecasts,
        llm_cost.store_ledger,
        llm_cost.store_source_health,
    ):
        source = inspect.getsource(function)
        assert "CREATE TABLE" not in source
        assert "create_table_sql(" not in source
        assert "setup_ledger_tables(" not in source


def test_llm_rollup_persists_feature_health_for_zero_and_unavailable_sources(
    monkeypatch,
):
    workspace = MagicMock()
    workspace.get_workspace_id.return_value = 123
    settings = SimpleNamespace(
        environment="prod",
        dashboard_catalog="main",
        dashboard_schema="dbx_platform",
        warehouse_id="warehouse",
    )
    monkeypatch.setattr("dbx_platform.cli.Settings.from_env", lambda: settings)
    monkeypatch.setattr("dbx_platform.cli.get_client", lambda _profile: workspace)
    monkeypatch.setattr(
        "dbx_platform.cli._verify_governed_write",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(llm_cost, "databricks_cost", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        llm_cost,
        "external_model_spend",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("preview")),
    )
    monkeypatch.setattr(
        llm_cost,
        "azure_actual_cost",
        lambda *_args, **_kwargs: llm_cost.AzureActualCostResult(
            rows=[],
            status="available",
            notes="detail available",
        ),
    )
    monkeypatch.setattr(
        llm_cost,
        "gateway_usage",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("preview")),
    )
    monkeypatch.setattr(llm_cost, "endpoint_usage", lambda *_args, **_kwargs: [])
    ledger_call = {}
    monkeypatch.setattr(
        llm_cost,
        "store_ledger",
        lambda *_args, **kwargs: ledger_call.update(kwargs) or {"cost_rows": 0, "usage_rows": 0},
    )
    health_call = {}
    monkeypatch.setattr(
        llm_cost,
        "store_source_health",
        lambda *call_args, **kwargs: health_call.update({"records": call_args[4], **kwargs}) or 4,
    )

    result = cmd_llm_cost_rollup(
        SimpleNamespace(
            profile=None,
            warehouse_id="warehouse",
            days=3,
            environment="prod",
            output="json",
        )
    )

    assert result == 0
    assert ledger_call["cost_scopes"]
    assert ledger_call["usage_scopes"]
    assert {
        scope["source"] for scope in ledger_call["usage_scopes"]
    } == {
        "system.ai_gateway.usage",
        "system.serving.endpoint_usage",
    }
    records = health_call["records"]
    by_key = {row["source_key"]: row for row in records}
    assert by_key["databricks-hosted-billing"]["status"] == "available"
    assert by_key["databricks-hosted-billing"]["row_count"] == 0
    assert by_key["ai-gateway-external-model-spend"]["status"] == "unavailable"
    assert by_key["model-request-usage"]["status"] == "partial"
    assert by_key["model-request-usage"]["row_count"] == 0
    assert health_call["workspace_id"] == "123"
    assert health_call["environment"] == "prod"


@pytest.mark.parametrize(
    "argv",
    [
        ["llm-cost", "rollup"],
        ["azure-cost", "pull"],
        ["forecast", "build-features"],
        ["forecast", "train"],
        ["forecast", "predict"],
        ["forecast", "monitor"],
        ["report", "operational-findings"],
        ["report", "ai-digest"],
        ["ai-catalog", "sync"],
        ["ai-monitor", "rollup"],
    ],
)
def test_direct_stateful_or_costly_cli_run_requires_governed_job_context(
    argv,
    monkeypatch,
):
    workspace = MagicMock()
    monkeypatch.setattr("dbx_platform.cli.get_client", lambda _profile: workspace)
    assert main(argv) == 2
    workspace.jobs.get_run.assert_not_called()
    workspace.statement_execution.execute_statement.assert_not_called()


def test_store_findings_flag_fails_closed_without_governed_context(monkeypatch):
    from dbx_platform import cli

    monkeypatch.setattr(cli, "get_client", lambda profile: MagicMock())
    monkeypatch.setattr(cli, "_verify_governed_write", lambda args, w, s: False)
    stored = MagicMock()
    monkeypatch.setattr("dbx_platform.digest.store_findings", stored)
    fetched = MagicMock()
    monkeypatch.setattr(azure_cost, "fetch_daily_buckets", fetched)
    args = SimpleNamespace(
        profile=None, warehouse_id="wh", days=7, output="json",
        store_findings=True, environment="prod",
    )
    assert cli.cmd_azure_cost_spikes(args) == 2
    stored.assert_not_called()
    fetched.assert_not_called()


def test_spikes_store_persists_check_key_even_when_clean(monkeypatch):
    from dbx_platform import cli

    monkeypatch.setattr(cli, "get_client", lambda profile: MagicMock())
    monkeypatch.setattr(cli, "_verify_governed_write", lambda args, w, s: True)
    monkeypatch.setattr(azure_cost, "fetch_daily_buckets", lambda *a, **k: [])
    captured = {}

    def fake_store(w, warehouse, catalog, schema, findings, **kwargs):
        captured.update(findings=findings, **kwargs)
        return 0

    monkeypatch.setattr("dbx_platform.digest.store_findings", fake_store)
    args = SimpleNamespace(
        profile=None, warehouse_id="wh", days=7, output="json",
        store_findings=True, environment="dev",
    )
    assert cli.cmd_azure_cost_spikes(args) == 0
    # The key is stored with zero rows so cleared spikes auto-resolve.
    assert captured["findings"] == {"cost/azure-spend-spike": []}
    assert captured["environment"] == "dev"


def test_report_commands_without_store_flag_never_write(monkeypatch):
    from dbx_platform import cli
    from dbx_platform import cost as cost_module

    monkeypatch.setattr(cli, "get_client", lambda profile: MagicMock())
    monkeypatch.setattr(cost_module, "cluster_utilization", lambda *a, **k: [])
    stored = MagicMock()
    monkeypatch.setattr("dbx_platform.digest.store_findings", stored)
    args = SimpleNamespace(
        profile=None, warehouse_id="wh", days=7, output="json",
        cpu_threshold=None, mem_threshold=None, store_findings=False,
    )
    assert cli.cmd_cost_cluster_utilization(args) == 0
    stored.assert_not_called()
