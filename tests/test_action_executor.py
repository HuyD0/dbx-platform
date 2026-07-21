"""Fail-closed and exactly-once tests for the dedicated action executor."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dbx_platform import action_executor
from dbx_platform.action_executor import (
    STATUS_APPROVED,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    ActionExecutionError,
    ActionHandler,
    ActionRolledBackError,
    AuditStorageUnavailableError,
    GovernedActionExecutor,
    StaleActionError,
    StoredAction,
    StoredApproval,
    TrustedPlan,
    bind_action_handler,
    bind_budget_handler,
    build_handlers,
    canonical_hash,
)
from dbx_platform.config import Settings


def test_serverless_entry_returns_normally_only_on_success(monkeypatch) -> None:
    monkeypatch.setattr(action_executor, "main", lambda _argv=None: 0)
    assert action_executor.entry([]) is None

    monkeypatch.setattr(action_executor, "main", lambda _argv=None: 2)
    with pytest.raises(SystemExit) as error:
        action_executor.entry([])
    assert error.value.code == 2


class FakeStore:
    def __init__(self, action: StoredAction, *, approved: bool = True) -> None:
        self.action = action
        self.approved = approved
        self.ready = True
        self.transitions: list[tuple[str, str]] = []
        self.events: list[tuple[str, str]] = []
        self.event_writes_fail = False
        self.verification_checkpoint: dict | None = None

    def ensure_ready(self) -> None:
        if not self.ready:
            raise AuditStorageUnavailableError("missing audit storage")

    def get_action(self, action_id: str) -> StoredAction | None:
        return self.action if action_id == self.action.action_id else None

    def get_matching_approval(
        self,
        action_id: str,
        plan_hash: str,
    ) -> StoredApproval | None:
        if (
            not self.approved
            or action_id != self.action.action_id
            or plan_hash != self.action.plan_hash
        ):
            return None
        return StoredApproval(
            approval_id="approval-1",
            approver_id="approver-1",
            approver_email="approver@example.com",
            approver_role="approver",
            confirmation=self.action.plan["confirm_phrase"],
        )

    def transition(
        self,
        action_id: str,
        allowed_from: set[str],
        to_status: str,
        actor_id: str,
        details,
    ) -> None:
        assert action_id == self.action.action_id
        if self.action.status not in allowed_from:
            raise ActionExecutionError("concurrent claim")
        self.transitions.append((self.action.status, to_status))
        if to_status == "VERIFYING" and details.get("checkpoint") == "MUTATION_APPLIED":
            self.verification_checkpoint = dict(details)
        self.action = replace(self.action, status=to_status)

    def append_event(self, action_id, event_type, actor_id, details) -> None:
        assert action_id == self.action.action_id
        assert actor_id
        assert details
        if self.event_writes_fail:
            raise AuditStorageUnavailableError("audit append failed")
        self.events.append((event_type, self.action.status))

    def get_verification_checkpoint(self, action_id) -> dict | None:
        assert action_id == self.action.action_id
        return self.verification_checkpoint


def make_action(
    current: TrustedPlan,
    *,
    action_type: str = "stale-clusters",
    expires_at: datetime | None = None,
) -> StoredAction:
    now = datetime(2026, 7, 17, 12, tzinfo=UTC)
    expiry = expires_at or now + timedelta(minutes=15)
    plan = {
        "schema_version": 1,
        "action_id": "action-1",
        "action_type": action_type,
        "workspace_id": "workspace-1",
        "environment": "dev",
        "targets": current.targets,
        "parameters": {"execution_payload": current.execution_payload},
        "preconditions": {
            "state_sha256": canonical_hash(current.state_document),
        },
        "before_state": current.targets,
        "after_state": {},
        "impact": {},
        "rollback": {"supported": False},
        "verification": {},
        "risk": "MEDIUM",
        "proposer_id": "user-1",
        "proposer_email": "user@example.com",
        "created_at": now.isoformat(),
        "expires_at": expiry.isoformat(),
        "idempotency_key": "key-1",
        "confirm_phrase": f"apply {action_type} 1",
    }
    return StoredAction(
        action_id="action-1",
        action_type=action_type,
        workspace_id="workspace-1",
        environment="dev",
        status=STATUS_APPROVED,
        plan=plan,
        plan_hash=canonical_hash(plan),
        expires_at=expiry,
    )


def build_executor(
    store: FakeStore,
    current_plan,
    applied: list,
    *,
    clock=lambda: datetime(2026, 7, 17, 12, 1, tzinfo=UTC),
):
    handler = ActionHandler(
        plan=lambda: current_plan,
        apply=lambda payload: applied.append(payload) or {"changed": 1},
        verify=lambda payload, result: {"verified": True},
    )
    return GovernedActionExecutor(
        store,
        {"stale-clusters": handler},
        workspace_id="workspace-1",
        environment="dev",
        executor_id="executor-1",
        clock=clock,
    )


def test_valid_approval_executes_exactly_once_and_audits_full_lifecycle():
    current = TrustedPlan(
        [{"cluster_id": "cluster-1", "action": "terminate"}],
        [{"cluster_id": "cluster-1", "action": "terminate"}],
    )
    store = FakeStore(make_action(current))
    applied: list = []
    executor = build_executor(store, current, applied)

    result = executor.execute("action-1")
    replay = executor.execute("action-1")

    assert result["status"] == STATUS_SUCCEEDED
    assert replay["idempotent_replay"] is True
    assert len(applied) == 1
    assert store.transitions == [
        ("APPROVED", "EXECUTING"),
        ("EXECUTING", "VERIFYING"),
        ("VERIFYING", "SUCCEEDED"),
    ]
    assert store.events == [
        ("MUTATION_INTENT", "EXECUTING"),
        ("IMPACT_MEASUREMENT", "VERIFYING"),
    ]
    assert result["impact_measurement"]["follow_up"]["status"] == (
        "PENDING_OBSERVATION_WINDOW"
    )


def test_mutation_intent_audit_failure_prevents_external_change():
    current = TrustedPlan(
        [{"cluster_id": "cluster-1", "action": "terminate"}],
        [{"cluster_id": "cluster-1", "action": "terminate"}],
    )
    store = FakeStore(make_action(current))
    store.event_writes_fail = True
    applied: list = []
    executor = build_executor(store, current, applied)

    with pytest.raises(AuditStorageUnavailableError, match="audit append failed"):
        executor.execute("action-1")

    assert applied == []
    assert store.action.status == STATUS_FAILED


def test_missing_audit_storage_fails_before_planning_or_mutation():
    current = TrustedPlan([], [])
    store = FakeStore(make_action(current))
    store.ready = False
    applied: list = []
    executor = build_executor(store, current, applied)

    with pytest.raises(AuditStorageUnavailableError):
        executor.execute("action-1")
    assert applied == []
    assert store.transitions == []


def test_missing_matching_approval_fails_without_mutation():
    current = TrustedPlan([], [])
    store = FakeStore(make_action(current), approved=False)
    applied: list = []
    executor = build_executor(store, current, applied)

    with pytest.raises(ActionExecutionError, match="No durable authorized approval"):
        executor.execute("action-1")
    assert applied == []
    assert store.transitions == []


def test_resource_or_target_drift_marks_plan_stale_without_mutation():
    approved = TrustedPlan(
        [{"cluster_id": "cluster-1", "action": "terminate"}],
        [{"cluster_id": "cluster-1", "action": "terminate"}],
    )
    changed = TrustedPlan(
        [{"cluster_id": "cluster-1", "action": "review"}],
        [{"cluster_id": "cluster-1", "action": "review"}],
    )
    store = FakeStore(make_action(approved))
    applied: list = []
    executor = build_executor(store, changed, applied)

    with pytest.raises(StaleActionError):
        executor.execute("action-1")
    assert applied == []
    assert store.transitions == [("APPROVED", "STALE")]


def test_handler_detected_drift_marks_plan_stale_without_mutation():
    current = TrustedPlan([], [])
    store = FakeStore(make_action(current))
    handler = ActionHandler(
        plan=lambda: (_ for _ in ()).throw(StaleActionError("job definition changed")),
        apply=lambda payload: pytest.fail("mutation must not run"),
        verify=lambda payload, result: pytest.fail("verification must not run"),
    )
    executor = GovernedActionExecutor(
        store,
        {"stale-clusters": handler},
        workspace_id="workspace-1",
        environment="dev",
        executor_id="executor-1",
        clock=lambda: datetime(2026, 7, 17, 12, 1, tzinfo=UTC),
    )

    with pytest.raises(StaleActionError, match="job definition changed"):
        executor.execute("action-1")

    assert store.transitions == [("APPROVED", "STALE")]


def test_exact_restoration_records_rolled_back_terminal_state():
    current = TrustedPlan([], [])
    store = FakeStore(make_action(current))
    handler = ActionHandler(
        plan=lambda: current,
        apply=lambda payload: (_ for _ in ()).throw(
            ActionRolledBackError("captured state restored")
        ),
        verify=lambda payload, result: pytest.fail("verification must not run"),
    )
    executor = GovernedActionExecutor(
        store,
        {"stale-clusters": handler},
        workspace_id="workspace-1",
        environment="dev",
        executor_id="executor-1",
        clock=lambda: datetime(2026, 7, 17, 12, 1, tzinfo=UTC),
    )

    with pytest.raises(ActionRolledBackError, match="captured state restored"):
        executor.execute("action-1")

    assert store.transitions == [
        ("APPROVED", "EXECUTING"),
        ("EXECUTING", "ROLLED_BACK"),
    ]


def test_expired_plan_becomes_terminal_without_mutation():
    current = TrustedPlan([], [])
    expiry = datetime(2026, 7, 17, 11, 59, tzinfo=UTC)
    store = FakeStore(make_action(current, expires_at=expiry))
    applied: list = []
    executor = build_executor(store, current, applied)

    with pytest.raises(ActionExecutionError, match="expired"):
        executor.execute("action-1")
    assert applied == []
    assert store.transitions == [("APPROVED", "EXPIRED")]


def test_modified_immutable_payload_is_rejected_without_mutation():
    current = TrustedPlan([], [])
    action = make_action(current)
    action.plan["targets"].append({"cluster_id": "attacker"})
    store = FakeStore(action)
    applied: list = []
    executor = build_executor(store, current, applied)

    with pytest.raises(ActionExecutionError, match="SHA-256"):
        executor.execute("action-1")
    assert applied == []
    assert store.transitions == []


def test_mutable_index_cannot_extend_immutable_plan_ttl():
    current = TrustedPlan([], [])
    action = make_action(current)
    store = FakeStore(
        replace(action, expires_at=action.expires_at + timedelta(days=30))
    )
    applied: list = []
    executor = build_executor(store, current, applied)

    with pytest.raises(ActionExecutionError, match="Indexed plan expiry differs"):
        executor.execute("action-1")
    assert applied == []
    assert store.transitions == []


def test_executor_revalidates_current_approver_membership():
    current = TrustedPlan([], [])
    store = FakeStore(make_action(current))
    applied: list = []
    handler = ActionHandler(
        plan=lambda: current,
        apply=lambda payload: applied.append(payload) or {},
        verify=lambda payload, result: {"verified": True},
    )
    executor = GovernedActionExecutor(
        store,
        {"stale-clusters": handler},
        workspace_id="workspace-1",
        environment="dev",
        executor_id="executor-1",
        approval_validator=lambda _approval: False,
        clock=lambda: datetime(2026, 7, 17, 12, 1, tzinfo=UTC),
    )
    with pytest.raises(ActionExecutionError, match="group membership"):
        executor.execute("action-1")
    assert applied == []


def test_workspace_approver_revalidation_uses_resolved_user_memberships():
    approval = StoredApproval(
        approval_id="approval-1",
        approver_id="approver-1",
        approver_email="approver@example.com",
        approver_role="approver",
        confirmation="apply run-job 1",
    )
    workspace = MagicMock()
    workspace.users.get.return_value = SimpleNamespace(
        id="approver-1",
        user_name="approver@example.com",
        active=True,
        groups=[
            SimpleNamespace(display="dbx-platform-approvers"),
            SimpleNamespace(display="users"),
        ],
    )

    assert action_executor._approver_is_current_member(
        workspace,
        approval,
        "dbx-platform-approvers",
        "group-1",
    )
    workspace.groups.list.assert_not_called()
    workspace.groups.get.assert_not_called()


def test_workspace_approver_revalidation_rejects_absent_group_or_identity_drift():
    approval = StoredApproval(
        approval_id="approval-1",
        approver_id="approver-1",
        approver_email="approver@example.com",
        approver_role="approver",
        confirmation="apply run-job 1",
    )
    workspace = MagicMock()
    workspace.users.get.return_value = SimpleNamespace(
        id="approver-1",
        user_name="approver@example.com",
        active=True,
        groups=[SimpleNamespace(display="users")],
    )
    assert not action_executor._approver_is_current_member(
        workspace,
        approval,
        "dbx-platform-approvers",
        "group-1",
    )

    workspace.users.get.return_value.user_name = "other@example.com"
    workspace.users.get.return_value.groups = [
        SimpleNamespace(display="dbx-platform-approvers")
    ]
    assert not action_executor._approver_is_current_member(
        workspace,
        approval,
        "dbx-platform-approvers",
        "group-1",
    )


def test_workspace_approver_revalidation_accepts_exact_group_id_without_display():
    approval = StoredApproval(
        approval_id="approval-1",
        approver_id="approver-1",
        approver_email="approver@example.com",
        approver_role="approver",
        confirmation="apply run-job 1",
    )
    workspace = MagicMock()
    workspace.users.get.return_value = SimpleNamespace(
        id="approver-1",
        user_name="approver@example.com",
        active=True,
        groups=[SimpleNamespace(display=None, value="group-1")],
    )

    assert action_executor._approver_is_current_member(
        workspace,
        approval,
        "dbx-platform-approvers",
        "group-1",
    )


def test_workspace_approver_revalidation_rejects_empty_group_id():
    approval = StoredApproval(
        approval_id="approval-1",
        approver_id="approver-1",
        approver_email="approver@example.com",
        approver_role="approver",
        confirmation="apply run-job 1",
    )
    workspace = MagicMock()
    workspace.users.get.return_value = SimpleNamespace(
        id="approver-1",
        user_name="approver@example.com",
        active=True,
        groups=[SimpleNamespace(display="users", value=None)],
    )

    assert not action_executor._approver_is_current_member(
        workspace,
        approval,
        "dbx-platform-approvers",
        "",
    )
    workspace.users.get.assert_not_called()


def test_executor_accepts_approval_without_typed_confirmation():
    current = TrustedPlan([], [])
    store = FakeStore(make_action(current))
    original = store.get_matching_approval
    store.get_matching_approval = lambda action_id, plan_hash: replace(
        original(action_id, plan_hash),
        confirmation="",
    )
    applied: list = []
    executor = build_executor(store, current, applied)
    result = executor.execute("action-1")
    assert result["status"] == STATUS_SUCCEEDED
    assert len(applied) == 1


def test_executor_fails_closed_instead_of_reapplying_interrupted_mutation():
    current = TrustedPlan([], [])
    action = replace(make_action(current), status="EXECUTING")
    store = FakeStore(action)
    applied: list = []
    executor = build_executor(store, current, applied)

    with pytest.raises(ActionExecutionError, match="cannot be resumed"):
        executor.execute("action-1")

    assert applied == []
    assert store.action.status == STATUS_FAILED


def test_executor_recovers_executing_action_from_durable_result_checkpoint():
    current = TrustedPlan([], [])
    action = replace(make_action(current), status="EXECUTING")
    store = FakeStore(action)
    store.verification_checkpoint = {
        "checkpoint": "MUTATION_APPLIED",
        "result": {"changed": 1},
    }
    applied: list = []
    executor = build_executor(store, current, applied)

    result = executor.execute("action-1")

    assert applied == []
    assert result["status"] == STATUS_SUCCEEDED
    assert result["verification_resumed"] is True
    assert store.action.status == STATUS_SUCCEEDED
    assert ("EXECUTING", "VERIFYING") in store.transitions


def test_executor_resumes_read_only_verification_from_durable_result_checkpoint():
    current = TrustedPlan([], [])
    action = replace(make_action(current), status="VERIFYING")
    store = FakeStore(action)
    store.verification_checkpoint = {
        "checkpoint": "MUTATION_APPLIED",
        "result": {"changed": 1},
    }
    applied: list = []
    executor = build_executor(store, current, applied)

    result = executor.execute("action-1")

    assert applied == []
    assert result["status"] == STATUS_SUCCEEDED
    assert result["verification_resumed"] is True
    assert store.action.status == STATUS_SUCCEEDED


def test_executor_fails_unknown_verifying_outcome_without_result_checkpoint():
    current = TrustedPlan([], [])
    action = replace(make_action(current), status="VERIFYING")
    store = FakeStore(action)
    applied: list = []
    executor = build_executor(store, current, applied)

    with pytest.raises(ActionExecutionError, match="no durable mutation-result"):
        executor.execute("action-1")

    assert applied == []
    assert store.action.status == STATUS_FAILED


def _run_job_executor(result_state: str, *, governed: bool = True):
    workspace = MagicMock()
    settings = MagicMock()
    settings.name = "[dbx-platform] cost-forecast-train"
    settings.as_dict.return_value = {
        "name": settings.name,
        "tasks": [{"task_key": "train_and_gate"}],
    }
    job_state = {
        "job_id": 91,
        "settings": settings.as_dict(),
        "run_as_user_name": "executor",
    }
    target = {
        "resource_type": "JOB",
        "resource_id": "91",
        "job_id": 91,
        "name": settings.name,
        "action": "RUN_NOW",
        "settings_sha256": canonical_hash(job_state),
        "job_state": job_state,
    }
    trusted = TrustedPlan([target], {"job_id": 91})
    action = make_action(trusted, action_type="run-job")
    current_job = SimpleNamespace(
        settings=settings,
        run_as_user_name="executor",
    )
    workspace.jobs.get.return_value = current_job
    workspace.jobs.run_now.return_value = SimpleNamespace(run_id=7001)
    workspace.jobs.wait_get_run_job_terminated_or_skipped.return_value = (
        SimpleNamespace(
            run_id=7001,
            job_id=91,
            state=SimpleNamespace(
                life_cycle_state="TERMINATED",
                result_state=result_state,
                state_message="finished",
            ),
        )
    )
    handlers = bind_action_handler(
        action,
        build_handlers(workspace, Settings()),
        workspace,
        governed_job_ids=frozenset({91}) if governed else frozenset(),
    )
    store = FakeStore(action)
    executor = GovernedActionExecutor(
        store,
        handlers,
        workspace_id="workspace-1",
        environment="dev",
        executor_id="executor-1",
        clock=lambda: datetime(2026, 7, 17, 12, 1, tzinfo=UTC),
    )
    return executor, store, workspace


def test_run_job_succeeds_only_after_exact_child_run_terminates_successfully():
    executor, store, workspace = _run_job_executor("SUCCESS")

    result = executor.execute("action-1")

    assert result["status"] == STATUS_SUCCEEDED
    assert result["verification"]["result_state"] == "SUCCESS"
    assert store.transitions[-1] == ("VERIFYING", "SUCCEEDED")
    workspace.jobs.wait_get_run_job_terminated_or_skipped.assert_called_once()


def test_run_job_failure_is_recorded_as_failed_action():
    executor, store, _workspace = _run_job_executor("FAILED")

    with pytest.raises(ActionExecutionError, match="did not terminate with SUCCESS"):
        executor.execute("action-1")

    assert store.transitions[-1] == ("VERIFYING", "FAILED")


def test_run_job_rejects_bundle_named_job_outside_exact_id_allowlist():
    executor, store, workspace = _run_job_executor("SUCCESS", governed=False)

    with pytest.raises(StaleActionError, match="bundle-bound allowlist"):
        executor.execute("action-1")

    assert store.action.status == "STALE"
    workspace.jobs.run_now.assert_not_called()


class _BudgetRow:
    def __init__(self, value):
        self.value = value

    def asDict(self, recursive=True):
        assert recursive is True
        return dict(self.value)


class _BudgetSpark:
    def __init__(self, desired):
        self.desired = desired
        self.row = None
        self.plan_hash = ""
        self.statements: list[str] = []

    def sql(self, statement: str):
        self.statements.append(statement)
        if "MERGE INTO" in statement:
            self.row = {
                **self.desired,
                "plan_hash": self.plan_hash,
                "updated_by": "executor-1",
                "updated_at": datetime(2026, 7, 17, 12, 2, tzinfo=UTC),
            }
            return self
        return self

    def collect(self):
        return [_BudgetRow(self.row)] if self.row else []


def test_configure_budget_executes_exact_upsert_and_replay_is_idempotent():
    desired = {
        "budget_id": "budget-team-platform",
        "workspace_id": "workspace-1",
        "environment": "dev",
        "scope_type": "team",
        "scope_value": "platform",
        "cost_basis": "AZURE_ACTUAL",
        "month": "2026-07-01",
        "currency": "USD",
        "amount": 1200.0,
        "warning_pct": 80,
        "critical_pct": 100,
        "status": "ACTIVE",
    }
    payload = {
        "operation": "UPSERT_LLM_BUDGET",
        "budget_id": desired["budget_id"],
        "expected_before": None,
        "desired": desired,
    }
    target = {
        "resource_type": "LLM_BUDGET",
        "resource_id": desired["budget_id"],
        "budget_id": desired["budget_id"],
    }
    current = TrustedPlan([target], payload)
    action = make_action(current, action_type="configure-budget")
    spark = _BudgetSpark(desired)
    spark.plan_hash = action.plan_hash
    handlers = bind_budget_handler(
        action,
        {},
        spark,
        catalog="main",
        schema="dbx_platform",
        executor_id="executor-1",
    )
    store = FakeStore(action)
    executor = GovernedActionExecutor(
        store,
        handlers,
        workspace_id="workspace-1",
        environment="dev",
        executor_id="executor-1",
        clock=lambda: datetime(2026, 7, 17, 12, 1, tzinfo=UTC),
    )

    result = executor.execute("action-1")
    replay = executor.execute("action-1")

    assert result["status"] == STATUS_SUCCEEDED
    assert result["verification"]["verified"] is True
    assert replay["idempotent_replay"] is True
    assert sum("MERGE INTO" in statement for statement in spark.statements) == 1
