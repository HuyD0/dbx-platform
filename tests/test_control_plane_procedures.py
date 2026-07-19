"""Security contracts for the human action-ledger write broker."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "platform-console"
sys.path.insert(0, str(APP_DIR))

from backend.control_plane import (  # noqa: E402
    ActionRequest,
    Actor,
    RiskLevel,
)
from backend.control_plane_repository import SQLControlPlaneRepository  # noqa: E402

from dbx_platform import migrations  # noqa: E402
from dbx_platform.control_plane_procedures import procedure_statements  # noqa: E402
from dbx_platform.migrations import procedure_migration_statements  # noqa: E402


def _action() -> ActionRequest:
    return ActionRequest.create(
        action_type="stale-clusters",
        workspace_id="workspace-1",
        environment="dev",
        targets=[{"cluster_id": "cluster-1", "action": "terminate"}],
        parameters={
            "execution_payload": [
                {"cluster_id": "cluster-1", "action": "terminate"}
            ]
        },
        preconditions={"state_sha256": "a" * 64},
        before_state=[],
        after_state={},
        impact={"target_count": 1},
        rollback={"supported": False},
        verification={"strategy": "re-read"},
        risk=RiskLevel.MEDIUM,
        proposer=Actor(
            actor_id="user-1",
            email="operator@example.com",
            roles=frozenset({"proposer"}),
        ),
    )


def test_security_definer_procedures_are_app_only_and_atomic():
    statements = procedure_statements(
        "main",
        "dbx_platform",
        app_service_principal="00000000-0000-0000-0000-000000000001",
        operator_group="dbx-platform-operators",
        approver_group="dbx-platform-approvers",
    )
    sql = "\n".join(statement for _description, statement in statements)

    assert sql.count("SQL SECURITY DEFINER") == 4
    assert sql.count("AS BEGIN ATOMIC") == 4
    assert "is_account_group_member" not in sql
    assert "session_user()" not in sql
    assert "p_proposer_id, p_proposer_email" in sql
    assert "p_decision, p_approver_id, p_approver_email" in sql
    assert "p_actor_id, p_details_json" in sql
    assert "sha2(p_plan_json, 256)" in sql
    assert "p_target_status NOT IN ('STALE', 'EXPIRED')" in sql
    assert "p_target_status = 'APPROVED'" in sql
    assert "INTERVAL 15 MINUTES" in sql
    assert "json_array_length" in sql
    assert "current_timestamp() >= v_expires_at" in sql
    assert "p_confirmation <> v_confirm_phrase" not in sql
    assert "p_action_type = 'token-revoke' AND p_risk <> 'HIGH'" in sql
    assert "p_action_type = 'run-job' AND p_risk NOT IN ('LOW', 'MEDIUM')" in sql
    assert "GRANT EXECUTE ON PROCEDURE" in sql
    assert "GRANT MODIFY" not in sql
    assert (
        "GRANT EXECUTE ON PROCEDURE "
        "`main`.`dbx_platform`.`cp_create_action` "
        "TO `dbx-platform-operators`"
    ) not in sql
    assert (
        "GRANT EXECUTE ON PROCEDURE "
        "`main`.`dbx_platform`.`cp_decide_action` "
        "TO `dbx-platform-approvers`"
    ) not in sql
    assert (
        "GRANT EXECUTE ON PROCEDURE "
        "`main`.`dbx_platform`.`cp_decide_action` "
        "TO `00000000-0000-0000-0000-000000000001`"
    ) in sql
    assert (
        "REVOKE EXECUTE ON PROCEDURE "
        "`main`.`dbx_platform`.`cp_decide_action` "
        "FROM `dbx-platform-approvers`"
    ) in sql


def test_procedure_migration_rejects_unsafe_group_names():
    with pytest.raises(ValueError, match="principal"):
        procedure_statements(
            "main",
            "dbx_platform",
            app_service_principal="app",
            operator_group="operators`; GRANT ALL",
            approver_group="approvers",
        )


def test_proposal_only_migration_creates_procedures_without_group_grants():
    statements = procedure_migration_statements(
        "main",
        "dbx_platform",
        app_service_principal="app",
        operator_group="missing-operators",
        approver_group="missing-approvers",
        actions_enabled=False,
    )
    sql = "\n".join(statement for _description, statement in statements)

    assert sql.count("CREATE OR REPLACE PROCEDURE") == 4
    assert "GRANT EXECUTE" not in sql
    assert "REVOKE EXECUTE" in sql


def test_enabled_migration_requires_configured_group_grants():
    statements = procedure_migration_statements(
        "main",
        "dbx_platform",
        app_service_principal="app",
        operator_group="dbx-platform-operators",
        approver_group="dbx-platform-approvers",
        actions_enabled=True,
    )

    assert any(description.startswith("grant ") for description, _sql in statements)
    assert all(
        "dbx-platform-approvers" not in sql
        for description, sql in statements
        if description.startswith("grant ")
    )


def test_migration_entry_returns_normally_on_success_and_raises_on_failure(
    monkeypatch,
):
    monkeypatch.setattr(migrations, "main", lambda _argv=None: 0)
    assert migrations.entry([]) is None

    monkeypatch.setattr(migrations, "main", lambda _argv=None: 1)
    with pytest.raises(SystemExit) as error:
        migrations.entry([])
    assert error.value.code == 1


def test_sql_human_action_write_calls_broker_not_table_insert():
    repository = SQLControlPlaneRepository(
        MagicMock(),
        "warehouse-1",
        "main",
        "dbx_platform",
        workspace_id="workspace-1",
        environment="dev",
    )
    repository._initialized = True
    calls: list[tuple[str, dict]] = []
    repository._run = lambda sql, parameters=None: (
        calls.append((sql, parameters or {})) or []
    )

    action = _action()
    repository.create_action(action)

    assert len(calls) == 1
    sql, parameters = calls[0]
    assert sql.startswith("CALL `main`.`dbx_platform`.`cp_create_action`")
    assert "INSERT INTO" not in sql
    assert parameters["plan_hash"] == action.plan_hash
    assert parameters["proposer_email"] == "operator@example.com"


# --- saved-estimate library broker -------------------------------------------


def test_estimate_procedure_is_app_only_append_and_validated():
    from dbx_platform.control_plane_procedures import estimate_procedure_statements

    statements = estimate_procedure_statements(
        "main",
        "dbx_platform",
        app_service_principal="00000000-0000-0000-0000-000000000001",
    )
    sql = "\n".join(statement for _description, statement in statements)
    assert "cp_record_estimate" in sql
    assert "SQL SECURITY DEFINER" in sql
    assert "BEGIN ATOMIC" in sql
    assert sql.count("INSERT INTO") == 1  # append-only: no UPDATE/DELETE
    assert "UPDATE" not in sql and "DELETE" not in sql
    assert "The verified creator identity is required" in sql
    assert "'^[0-9a-f]{64}$'" in sql  # canonical requirements hash enforced
    assert "current_timestamp()" in sql  # created_at stamped server-side
    grants = [d for d, _ in statements if d.startswith("grant ")]
    assert grants == [
        "grant 00000000-0000-0000-0000-000000000001 execute on cp_record_estimate"
    ]


def test_estimate_procedure_grant_survives_actions_disabled_migrations():
    """The library must work in proposal-only deployments: its grant is not
    action machinery and never rides the actions_enabled gate."""

    spark = MagicMock()
    completed = migrations.run_migrations(
        spark,
        "main",
        "dbx_platform",
        ["team"],
        app_service_principal="00000000-0000-0000-0000-000000000001",
        actions_enabled=False,
    )
    assert any("cp_record_estimate" in step for step in completed)
    assert "grant 00000000-0000-0000-0000-000000000001 execute on cp_record_estimate" in completed
    # the human action procedures stay grant-filtered
    assert not any(
        step.startswith("grant ") and "cp_create_action" in step for step in completed
    )


def test_sql_estimate_write_calls_broker_not_table_insert():
    repository = SQLControlPlaneRepository(
        MagicMock(),
        "warehouse",
        "main",
        "dbx_platform",
        workspace_id="workspace-1",
        environment="dev",
    )
    executed: list[tuple[str, dict]] = []
    repository._run = lambda sql, params=None: executed.append((sql, params or {})) or []
    repository.record_estimate(
        {
            "workspace_id": "workspace-1",
            "environment": "dev",
            "estimate_id": "e1",
            "created_by": "user-1",
            "title": "Doc chat",
            "pattern": "doc_chat",
            "monthly_requests": 4000,
            "corpus_gb": 2.0,
            "requirements_json": '{"pattern": "doc_chat"}',
            "requirements_hash": "a" * 64,
            "engine_version": "1",
            "rate_card_version": "2026.07.1",
            "snapshot_date": "2026-07-14",
            "rigor_pct": 10,
            "results_json": "{}",
        }
    )
    assert len(executed) == 1
    sql, params = executed[0]
    assert "CALL" in sql and "cp_record_estimate" in sql
    assert "INSERT" not in sql
    assert params["monthly_requests"] == "4000"  # procedure params travel as strings
    assert params["created_by"] == "user-1"


# --- deployment-link broker ---------------------------------------------------


def test_deployment_procedure_is_app_only_append_and_validated():
    from dbx_platform.control_plane_procedures import deployment_procedure_statements

    statements = deployment_procedure_statements(
        "main", "dbx_platform",
        app_service_principal="00000000-0000-0000-0000-000000000001",
    )
    sql = "\n".join(statement for _description, statement in statements)
    assert "cp_link_deployment" in sql
    assert "SQL SECURITY DEFINER" in sql
    assert "BEGIN ATOMIC" in sql
    assert sql.count("INSERT INTO") == 1  # append-only
    assert "UPDATE" not in sql and "DELETE" not in sql
    assert "The verified creator identity is required" in sql
    # allowlists for tier/scenario/anchor kind
    assert "'prototype', 'production', 'fiduciary'" in sql
    assert "'databricks', 'azure'" in sql
    assert "azure_resource_group" in sql
    # the referenced estimate must exist
    assert "estimator_estimates" in sql
    grants = [d for d, _ in statements if d.startswith("grant ")]
    assert grants == [
        "grant 00000000-0000-0000-0000-000000000001 execute on cp_link_deployment"
    ]


def test_deployment_grant_survives_actions_disabled_migrations():
    spark = MagicMock()
    completed = migrations.run_migrations(
        spark, "main", "dbx_platform", ["team"],
        app_service_principal="00000000-0000-0000-0000-000000000001",
        actions_enabled=False,
    )
    assert any("cp_link_deployment" in step for step in completed)
    assert (
        "grant 00000000-0000-0000-0000-000000000001 execute on cp_link_deployment"
        in completed
    )
