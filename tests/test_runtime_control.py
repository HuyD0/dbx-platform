"""Pure tests for the exact-ID, human-approved runtime controller."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from databricks.sdk.service import jobs, sql

from dbx_platform.runtime_control import (
    ACTION_HIBERNATE,
    ACTION_WAKE,
    STATUS_APPROVED,
    STATUS_AWAITING_APPROVAL,
    STATUS_EXECUTING,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_REJECTED,
    STATUS_ROLLED_BACK,
    STATUS_STALE,
    STATUS_SUCCEEDED,
    STATUS_VERIFYING,
    ActionRecord,
    Actor,
    ApprovalEvidence,
    ApprovalRequiredError,
    DatabricksRuntimeAdapter,
    DrainTimeoutError,
    InventoryError,
    ManagedResource,
    ResourceKind,
    RuntimeController,
    RuntimeInventory,
    RuntimeState,
    SparkSqlActionStore,
    StalePlanError,
    _parse_inventory,
    canonical_hash,
    canonical_json,
)

NOW = datetime(2026, 7, 17, 16, 0, tzinfo=UTC)
APPROVER = Actor("user-1", "owner@example.com", ("dbx-platform-approvers",))


def inventory() -> RuntimeInventory:
    return RuntimeInventory(
        (
            ManagedResource(
                "cost_usage_report",
                ResourceKind.JOB,
                "101",
                "cost-usage-report",
                10,
            ),
            ManagedResource(
                "security_audit",
                ResourceKind.JOB,
                "102",
                "security-audit",
                11,
            ),
            ManagedResource(
                "platform_console_warehouse",
                ResourceKind.WAREHOUSE,
                "wh-1",
                "mission-control",
                100,
            ),
            ManagedResource(
                "platform_console",
                ResourceKind.APP,
                "platform-console",
                "platform-console",
                200,
            ),
        )
    )


class MemoryStore:
    def __init__(self) -> None:
        self.actions: dict[str, ActionRecord] = {}
        self.events: list[dict[str, Any]] = []
        self.runtime: RuntimeState | None = None
        self.approvals: dict[tuple[str, str], ApprovalEvidence] = {}
        self.inventory_updates = 0
        self.schema_verified = False

    def verify_schema(self, _actor_id: str, _verified_at: datetime) -> None:
        self.schema_verified = True

    def upsert_inventory(self, *_args: Any, **_kwargs: Any) -> None:
        self.inventory_updates += 1

    def create_action(
        self, plan: dict[str, Any], plan_hash: str, confirm_phrase: str
    ) -> None:
        action_id = plan["action_id"]
        self.actions[action_id] = ActionRecord(
            action_id,
            plan["action_type"],
            STATUS_AWAITING_APPROVAL,
            plan,
            plan_hash,
            confirm_phrase,
            datetime.fromisoformat(plan["expires_at"].replace("Z", "+00:00")),
        )

    def get_action(self, action_id: str) -> ActionRecord | None:
        return self.actions.get(action_id)

    def get_matching_approval(
        self, action_id: str, plan_hash: str
    ) -> ApprovalEvidence | None:
        return self.approvals.get((action_id, plan_hash))

    def approve_action(
        self,
        action_id: str,
        plan_hash: str,
        actor: Actor,
        confirmation: str,
        decided_at: datetime,
    ) -> None:
        current = self.actions[action_id]
        assert current.status == STATUS_AWAITING_APPROVAL
        assert current.plan_hash == plan_hash
        self.actions[action_id] = replace(current, status=STATUS_APPROVED)
        self.approvals[(action_id, plan_hash)] = ApprovalEvidence(
            approval_id="approval-1",
            action_id=action_id,
            plan_hash=plan_hash,
            approver_id=actor.actor_id,
            approver_email=actor.email,
            approver_role=",".join(actor.roles),
            confirmation=confirmation,
            decided_at=decided_at,
        )
        self.events.append(
            {
                "action_id": action_id,
                "type": "APPROVED",
                "actor": actor.actor_id,
                "confirmation": confirmation,
                "at": decided_at,
            }
        )

    def transition(
        self,
        action_id: str,
        allowed_from: set[str],
        to_status: str,
        actor_id: str,
        details: dict[str, Any],
        event_at: datetime,
    ) -> None:
        current = self.actions[action_id]
        assert current.status in allowed_from
        self.actions[action_id] = replace(current, status=to_status)
        self.events.append(
            {
                "action_id": action_id,
                "type": "STATUS_CHANGED",
                "from": current.status,
                "to": to_status,
                "actor": actor_id,
                "details": details,
                "at": event_at,
            }
        )

    def append_event(
        self,
        action_id: str,
        event_type: str,
        actor_id: str,
        details: dict[str, Any],
        event_at: datetime,
    ) -> None:
        self.events.append(
            {
                "action_id": action_id,
                "type": event_type,
                "actor": actor_id,
                "details": details,
                "at": event_at,
            }
        )

    def get_runtime_state(
        self, _workspace_id: str, _environment: str
    ) -> RuntimeState | None:
        return self.runtime

    def save_runtime_state(self, state: RuntimeState) -> None:
        self.runtime = state


class FakeRuntimeAdapter:
    def __init__(self) -> None:
        self.resources: dict[str, dict[str, Any]] = {
            "cost_usage_report": {
                "resource_key": "cost_usage_report",
                "resource_type": "JOB",
                "resource_id": "101",
                "state": "UNPAUSED",
                "config_hash": "job-101-v1",
                "config": {},
            },
            "security_audit": {
                "resource_key": "security_audit",
                "resource_type": "JOB",
                "resource_id": "102",
                "state": "PAUSED",
                "config_hash": "job-102-v1",
                "config": {},
            },
            "platform_console_warehouse": {
                "resource_key": "platform_console_warehouse",
                "resource_type": "WAREHOUSE",
                "resource_id": "wh-1",
                "state": "RUNNING",
                "config_hash": "warehouse-v1",
                "config": {},
            },
            "platform_console": {
                "resource_key": "platform_console",
                "resource_type": "APP",
                "resource_id": "platform-console",
                "state": "ACTIVE",
                "config_hash": "app-v1",
                "config": {},
                "health": {
                    "compute_state": "ACTIVE",
                    "application_state": "RUNNING",
                    "deployment_state": "SUCCEEDED",
                    "running_instances": 1,
                },
            },
        }
        self.calls: list[tuple[Any, ...]] = []
        self.observe_count = 0
        self.active_results: list[list[dict[str, Any]]] = []
        self.active_query_results: list[list[dict[str, Any]]] = []

    def observe(self, resource: ManagedResource) -> dict[str, Any]:
        self.observe_count += 1
        return dict(self.resources[resource.resource_key])

    def set_job_paused(self, job_id: str, paused: bool) -> None:
        resource = next(
            value
            for value in self.resources.values()
            if value["resource_type"] == "JOB" and value["resource_id"] == job_id
        )
        desired = "PAUSED" if paused else "UNPAUSED"
        if resource["state"] != desired:
            self.calls.append(("set_job_paused", job_id, paused))
            resource["state"] = desired

    def active_job_runs(self, job_ids: list[str]) -> list[dict[str, Any]]:
        self.calls.append(("active_job_runs", tuple(job_ids)))
        return self.active_results.pop(0) if self.active_results else []

    def active_queries(self, warehouse_id: str) -> list[dict[str, Any]]:
        self.calls.append(("active_queries", warehouse_id))
        return (
            self.active_query_results.pop(0)
            if self.active_query_results
            else []
        )

    def stop_warehouse(self, warehouse_id: str) -> None:
        resource = self.resources["platform_console_warehouse"]
        if resource["state"] != "STOPPED":
            self.calls.append(("stop_warehouse", warehouse_id))
            resource["state"] = "STOPPED"

    def start_warehouse(self, warehouse_id: str) -> None:
        resource = self.resources["platform_console_warehouse"]
        if resource["state"] != "RUNNING":
            self.calls.append(("start_warehouse", warehouse_id))
            resource["state"] = "RUNNING"

    def stop_app(self, app_name: str) -> None:
        resource = self.resources["platform_console"]
        if resource["state"] != "STOPPED":
            self.calls.append(("stop_app", app_name))
            resource["state"] = "STOPPED"

    def start_app(self, app_name: str) -> None:
        resource = self.resources["platform_console"]
        if resource["state"] != "ACTIVE":
            self.calls.append(("start_app", app_name))
            resource["state"] = "ACTIVE"
        resource["health"] = {
            "compute_state": "ACTIVE",
            "application_state": "RUNNING",
            "deployment_state": "SUCCEEDED",
            "running_instances": 1,
        }


class MutableTime:
    def __init__(self) -> None:
        self.wall = NOW
        self.monotonic_value = 0.0

    def clock(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.monotonic_value

    def sleep(self, seconds: float) -> None:
        self.monotonic_value += seconds
        self.wall += timedelta(seconds=seconds)


class FakeApproverVerifier:
    def __init__(self) -> None:
        self.active = True
        self.roles = ("dbx-platform-approvers",)
        self.missing = False
        self.calls: list[tuple[str, str]] = []

    def actor_for_run(self, _run_id: int, _required_group: str) -> Actor:
        return APPROVER

    def actor_for_approval(
        self, approval: ApprovalEvidence, required_group: str
    ) -> Actor:
        self.calls.append((approval.approver_id, required_group))
        if self.missing:
            raise ApprovalRequiredError("Cannot re-resolve durable approver")
        if not self.active:
            raise ApprovalRequiredError("approving user is no longer active")
        if required_group not in self.roles:
            raise ApprovalRequiredError("not a member of required group")
        return Actor(
            approval.approver_id,
            approval.approver_email or "owner@example.com",
            self.roles,
        )


def controller(
    adapter: FakeRuntimeAdapter | None = None,
    store: MemoryStore | None = None,
    timer: MutableTime | None = None,
    approver_verifier: FakeApproverVerifier | None = None,
    *,
    drain_timeout_seconds: int = 900,
) -> tuple[RuntimeController, FakeRuntimeAdapter, MemoryStore, MutableTime]:
    adapter = adapter or FakeRuntimeAdapter()
    store = store or MemoryStore()
    timer = timer or MutableTime()
    approver_verifier = approver_verifier or FakeApproverVerifier()
    value = RuntimeController(
        adapter,
        store,
        inventory(),
        "123456",
        "prod",
        approver_verifier,
        clock=timer.clock,
        monotonic=timer.monotonic,
        sleeper=timer.sleep,
        drain_timeout_seconds=drain_timeout_seconds,
        drain_poll_seconds=5,
    )
    value.initialize()
    return value, adapter, store, timer


def execute_reviewed(
    value: RuntimeController, record: ActionRecord
) -> RuntimeState:
    return value.execute(
        record.action_id,
        record.plan_hash,
        actor=APPROVER,
        confirmation=record.confirm_phrase,
    )


def test_canonical_plan_hash_is_stable_and_strict() -> None:
    left = {"z": [2, 1], "name": "café", "a": {"x": True}}
    right = {"a": {"x": True}, "name": "café", "z": [2, 1]}

    assert canonical_json(left) == canonical_json(right)
    assert canonical_hash(left) == canonical_hash(right)
    with pytest.raises(ValueError):
        canonical_json({"not_a_number": float("nan")})


def test_inventory_requires_unique_exact_ids_and_owned_compute() -> None:
    with pytest.raises(InventoryError, match="IDs must be unique"):
        RuntimeInventory(
            (
                ManagedResource("a", ResourceKind.JOB, "1", "a", 1),
                ManagedResource("b", ResourceKind.JOB, "1", "b", 2),
                ManagedResource("w", ResourceKind.WAREHOUSE, "w", "w", 3),
                ManagedResource("app", ResourceKind.APP, "app", "app", 4),
            )
        )
    with pytest.raises(InventoryError, match="dedicated warehouse"):
        RuntimeInventory(
            (ManagedResource("app", ResourceKind.APP, "app", "app", 1),)
        )


def test_plan_is_read_only_exact_hashed_and_expires_in_15_minutes() -> None:
    value, adapter, store, _ = controller()
    adapter.calls.clear()

    record = value.plan_hibernate(APPROVER)

    assert adapter.calls == [
        ("active_job_runs", ("101", "102")),
        ("active_queries", "wh-1"),
    ]
    assert record.status == STATUS_AWAITING_APPROVAL
    assert record.action_type == ACTION_HIBERNATE
    assert record.expires_at == NOW + timedelta(minutes=15)
    assert canonical_hash(record.plan) == record.plan_hash
    assert record.plan["parameters"]["force_cancel"] is False
    assert record.plan["impact"]["active_run_count_at_plan_time"] == 0
    assert record.plan["impact"]["active_query_count_at_plan_time"] == 0
    assert record.plan["impact"]["estimated_idle_savings"]["status"] == "UNAVAILABLE"
    assert "power-controller" in record.plan["impact"]["exclusions"]
    assert record.plan["impact"]["wake_procedure"]
    assert {target["resource_id"] for target in record.plan["targets"]} == {
        "101",
        "102",
        "wh-1",
        "platform-console",
    }
    assert record.confirm_phrase == "apply runtime.hibernate 4"
    assert store.schema_verified
    assert store.inventory_updates == 1


def test_planning_refuses_unknown_state_that_cannot_be_rolled_back() -> None:
    value, adapter, store, _ = controller()
    adapter.resources["platform_console"]["state"] = "STARTING"

    with pytest.raises(StalePlanError, match="transitional or unknown"):
        value.plan_hibernate(APPROVER)

    assert store.actions == {}
    assert all(call[0] not in {"stop_app", "stop_warehouse"} for call in adapter.calls)


def test_missing_audit_storage_fails_before_observing_or_mutating_targets() -> None:
    adapter = FakeRuntimeAdapter()
    store = MemoryStore()
    timer = MutableTime()

    def missing(_actor_id: str, _verified_at: datetime) -> None:
        raise RuntimeError("required control-plane tables are missing")

    store.verify_schema = missing  # type: ignore[method-assign]
    value = RuntimeController(
        adapter,
        store,
        inventory(),
        "123456",
        "prod",
        FakeApproverVerifier(),
        clock=timer.clock,
        monotonic=timer.monotonic,
        sleeper=timer.sleep,
    )

    with pytest.raises(RuntimeError, match="tables are missing"):
        value.initialize()

    assert adapter.observe_count == 0
    assert adapter.calls == []


def test_execution_requires_exact_confirmation_and_hash() -> None:
    value, adapter, _, _ = controller()
    record = value.plan_hibernate(APPROVER)
    adapter.calls.clear()

    with pytest.raises(ApprovalRequiredError, match="authorized human"):
        value.execute(record.action_id, record.plan_hash)
    with pytest.raises(ApprovalRequiredError, match="Confirmation must exactly"):
        value.execute(
            record.action_id,
            record.plan_hash,
            actor=APPROVER,
            confirmation="yes",
        )
    with pytest.raises(ApprovalRequiredError, match="hash"):
        value.execute(
            record.action_id,
            "0" * 64,
            actor=APPROVER,
            confirmation=record.confirm_phrase,
        )
    assert adapter.calls == []


def test_approved_status_without_durable_matching_approval_fails_closed() -> None:
    value, adapter, store, _ = controller()
    record = value.plan_hibernate(APPROVER)
    adapter.calls.clear()
    store.actions[record.action_id] = replace(record, status=STATUS_APPROVED)

    with pytest.raises(ApprovalRequiredError, match="no durable human approval"):
        value.execute(record.action_id, record.plan_hash)

    assert adapter.calls == []


def test_app_approved_execution_revalidates_current_approver_membership() -> None:
    verifier = FakeApproverVerifier()
    value, adapter, store, _ = controller(approver_verifier=verifier)
    record = value.plan_hibernate(APPROVER)
    store.approve_action(
        record.action_id,
        record.plan_hash,
        APPROVER,
        record.confirm_phrase,
        NOW,
    )
    adapter.calls.clear()

    state = value.execute(record.action_id, record.plan_hash)

    assert state.desired_state.value == "SLEEPING"
    assert verifier.calls == [
        (APPROVER.actor_id, "dbx-platform-approvers")
    ]


def test_removed_or_missing_approver_fails_before_managed_mutation() -> None:
    verifier = FakeApproverVerifier()
    value, adapter, store, _ = controller(approver_verifier=verifier)
    record = value.plan_hibernate(APPROVER)
    store.approve_action(
        record.action_id,
        record.plan_hash,
        APPROVER,
        record.confirm_phrase,
        NOW,
    )
    adapter.calls.clear()
    verifier.roles = ()

    with pytest.raises(ApprovalRequiredError, match="required group"):
        value.execute(record.action_id, record.plan_hash)

    assert adapter.calls == []
    verifier.roles = ("dbx-platform-approvers",)
    verifier.missing = True
    with pytest.raises(ApprovalRequiredError, match="re-resolve"):
        value.execute(record.action_id, record.plan_hash)
    assert adapter.calls == []


def test_tampered_approval_role_or_confirmation_fails_closed() -> None:
    value, adapter, store, _ = controller()
    record = value.plan_hibernate(APPROVER)
    store.approve_action(
        record.action_id,
        record.plan_hash,
        APPROVER,
        record.confirm_phrase,
        NOW,
    )
    key = (record.action_id, record.plan_hash)
    approval = store.approvals[key]
    adapter.calls.clear()
    store.approvals[key] = replace(approval, approver_role="viewer")

    with pytest.raises(ApprovalRequiredError, match="approver role"):
        value.execute(record.action_id, record.plan_hash)

    store.approvals[key] = replace(
        approval, confirmation="apply runtime.hibernate 999"
    )
    with pytest.raises(ApprovalRequiredError, match="typed confirmation"):
        value.execute(record.action_id, record.plan_hash)
    assert adapter.calls == []


def test_hibernate_pauses_drains_and_stops_app_last() -> None:
    value, adapter, store, _ = controller()
    record = value.plan_hibernate(APPROVER)
    adapter.calls.clear()
    adapter.active_results = [[{"job_id": "101", "run_id": 7}], []]

    state = execute_reviewed(value, record)

    assert state.desired_state.value == "SLEEPING"
    assert state.actual_state == "SLEEPING"
    assert store.actions[record.action_id].status == STATUS_SUCCEEDED
    assert adapter.calls == [
        ("set_job_paused", "101", True),
        ("active_job_runs", ("101", "102")),
        ("active_queries", "wh-1"),
        ("active_job_runs", ("101", "102")),
        ("active_queries", "wh-1"),
        ("stop_warehouse", "wh-1"),
        ("stop_app", "platform-console"),
    ]
    assert state.prior_state["resources"]["cost_usage_report"]["state"] == "UNPAUSED"
    assert state.prior_state["resources"]["security_audit"]["state"] == "PAUSED"
    measurement = next(
        event for event in store.events if event["type"] == "IMPACT_MEASUREMENT"
    )
    assert measurement["details"]["observed"]["verified_target_count"] == 4
    assert measurement["details"]["follow_up"]["status"] == (
        "PENDING_OBSERVATION_WINDOW"
    )


def test_runtime_mutation_intent_audit_failure_prevents_resource_change() -> None:
    value, adapter, store, _ = controller()
    record = value.plan_hibernate(APPROVER)
    store.approve_action(
        record.action_id,
        record.plan_hash,
        APPROVER,
        record.confirm_phrase,
        NOW,
    )
    adapter.calls.clear()
    original_append = store.append_event

    def fail_mutation_intent(
        action_id: str,
        event_type: str,
        actor_id: str,
        details: dict[str, Any],
        event_at: datetime,
    ) -> None:
        if event_type == "MUTATION_INTENT":
            raise RuntimeError("audit append failed")
        original_append(action_id, event_type, actor_id, details, event_at)

    store.append_event = fail_mutation_intent  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="audit append failed"):
        value.execute(record.action_id, record.plan_hash)

    assert store.actions[record.action_id].status == STATUS_FAILED
    mutators = {
        "set_job_paused",
        "stop_warehouse",
        "start_warehouse",
        "stop_app",
        "start_app",
    }
    assert all(call[0] not in mutators for call in adapter.calls)


def test_config_drift_marks_plan_stale_before_any_mutation() -> None:
    value, adapter, store, _ = controller()
    record = value.plan_hibernate(APPROVER)
    adapter.calls.clear()
    adapter.resources["cost_usage_report"]["config_hash"] = "job-101-v2"

    with pytest.raises(StalePlanError, match="configuration changed"):
        execute_reviewed(value, record)

    assert store.actions[record.action_id].status == STATUS_STALE
    assert adapter.calls == []


def test_state_drift_to_target_is_stale_before_first_execution_claim() -> None:
    value, adapter, store, _ = controller()
    record = value.plan_hibernate(APPROVER)
    adapter.calls.clear()
    adapter.resources["platform_console_warehouse"]["state"] = "STOPPED"

    with pytest.raises(StalePlanError, match="state STOPPED"):
        execute_reviewed(value, record)

    assert store.actions[record.action_id].status == STATUS_STALE
    assert adapter.calls == []


def test_in_progress_retry_accepts_only_partial_progress_toward_target() -> None:
    value, adapter, store, _ = controller()
    record = value.plan_hibernate(APPROVER)
    store.approve_action(
        record.action_id,
        record.plan_hash,
        APPROVER,
        record.confirm_phrase,
        NOW,
    )
    store.transition(
        record.action_id,
        {STATUS_APPROVED},
        STATUS_EXECUTING,
        "runtime-controller",
        {},
        NOW,
    )
    adapter.resources["cost_usage_report"]["state"] = "PAUSED"
    adapter.resources["platform_console_warehouse"]["state"] = "STOPPED"
    adapter.calls.clear()

    state = value.execute(record.action_id, record.plan_hash)

    assert state.desired_state.value == "SLEEPING"
    assert store.actions[record.action_id].status == STATUS_SUCCEEDED
    assert ("stop_app", "platform-console") in adapter.calls


def test_drain_timeout_restores_schedule_and_never_cancels_runs() -> None:
    value, adapter, store, _ = controller(drain_timeout_seconds=5)
    record = value.plan_hibernate(APPROVER)
    adapter.calls.clear()
    adapter.active_results = [
        [{"job_id": "101", "run_id": 7}],
        [{"job_id": "101", "run_id": 7}],
    ]

    with pytest.raises(DrainTimeoutError, match="no runs were cancelled"):
        execute_reviewed(value, record)

    assert store.actions[record.action_id].status == STATUS_ROLLED_BACK
    assert adapter.resources["cost_usage_report"]["state"] == "UNPAUSED"
    assert adapter.resources["security_audit"]["state"] == "PAUSED"
    assert adapter.resources["platform_console_warehouse"]["state"] == "RUNNING"
    assert adapter.resources["platform_console"]["state"] == "ACTIVE"
    assert store.runtime is not None
    assert store.runtime.desired_state.value == "ON"
    assert store.runtime.actual_state == "ON"
    assert all(call[0] != "cancel_run" for call in adapter.calls)
    drain_timeout = next(
        event for event in store.events if event["type"] == "DRAIN_TIMEOUT"
    )
    assert drain_timeout["details"]["active_job_runs"][0]["run_id"] == 7
    assert drain_timeout["details"]["cancellation_attempted"] is False


def test_active_queries_are_previewed_and_drained_without_cancellation() -> None:
    value, adapter, store, _ = controller()
    preview_query = {
        "warehouse_id": "wh-1",
        "query_id": "query-7",
        "status": "RUNNING",
    }
    adapter.active_query_results = [[preview_query]]

    record = value.plan_hibernate(APPROVER)

    assert record.plan["impact"]["active_query_count_at_plan_time"] == 1
    assert record.plan["impact"]["active_queries_at_plan_time"] == [preview_query]
    assert record.plan["verification"][
        "active_queries_must_be_zero_before_compute_stop"
    ] is True

    adapter.calls.clear()
    adapter.active_query_results = [[preview_query], []]
    execute_reviewed(value, record)

    query_checks = [
        call for call in adapter.calls if call[0] == "active_queries"
    ]
    assert query_checks == [
        ("active_queries", "wh-1"),
        ("active_queries", "wh-1"),
    ]
    assert adapter.calls.index(("stop_warehouse", "wh-1")) > max(
        index
        for index, call in enumerate(adapter.calls)
        if call[0] == "active_queries"
    )
    assert all(event["type"] != "QUERY_CANCELLED" for event in store.events)


def test_active_query_timeout_rolls_back_without_stopping_warehouse() -> None:
    value, adapter, store, _ = controller(drain_timeout_seconds=5)
    query = {
        "warehouse_id": "wh-1",
        "query_id": "query-7",
        "status": "RUNNING",
    }
    record = value.plan_hibernate(APPROVER)
    adapter.calls.clear()
    adapter.active_query_results = [[query], [query]]

    with pytest.raises(DrainTimeoutError, match="no queries were cancelled"):
        execute_reviewed(value, record)

    assert store.actions[record.action_id].status == STATUS_ROLLED_BACK
    assert adapter.resources["cost_usage_report"]["state"] == "UNPAUSED"
    assert ("stop_warehouse", "wh-1") not in adapter.calls
    assert all(call[0] != "cancel_query" for call in adapter.calls)
    drain_timeout = next(
        event for event in store.events if event["type"] == "DRAIN_TIMEOUT"
    )
    assert drain_timeout["details"]["active_queries"] == [query]


def test_hibernate_is_idempotent_after_success() -> None:
    value, adapter, _, _ = controller()
    record = value.plan_hibernate(APPROVER)
    execute_reviewed(value, record)
    adapter.calls.clear()

    state = value.execute(record.action_id, record.plan_hash)

    assert state.desired_state.value == "SLEEPING"
    assert adapter.calls == []


def test_wake_restores_only_previously_unpaused_schedules_in_safe_order() -> None:
    value, adapter, store, _ = controller()
    hibernate = value.plan_hibernate(APPROVER)
    execute_reviewed(value, hibernate)
    adapter.calls.clear()

    wake = value.plan_wake(APPROVER)
    assert wake.action_type == ACTION_WAKE
    assert wake.plan["after_state"]["resources"]["cost_usage_report"]["state"] == "UNPAUSED"
    assert wake.plan["after_state"]["resources"]["security_audit"]["state"] == "PAUSED"
    state = execute_reviewed(value, wake)

    assert state.desired_state.value == "ON"
    assert store.actions[wake.action_id].status == STATUS_SUCCEEDED
    assert adapter.calls == [
        ("start_warehouse", "wh-1"),
        ("start_app", "platform-console"),
        ("set_job_paused", "101", False),
    ]
    assert adapter.resources["security_audit"]["state"] == "PAUSED"


def test_first_wake_without_prior_state_preserves_deployed_schedule_states() -> None:
    value, adapter, _, _ = controller()
    adapter.resources["cost_usage_report"]["state"] = "PAUSED"

    wake = value.plan_wake(APPROVER)

    assert wake.plan["after_state"]["resources"]["cost_usage_report"]["state"] == "PAUSED"
    assert wake.plan["after_state"]["resources"]["security_audit"]["state"] == "PAUSED"


def test_bundle_inventory_bootstraps_schedules_only_through_approved_wake() -> None:
    adapter = FakeRuntimeAdapter()
    adapter.resources["cost_usage_report"]["state"] = "PAUSED"
    adapter.resources["security_audit"]["state"] = "PAUSED"
    adapter.resources["platform_console_warehouse"]["state"] = "STOPPED"
    adapter.resources["platform_console"]["state"] = "STOPPED"
    store = MemoryStore()
    timer = MutableTime()
    bundle_inventory = _parse_inventory(
        ["cost_usage_report=101", "security_audit=102"],
        "wh-1",
        "platform-console",
    )
    value = RuntimeController(
        adapter,
        store,
        bundle_inventory,
        "123456",
        "prod",
        FakeApproverVerifier(),
        clock=timer.clock,
        monotonic=timer.monotonic,
        sleeper=timer.sleep,
    )
    value.initialize()
    adapter.calls.clear()

    wake = value.plan_reconciliation(APPROVER)

    assert wake is not None
    assert wake.action_type == ACTION_WAKE
    assert adapter.calls == []
    assert wake.plan["before_state"]["resources"]["cost_usage_report"]["state"] == "PAUSED"
    assert wake.plan["after_state"]["resources"]["cost_usage_report"]["state"] == "UNPAUSED"
    assert wake.plan["after_state"]["resources"]["security_audit"]["state"] == "UNPAUSED"

    state = execute_reviewed(value, wake)

    assert state.desired_state.value == "ON"
    assert adapter.resources["cost_usage_report"]["state"] == "UNPAUSED"
    assert adapter.resources["security_audit"]["state"] == "UNPAUSED"


def test_expired_plan_fails_closed_without_resource_changes() -> None:
    value, adapter, store, timer = controller()
    record = value.plan_hibernate(APPROVER)
    adapter.calls.clear()
    timer.wall += timedelta(minutes=15)

    with pytest.raises(ApprovalRequiredError, match="expired"):
        execute_reviewed(value, record)

    assert store.actions[record.action_id].status == STATUS_EXPIRED
    assert adapter.calls == []


def test_wake_does_not_restore_schedules_until_app_process_is_healthy() -> None:
    value, adapter, store, _ = controller()
    hibernate = value.plan_hibernate(APPROVER)
    execute_reviewed(value, hibernate)
    wake = value.plan_wake(APPROVER)

    def start_broken_app(_app_name: str) -> None:
        resource = adapter.resources["platform_console"]
        resource["state"] = "ACTIVE"
        resource["health"] = {
            "compute_state": "ACTIVE",
            "application_state": "UNAVAILABLE",
            "deployment_state": "SUCCEEDED",
            "running_instances": 0,
        }

    adapter.start_app = start_broken_app

    with pytest.raises(RuntimeError, match="health checks"):
        execute_reviewed(value, wake)

    assert adapter.resources["cost_usage_report"]["state"] == "PAUSED"
    assert store.actions[wake.action_id].status == STATUS_ROLLED_BACK


def test_wake_polls_bounded_app_readiness_before_restoring_schedules() -> None:
    value, adapter, store, timer = controller()
    hibernate = value.plan_hibernate(APPROVER)
    execute_reviewed(value, hibernate)
    wake = value.plan_wake(APPROVER)
    original_observe = adapter.observe
    app_health_polls = 0

    def start_delayed_app(_app_name: str) -> None:
        resource = adapter.resources["platform_console"]
        resource["state"] = "ACTIVE"
        resource["health"] = {
            "compute_state": "ACTIVE",
            "application_state": "STARTING",
            "deployment_state": "UPDATING",
            "running_instances": 0,
        }

    def observe_with_delayed_health(resource: ManagedResource) -> dict[str, Any]:
        nonlocal app_health_polls
        if (
            resource.resource_key == "platform_console"
            and adapter.resources["platform_console"]["state"] == "ACTIVE"
            and adapter.resources["platform_console"]["health"]["application_state"]
            != "RUNNING"
        ):
            app_health_polls += 1
            if app_health_polls == 3:
                adapter.resources["platform_console"]["health"] = {
                    "compute_state": "ACTIVE",
                    "application_state": "RUNNING",
                    "deployment_state": "SUCCEEDED",
                    "running_instances": 1,
                }
        return original_observe(resource)

    adapter.start_app = start_delayed_app
    adapter.observe = observe_with_delayed_health
    adapter.calls.clear()

    state = execute_reviewed(value, wake)

    assert state.desired_state.value == "ON"
    assert store.actions[wake.action_id].status == STATUS_SUCCEEDED
    assert timer.monotonic_value == 10
    assert ("set_job_paused", "101", False) in adapter.calls
    healthy = next(event for event in store.events if event["type"] == "APP_HEALTHY")
    assert healthy["details"]["attempts"] == 3


def test_tampered_durable_payload_fails_hash_check() -> None:
    value, adapter, store, _ = controller()
    record = value.plan_hibernate(APPROVER)
    adapter.calls.clear()
    tampered = dict(record.plan)
    tampered["after_state"] = {"desired_state": "ON", "resources": {}}
    store.actions[record.action_id] = replace(record, plan=tampered)

    with pytest.raises(ApprovalRequiredError, match="payload"):
        execute_reviewed(value, record)

    assert adapter.calls == []


def test_tampered_indexed_confirmation_fails_even_when_plan_hash_is_intact() -> None:
    value, adapter, store, _ = controller()
    record = value.plan_hibernate(APPROVER)
    adapter.calls.clear()
    store.actions[record.action_id] = replace(record, confirm_phrase="yes")

    with pytest.raises(ApprovalRequiredError, match="Indexed confirmation phrase"):
        value.execute(
            record.action_id,
            record.plan_hash,
            actor=APPROVER,
            confirmation="yes",
        )

    assert adapter.calls == []


def test_terminal_statuses_are_not_accidentally_accepted() -> None:
    assert {
        STATUS_REJECTED,
        STATUS_STALE,
        STATUS_FAILED,
        STATUS_ROLLED_BACK,
        STATUS_EXPIRED,
        STATUS_EXECUTING,
        STATUS_VERIFYING,
    }.isdisjoint({STATUS_APPROVED, STATUS_SUCCEEDED})


def test_sdk_adapter_updates_only_exact_job_schedule_and_preserves_cron() -> None:
    workspace = MagicMock()
    workspace.jobs.get.return_value = jobs.Job(
        job_id=101,
        settings=jobs.JobSettings(
            name="[dbx-platform] cost-usage-report",
            schedule=jobs.CronSchedule(
                quartz_cron_expression="0 0 7 * * ?",
                timezone_id="UTC",
                pause_status=jobs.PauseStatus.UNPAUSED,
            ),
        ),
    )
    adapter = DatabricksRuntimeAdapter(workspace)

    adapter.set_job_paused("101", True)

    workspace.jobs.get.assert_called_once_with(101)
    workspace.jobs.update.assert_called_once()
    called_job_id = workspace.jobs.update.call_args.args[0]
    update = workspace.jobs.update.call_args.kwargs["new_settings"]
    assert called_job_id == 101
    assert update.schedule.quartz_cron_expression == "0 0 7 * * ?"
    assert update.schedule.timezone_id == "UTC"
    assert update.schedule.pause_status == jobs.PauseStatus.PAUSED
    assert not workspace.jobs.cancel_run.called
    assert not workspace.jobs.cancel_all_runs.called


def test_sdk_adapter_lists_only_active_queries_on_exact_warehouse() -> None:
    workspace = MagicMock()
    workspace.query_history.list.return_value = sql.ListQueriesResponse(
        has_next_page=False,
        res=[
            sql.QueryInfo(
                warehouse_id="wh-1",
                query_id="query-1",
                status=sql.QueryStatus.RUNNING,
                statement_type=sql.QueryStatementType.SELECT,
                client_application="Databricks Apps",
                query_start_time_ms=123,
            )
        ],
    )
    adapter = DatabricksRuntimeAdapter(workspace)

    queries = adapter.active_queries("wh-1")

    assert queries == [
        {
            "warehouse_id": "wh-1",
            "query_id": "query-1",
            "status": "RUNNING",
            "statement_type": "SELECT",
            "client_application": "Databricks Apps",
            "query_start_time_ms": 123,
        }
    ]
    call = workspace.query_history.list.call_args
    query_filter = call.kwargs["filter_by"]
    assert query_filter.warehouse_ids == ["wh-1"]
    assert set(query_filter.statuses or []) == {
        sql.QueryStatus.COMPILED,
        sql.QueryStatus.COMPILING,
        sql.QueryStatus.QUEUED,
        sql.QueryStatus.RUNNING,
        sql.QueryStatus.STARTED,
    }
    assert call.kwargs["max_results"] == 999


def test_inventory_merge_tombstones_resources_removed_from_bundle_scope() -> None:
    spark = MagicMock()
    store = SparkSqlActionStore(
        spark,
        "main",
        "dbx_platform",
        workspace_id="123456",
        environment="prod",
    )
    managed = inventory()
    observations = FakeRuntimeAdapter().resources

    store.upsert_inventory("123456", "prod", managed, NOW, observations)

    spark.sql.assert_called_once()
    statement = spark.sql.call_args.args[0]
    assert "WHEN NOT MATCHED BY SOURCE" in statement
    assert "ownership = 'RETIRED'" in statement
    assert "protected = TRUE" in statement
    assert "state = 'OUT_OF_SCOPE'" in statement
    assert "target.workspace_id = '123456'" in statement
    assert "target.environment = 'prod'" in statement
    for resource in managed.resources:
        assert resource.resource_key in statement
