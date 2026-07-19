import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import yaml

from dbx_platform.approved_job import (
    ApprovalGateError,
    verify_approved_job_launch,
    verify_governed_write_launch,
)


def _hash(value: dict) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _plan(job_id: int = 42) -> dict:
    return {
        "action_id": "action-1",
        "action_type": "run-job",
        "workspace_id": "123",
        "environment": "dev",
        "targets": [{"job_id": job_id}],
        "parameters": {"execution_payload": {"job_id": job_id}},
    }


def _workspace() -> MagicMock:
    workspace = MagicMock()
    workspace.get_workspace_id.return_value = 123
    return workspace


def test_exact_executor_launched_job_run_is_accepted():
    plan = _plan()
    plan_hash = _hash(plan)
    responses = [
        [
            {
                "workspace_id": "123",
                "environment": "dev",
                "action_id": "action-1",
                "action_type": "run-job",
                "status": "SUCCEEDED",
                "plan_json": json.dumps(plan),
                "plan_hash": plan_hash,
            }
        ],
        [{"approval_id": "approval-1"}],
        [{"event_id": "event-1"}],
    ]
    query = MagicMock(side_effect=responses)

    verify_approved_job_launch(
        _workspace(),
        "warehouse",
        catalog="main",
        schema="dbx_platform",
        environment="dev",
        action_id="action-1",
        plan_hash=plan_hash,
        job_id=42,
        run_id=9001,
        wait_seconds=0,
        query=query,
    )

    event_call = query.call_args_list[2]
    assert event_call.kwargs["parameters"]["run_id"] == "9001"
    assert "event_type = 'STATUS_VERIFYING'" in event_call.args[1]
    assert "$.result.run_id" in event_call.args[1]


def test_manual_rerun_with_another_run_id_fails_closed():
    plan = _plan()
    plan_hash = _hash(plan)
    responses = [
        [
            {
                "workspace_id": "123",
                "environment": "dev",
                "action_id": "action-1",
                "action_type": "run-job",
                "status": "SUCCEEDED",
                "plan_json": json.dumps(plan),
                "plan_hash": plan_hash,
            }
        ],
        [{"approval_id": "approval-1"}],
        [],
    ]

    with pytest.raises(ApprovalGateError, match="exact Databricks run ID"):
        verify_approved_job_launch(
            _workspace(),
            "warehouse",
            catalog="main",
            schema="dbx_platform",
            environment="dev",
            action_id="action-1",
            plan_hash=plan_hash,
            job_id=42,
            run_id=9002,
            wait_seconds=0,
            query=MagicMock(side_effect=responses),
        )


def test_modified_immutable_plan_fails_before_event_lookup():
    plan = _plan()
    plan_hash = _hash(plan)
    plan["targets"][0]["job_id"] = 99
    query = MagicMock(
        return_value=[
            {
                "workspace_id": "123",
                "environment": "dev",
                "action_id": "action-1",
                "action_type": "run-job",
                "status": "EXECUTING",
                "plan_json": json.dumps(plan),
                "plan_hash": plan_hash,
            }
        ]
    )

    with pytest.raises(ApprovalGateError, match="immutable action payload"):
        verify_approved_job_launch(
            _workspace(),
            "warehouse",
            catalog="main",
            schema="dbx_platform",
            environment="dev",
            action_id="action-1",
            plan_hash=plan_hash,
            job_id=42,
            run_id=9001,
            wait_seconds=0,
            query=query,
        )
    assert query.call_count == 1


def test_missing_action_context_never_queries_or_mutates():
    query = MagicMock()
    with pytest.raises(ApprovalGateError, match="require an action ID"):
        verify_approved_job_launch(
            _workspace(),
            "warehouse",
            catalog="main",
            schema="dbx_platform",
            environment="dev",
            action_id="",
            plan_hash="",
            job_id=42,
            run_id=9001,
            query=query,
        )
    query.assert_not_called()


def test_periodic_job_write_is_autonomous_without_an_approval():
    workspace = _workspace()
    workspace.jobs.get_run.return_value = SimpleNamespace(
        run_id=9001,
        job_id=42,
        trigger=SimpleNamespace(value="PERIODIC"),
    )
    query = MagicMock()

    verify_governed_write_launch(
        workspace,
        "warehouse",
        catalog="main",
        schema="dbx_platform",
        environment="dev",
        action_id="",
        plan_hash="",
        job_id=42,
        run_id=9001,
        trigger_type="periodic",
        query=query,
    )
    query.assert_not_called()


def test_manual_job_write_requires_exact_approved_run_event():
    workspace = _workspace()
    workspace.jobs.get_run.return_value = SimpleNamespace(
        run_id=9002,
        job_id=42,
        trigger=SimpleNamespace(value="ONE_TIME"),
    )

    with pytest.raises(ApprovalGateError, match="require an action ID"):
        verify_governed_write_launch(
            workspace,
            "warehouse",
            catalog="main",
            schema="dbx_platform",
            environment="dev",
            action_id="",
            plan_hash="",
            job_id=42,
            run_id=9002,
            trigger_type="ONE_TIME",
            wait_seconds=0,
            query=MagicMock(),
        )


def test_spoofed_periodic_trigger_is_rejected_against_jobs_api():
    workspace = _workspace()
    workspace.jobs.get_run.return_value = SimpleNamespace(
        run_id=9002,
        job_id=42,
        trigger=SimpleNamespace(value="ONE_TIME"),
    )

    with pytest.raises(ApprovalGateError, match="trigger type"):
        verify_governed_write_launch(
            workspace,
            "warehouse",
            catalog="main",
            schema="dbx_platform",
            environment="dev",
            action_id="",
            plan_hash="",
            job_id=42,
            run_id=9002,
            trigger_type="PERIODIC",
            query=MagicMock(),
        )


def test_bundle_binds_forecast_train_to_exact_action_job_and_run():
    resource = (
        __import__("pathlib").Path(__file__).resolve().parent.parent
        / "resources"
        / "forecast_jobs.yml"
    ).read_text()
    for required in (
        "approved_action_id",
        "approved_plan_hash",
        "--approved-action-id",
        "--approved-plan-hash",
        '--approved-job-id", "{{job.id}}"',
        '--approved-run-id", "{{job.run_id}}"',
        '--environment", "${bundle.target}"',
    ):
        assert required in resource
    assert "schedule:" not in resource.split("cost_forecast_train:", 1)[1].split(
        "cost_forecast_daily:", 1
    )[0]


def test_every_scheduled_stateful_task_passes_exact_run_context():
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    expected = {
        ("llm-cost", "rollup"),
        ("azure-cost", "pull"),
        ("forecast", "build-features"),
        ("forecast", "predict"),
        ("forecast", "monitor"),
        ("report", "operational-findings"),
        ("report", "ai-digest"),
        ("ai-catalog", "sync"),
        ("ai-monitor", "rollup"),
    }
    found = set()
    required = {
        "--approved-action-id",
        "--approved-plan-hash",
        "--approved-job-id",
        "--approved-run-id",
        "--trigger-type",
        "--environment",
    }
    for resource_path in (root / "resources").glob("*.yml"):
        document = yaml.safe_load(resource_path.read_text()) or {}
        for job in document.get("resources", {}).get("jobs", {}).values():
            for task in job.get("tasks", []):
                parameters = task.get("python_wheel_task", {}).get("parameters", [])
                command = tuple(parameters[:2])
                if command not in expected:
                    continue
                found.add(command)
                assert required.issubset(set(parameters)), (
                    resource_path.name,
                    command,
                )
                assert "{{job.id}}" in parameters
                assert "{{job.run_id}}" in parameters
                assert "{{job.trigger.type}}" in parameters
                assert "${bundle.target}" in parameters
    assert found == expected


def test_app_binds_manual_job_without_adding_it_to_hibernate_inventory():
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    app_resource = (root / "resources" / "app.yml").read_text()
    runtime_resource = (root / "resources" / "runtime_control.yml").read_text()
    assert "DBX_PLATFORM_GOVERNED_MANUAL_JOB_IDS" in app_resource
    assert "${resources.jobs.cost_forecast_train.id}" in app_resource
    assert "cost_forecast_train=${resources.jobs.cost_forecast_train.id}" not in (
        runtime_resource
    )


def test_every_scheduled_job_grants_only_exact_runtime_and_run_permissions():
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    scheduled = []
    for resource_path in (root / "resources").glob("*.yml"):
        document = yaml.safe_load(resource_path.read_text()) or {}
        for key, job in document.get("resources", {}).get("jobs", {}).items():
            if "schedule" not in job:
                continue
            scheduled.append(key)
            grants = {
                (
                    row.get("service_principal_name"),
                    row.get("level"),
                )
                for row in job.get("permissions", [])
            }
            assert (
                "${var.runtime_executor_service_principal_name}",
                "CAN_MANAGE",
            ) in grants
            assert (
                "${var.action_executor_service_principal_name}",
                "CAN_MANAGE_RUN",
            ) in grants
    assert len(scheduled) == 14
