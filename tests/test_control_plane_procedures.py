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


def test_security_definer_procedures_use_native_group_grants_and_are_atomic():
    statements = procedure_statements(
        "main",
        "dbx_platform",
        operator_group="dbx-platform-operators",
        approver_group="dbx-platform-approvers",
    )
    sql = "\n".join(statement for _description, statement in statements)

    assert sql.count("SQL SECURITY DEFINER") == 4
    assert sql.count("AS BEGIN ATOMIC") == 4
    assert "is_account_group_member" not in sql
    assert "session_user()" in sql
    assert "sha2(p_plan_json, 256)" in sql
    assert "p_target_status NOT IN ('STALE', 'EXPIRED')" in sql
    assert "p_target_status = 'APPROVED'" in sql
    assert "INTERVAL 15 MINUTES" in sql
    assert "json_array_length" in sql
    assert "current_timestamp() >= v_expires_at" in sql
    assert "p_confirmation <> v_confirm_phrase" in sql
    assert "p_action_type = 'token-revoke' AND p_risk <> 'HIGH'" in sql
    assert "GRANT EXECUTE ON PROCEDURE" in sql
    assert "GRANT MODIFY" not in sql
    assert (
        "GRANT EXECUTE ON PROCEDURE "
        "`main`.`dbx_platform`.`cp_decide_action` "
        "TO `dbx-platform-operators`"
    ) not in sql
    assert (
        "GRANT EXECUTE ON PROCEDURE "
        "`main`.`dbx_platform`.`cp_decide_action` "
        "TO `dbx-platform-approvers`"
    ) in sql


def test_procedure_migration_rejects_unsafe_group_names():
    with pytest.raises(ValueError, match="principal"):
        procedure_statements(
            "main",
            "dbx_platform",
            operator_group="operators`; GRANT ALL",
            approver_group="approvers",
        )


def test_proposal_only_migration_creates_procedures_without_group_grants():
    statements = procedure_migration_statements(
        "main",
        "dbx_platform",
        operator_group="missing-operators",
        approver_group="missing-approvers",
        actions_enabled=False,
    )
    sql = "\n".join(statement for _description, statement in statements)

    assert sql.count("CREATE OR REPLACE PROCEDURE") == 4
    assert "GRANT EXECUTE" not in sql


def test_enabled_migration_requires_configured_group_grants():
    statements = procedure_migration_statements(
        "main",
        "dbx_platform",
        operator_group="dbx-platform-operators",
        approver_group="dbx-platform-approvers",
        actions_enabled=True,
    )

    assert any(description.startswith("grant ") for description, _sql in statements)


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
