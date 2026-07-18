"""Human-approved Hibernate/Wake orchestration for dbx-platform resources.

The controller is deliberately separate from the Platform Console app.  The app
is one of the resources Hibernate stops, so an unscheduled serverless Job runs
this module and remains available as the recovery path.

Safety invariants:

* the inventory is made only from exact bundle resource IDs;
* planning is read-only against managed resources;
* execution accepts only an approved, unexpired, canonical plan;
* resource configuration is re-read immediately before execution;
* active Jobs and dedicated-warehouse queries are drained, never force-cancelled;
* every operation is idempotent and records its outcome;
* the app is stopped last, after the durable desired state is written.

The production entry point is the ``spark_python_task`` in
``resources/runtime_control.yml``.  Importing this module has no side effects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Protocol

from databricks.sdk.service import jobs, sql

PLAN_SCHEMA_VERSION = 1
PLAN_TTL = timedelta(minutes=15)
DEFAULT_DRAIN_TIMEOUT_SECONDS = 15 * 60
DEFAULT_APP_HEALTH_TIMEOUT_SECONDS = 5 * 60
APPROVER_GROUP = "dbx-platform-approvers"

ACTION_HIBERNATE = "runtime.hibernate"
ACTION_WAKE = "runtime.wake"
ALLOWED_ACTIONS = frozenset({ACTION_HIBERNATE, ACTION_WAKE})

STATUS_AWAITING_APPROVAL = "AWAITING_APPROVAL"
STATUS_APPROVED = "APPROVED"
STATUS_EXECUTING = "EXECUTING"
STATUS_VERIFYING = "VERIFYING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_REJECTED = "REJECTED"
STATUS_EXPIRED = "EXPIRED"
STATUS_STALE = "STALE"
STATUS_FAILED = "FAILED"
STATUS_ROLLED_BACK = "ROLLED_BACK"


class RuntimeControlError(RuntimeError):
    """Base class for safe runtime-controller failures."""


class ApprovalRequiredError(RuntimeControlError):
    """The supplied action is not durably approved."""


class StalePlanError(RuntimeControlError):
    """A target changed after the human reviewed the plan."""


class DrainTimeoutError(RuntimeControlError):
    """Owned activity did not finish within the approved drain window."""


class InventoryError(RuntimeControlError):
    """The exact managed-resource inventory is invalid."""


class ResourceKind(str, Enum):
    JOB = "JOB"
    WAREHOUSE = "WAREHOUSE"
    APP = "APP"


class DesiredState(str, Enum):
    ON = "ON"
    SLEEPING = "SLEEPING"


@dataclass(frozen=True)
class ManagedResource:
    """A single exact bundle-owned resource."""

    resource_key: str
    resource_type: ResourceKind
    resource_id: str
    display_name: str
    stop_order: int
    stoppable: bool = True
    protected: bool = False
    desired_on_state: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["resource_type"] = self.resource_type.value
        return result


@dataclass(frozen=True)
class RuntimeInventory:
    """The complete v1 toolkit inventory; no discovery by name is permitted."""

    resources: tuple[ManagedResource, ...]

    def __post_init__(self) -> None:
        if not self.resources:
            raise InventoryError("The managed-resource inventory cannot be empty")
        keys = [resource.resource_key for resource in self.resources]
        identities = [
            (resource.resource_type.value, resource.resource_id)
            for resource in self.resources
        ]
        if len(keys) != len(set(keys)):
            raise InventoryError("Managed resource keys must be unique")
        if len(identities) != len(set(identities)):
            raise InventoryError("Managed resource IDs must be unique within a type")
        if any(not resource.resource_id.strip() for resource in self.resources):
            raise InventoryError("Managed resource IDs cannot be blank")
        invalid_on_states = [
            resource.resource_key
            for resource in self.resources
            if resource.resource_type is ResourceKind.JOB
            and resource.desired_on_state not in {None, "PAUSED", "UNPAUSED"}
        ]
        if invalid_on_states:
            raise InventoryError(
                "Managed Job desired ON state must be PAUSED or UNPAUSED: "
                + ", ".join(sorted(invalid_on_states))
            )
        if sum(resource.resource_type is ResourceKind.APP for resource in self.resources) != 1:
            raise InventoryError("The inventory must contain exactly one Platform Console app")
        if (
            sum(
                resource.resource_type is ResourceKind.WAREHOUSE
                for resource in self.resources
            )
            != 1
        ):
            raise InventoryError("The inventory must contain exactly one dedicated warehouse")

    @property
    def jobs(self) -> tuple[ManagedResource, ...]:
        return tuple(
            resource
            for resource in self.resources
            if resource.resource_type is ResourceKind.JOB
        )

    @property
    def warehouse(self) -> ManagedResource:
        return next(
            resource
            for resource in self.resources
            if resource.resource_type is ResourceKind.WAREHOUSE
        )

    @property
    def app(self) -> ManagedResource:
        return next(
            resource
            for resource in self.resources
            if resource.resource_type is ResourceKind.APP
        )

    @property
    def inventory_hash(self) -> str:
        return canonical_hash([resource.to_dict() for resource in self.resources])


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    action_type: str
    status: str
    plan: dict[str, Any]
    plan_hash: str
    confirm_phrase: str
    expires_at: datetime


@dataclass(frozen=True)
class RuntimeState:
    workspace_id: str
    environment: str
    desired_state: DesiredState
    actual_state: str
    prior_state: dict[str, Any]
    active_action_id: str | None
    last_reconciled_at: datetime | None
    updated_at: datetime
    version: int


@dataclass(frozen=True)
class Actor:
    actor_id: str
    email: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class ApprovalEvidence:
    approval_id: str
    action_id: str
    plan_hash: str
    approver_id: str
    approver_email: str | None
    approver_role: str
    confirmation: str | None
    decided_at: datetime


class ActionStore(Protocol):
    """Durable storage shared with the Mission Control approval service."""

    def verify_schema(self, actor_id: str, verified_at: datetime) -> None: ...

    def upsert_inventory(
        self,
        workspace_id: str,
        environment: str,
        inventory: RuntimeInventory,
        updated_at: datetime,
        observations: Mapping[str, Mapping[str, Any]],
    ) -> None: ...

    def create_action(
        self,
        plan: Mapping[str, Any],
        plan_hash: str,
        confirm_phrase: str,
    ) -> None: ...

    def get_action(self, action_id: str) -> ActionRecord | None: ...

    def get_matching_approval(
        self, action_id: str, plan_hash: str
    ) -> ApprovalEvidence | None: ...

    def approve_action(
        self,
        action_id: str,
        plan_hash: str,
        actor: Actor,
        confirmation: str,
        decided_at: datetime,
    ) -> None: ...

    def transition(
        self,
        action_id: str,
        allowed_from: set[str],
        to_status: str,
        actor_id: str,
        details: Mapping[str, Any],
        event_at: datetime,
    ) -> None: ...

    def append_event(
        self,
        action_id: str,
        event_type: str,
        actor_id: str,
        details: Mapping[str, Any],
        event_at: datetime,
    ) -> None: ...

    def get_runtime_state(
        self, workspace_id: str, environment: str
    ) -> RuntimeState | None: ...

    def save_runtime_state(self, state: RuntimeState) -> None: ...


class RuntimeAdapter(Protocol):
    """All Databricks operations used by the pure controller."""

    def observe(self, resource: ManagedResource) -> dict[str, Any]: ...

    def set_job_paused(self, job_id: str, paused: bool) -> None: ...

    def active_job_runs(self, job_ids: Sequence[str]) -> list[dict[str, Any]]: ...

    def active_queries(self, warehouse_id: str) -> list[dict[str, Any]]: ...

    def stop_warehouse(self, warehouse_id: str) -> None: ...

    def start_warehouse(self, warehouse_id: str) -> None: ...

    def stop_app(self, app_name: str) -> None: ...

    def start_app(self, app_name: str) -> None: ...


class ApproverVerifier(Protocol):
    def actor_for_run(self, run_id: int, required_group: str) -> Actor: ...

    def actor_for_approval(
        self, approval: ApprovalEvidence, required_group: str
    ) -> Actor: ...


def canonical_json(value: Any) -> str:
    """Return the immutable action-plan representation used by every executor."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _runtime_impact_measurement(
    plan: Mapping[str, Any],
    verified: Mapping[str, Mapping[str, Any]],
    measured_at: datetime,
) -> dict[str, Any]:
    targets = list(plan.get("targets") or [])
    target_states = plan.get("after_state", {}).get("resources", {})
    return {
        "status": "INITIAL_VERIFICATION_COMPLETE",
        "measured_at": _iso(measured_at),
        "expected": dict(plan.get("impact") or {}),
        "observed": {
            "verified_target_count": len(verified),
            "target_count": len(targets),
            "resource_states": {
                key: value.get("state") for key, value in sorted(verified.items())
            },
            "expected_resource_states": {
                key: value.get("state")
                for key, value in sorted(target_states.items())
            },
        },
        "follow_up": {
            "status": "PENDING_OBSERVATION_WINDOW",
            "measure_after": _iso(_utc(measured_at) + timedelta(hours=24)),
            "financial_savings": "PENDING_BILLING_COVERAGE",
            "risk_reduction": "NOT_APPLICABLE",
            "performance_change": "PENDING_SLO_COVERAGE",
        },
    }


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _resource_map(observations: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(observation["resource_key"]): dict(observation)
        for observation in observations
    }


def _target_observation(
    observation: Mapping[str, Any], action_type: str, wake_state: str | None = None
) -> dict[str, Any]:
    target = dict(observation)
    resource_type = target["resource_type"]
    if action_type == ACTION_HIBERNATE:
        if resource_type == ResourceKind.JOB.value:
            target["state"] = "PAUSED"
        else:
            target["state"] = "STOPPED"
    elif resource_type == ResourceKind.JOB.value:
        target["state"] = wake_state or "PAUSED"
    elif resource_type == ResourceKind.WAREHOUSE.value:
        target["state"] = "RUNNING"
    else:
        target["state"] = "ACTIVE"
    return target


def _precondition_from_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "resource_key": observation["resource_key"],
        "resource_type": observation["resource_type"],
        "resource_id": observation["resource_id"],
        "config_hash": observation["config_hash"],
        "state": observation["state"],
    }


class RuntimeController:
    """Plan and execute exact, reversible Hibernate/Wake operations."""

    def __init__(
        self,
        adapter: RuntimeAdapter,
        store: ActionStore,
        inventory: RuntimeInventory,
        workspace_id: str,
        environment: str,
        approver_verifier: ApproverVerifier,
        *,
        approver_group: str = APPROVER_GROUP,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        drain_timeout_seconds: int = DEFAULT_DRAIN_TIMEOUT_SECONDS,
        drain_poll_seconds: int = 10,
        app_health_timeout_seconds: int = DEFAULT_APP_HEALTH_TIMEOUT_SECONDS,
        app_health_poll_seconds: int = 5,
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.inventory = inventory
        self.workspace_id = workspace_id
        self.environment = environment
        self.approver_verifier = approver_verifier
        self.approver_group = approver_group
        self.clock = clock or (lambda: datetime.now(UTC))
        self.monotonic = monotonic or time.monotonic
        self.sleeper = sleeper or time.sleep
        self.drain_timeout_seconds = drain_timeout_seconds
        self.drain_poll_seconds = drain_poll_seconds
        self.app_health_timeout_seconds = app_health_timeout_seconds
        self.app_health_poll_seconds = app_health_poll_seconds

    def initialize(self) -> None:
        # Fail before even reading target state when the durable ledger is not
        # present and writable. The runtime executor never owns CREATE TABLE.
        self.store.verify_schema("runtime-controller", self.clock())
        observations = self.observe_all()
        self.store.upsert_inventory(
            self.workspace_id,
            self.environment,
            self.inventory,
            self.clock(),
            observations,
        )

    def observe_all(self) -> dict[str, dict[str, Any]]:
        observations = [self.adapter.observe(resource) for resource in self.inventory.resources]
        actual = _resource_map(observations)
        expected_keys = {resource.resource_key for resource in self.inventory.resources}
        if set(actual) != expected_keys:
            raise InventoryError("Adapter observations do not match the exact inventory")
        for resource in self.inventory.resources:
            observation = actual[resource.resource_key]
            if observation["resource_id"] != resource.resource_id:
                raise InventoryError(
                    f"Resource ID mismatch for {resource.resource_key}: "
                    f"{observation['resource_id']!r}"
                )
            if observation["resource_type"] != resource.resource_type.value:
                raise InventoryError(
                    f"Resource type mismatch for {resource.resource_key}"
                )
        return actual

    def plan_hibernate(self, proposer: Actor) -> ActionRecord:
        return self._create_plan(ACTION_HIBERNATE, proposer)

    def plan_wake(self, proposer: Actor) -> ActionRecord:
        return self._create_plan(ACTION_WAKE, proposer)

    def plan_reconciliation(self, proposer: Actor) -> ActionRecord | None:
        """Create, but never execute, the plan needed to restore durable intent."""

        current = self.observe_all()
        runtime = self.store.get_runtime_state(self.workspace_id, self.environment)
        desired = runtime.desired_state if runtime else DesiredState.ON
        action_type = ACTION_HIBERNATE if desired is DesiredState.SLEEPING else ACTION_WAKE
        expected = self._expected_after(action_type, current, runtime)
        if all(
            current[key]["config_hash"] == expected[key]["config_hash"]
            and current[key]["state"] == expected[key]["state"]
            for key in current
        ):
            return None
        return self._create_plan(action_type, proposer, observations=current)

    def _create_plan(
        self,
        action_type: str,
        proposer: Actor,
        *,
        observations: dict[str, dict[str, Any]] | None = None,
    ) -> ActionRecord:
        if action_type not in ALLOWED_ACTIONS:
            raise ValueError(f"Unsupported runtime action: {action_type}")
        now = _utc(self.clock())
        current = observations or self.observe_all()
        self._validate_stable_observations(current)
        runtime = self.store.get_runtime_state(self.workspace_id, self.environment)
        after = self._expected_after(action_type, current, runtime)
        action_id = str(uuid.uuid4())
        expires_at = now + PLAN_TTL
        changed = [
            key
            for key, observation in current.items()
            if observation["state"] != after[key]["state"]
        ]
        already_in_target_state = sorted(set(current) - set(changed))
        active_runs = (
            self.adapter.active_job_runs(
                [resource.resource_id for resource in self.inventory.jobs]
            )
            if action_type == ACTION_HIBERNATE
            else []
        )
        active_queries = (
            self.adapter.active_queries(self.inventory.warehouse.resource_id)
            if action_type == ACTION_HIBERNATE
            else []
        )
        confirm_phrase = f"apply {action_type} {len(self.inventory.resources)}"
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA_VERSION,
            "action_id": action_id,
            "action_type": action_type,
            "workspace_id": self.workspace_id,
            "environment": self.environment,
            "targets": [
                resource.to_dict()
                for resource in sorted(
                    self.inventory.resources, key=lambda item: item.resource_key
                )
            ],
            "parameters": {
                "drain_timeout_seconds": self.drain_timeout_seconds,
                "force_cancel": False,
                "inventory_hash": self.inventory.inventory_hash,
            },
            "preconditions": {
                "resources": {
                    key: _precondition_from_observation(value)
                    for key, value in sorted(current.items())
                }
            },
            "before_state": {
                "resources": current,
                "runtime": self._runtime_snapshot(runtime),
            },
            "after_state": {
                "desired_state": (
                    DesiredState.SLEEPING.value
                    if action_type == ACTION_HIBERNATE
                    else DesiredState.ON.value
                ),
                "resources": after,
            },
            "impact": {
                "changed_resource_count": len(changed),
                "changed_resource_keys": sorted(changed),
                "resources_to_change": sorted(changed),
                "already_in_target_state": already_in_target_state,
                "managed_job_count": len(self.inventory.jobs),
                "active_run_count_at_plan_time": len(active_runs),
                "active_runs_at_plan_time": active_runs,
                "active_query_count_at_plan_time": len(active_queries),
                "active_queries_at_plan_time": active_queries,
                "dependencies": [
                    {
                        "resource_key": self.inventory.app.resource_key,
                        "depends_on": self.inventory.warehouse.resource_key,
                        "relationship": "bound SQL compute",
                    },
                    {
                        "resource_key": "scheduled_jobs",
                        "depends_on": self.inventory.warehouse.resource_key,
                        "relationship": "SQL-backed report and rollup tasks",
                    },
                ],
                "estimated_idle_savings": {
                    "status": "UNAVAILABLE",
                    "reason": (
                        "The runtime controller has no invoice allocation input "
                        "and will not invent a currency estimate."
                    ),
                },
                "exclusions": [
                    "workspace",
                    "dashboards",
                    "Unity Catalog data",
                    "models",
                    "shared warehouses",
                    "unrelated jobs and apps",
                    "Azure storage and network resources",
                    "power-controller",
                    "action-executor",
                    "schema-migrations",
                ],
                "retained_data": [
                    "Unity Catalog tables and volumes",
                    "dashboard definitions",
                    "models and serving endpoint configuration",
                    "workspace files and bundle deployment state",
                    "Azure storage and network resources",
                ],
                "wake_procedure": [
                    "Run plan-wake in the Databricks Jobs UI.",
                    "Review its exact 15-minute plan and SHA-256 hash.",
                    "Run execute-wake with the plan ID, hash, and confirmation.",
                    "Verify warehouse, app health, and exact prior schedule states.",
                ],
            },
            "rollback": {
                "strategy": "restore-exact-before-state",
                "resources": current,
                "runtime": self._runtime_snapshot(runtime),
                "forced_job_cancellation": False,
            },
            "verification": {
                "resource_states": {
                    key: value["state"] for key, value in sorted(after.items())
                },
                "active_runs_must_be_zero_before_compute_stop": (
                    action_type == ACTION_HIBERNATE
                ),
                "active_queries_must_be_zero_before_compute_stop": (
                    action_type == ACTION_HIBERNATE
                ),
                "app_health_required_before_schedule_restore": (
                    action_type == ACTION_WAKE
                ),
            },
            "risk": "MEDIUM",
            "proposer_id": proposer.actor_id,
            "proposer_email": proposer.email,
            "created_at": now.isoformat(),
            "expires_at": expires_at.isoformat(),
            "idempotency_key": str(uuid.uuid4()),
            "confirm_phrase": confirm_phrase,
        }
        plan_hash = canonical_hash(plan)
        self.store.create_action(plan, plan_hash, confirm_phrase)
        return ActionRecord(
            action_id=action_id,
            action_type=action_type,
            status=STATUS_AWAITING_APPROVAL,
            plan=plan,
            plan_hash=plan_hash,
            confirm_phrase=confirm_phrase,
            expires_at=expires_at,
        )

    def _expected_after(
        self,
        action_type: str,
        current: Mapping[str, Mapping[str, Any]],
        runtime: RuntimeState | None,
    ) -> dict[str, dict[str, Any]]:
        if action_type == ACTION_HIBERNATE:
            return {
                key: _target_observation(value, action_type)
                for key, value in current.items()
            }

        prior_resources = {}
        if runtime and runtime.prior_state:
            prior_resources = runtime.prior_state.get("resources", {})
        resources_by_key = {
            resource.resource_key: resource for resource in self.inventory.resources
        }
        result: dict[str, dict[str, Any]] = {}
        for key, value in current.items():
            wake_state = None
            if value["resource_type"] == ResourceKind.JOB.value:
                if runtime and runtime.prior_state:
                    # Once Hibernate has captured a before-state, Wake restores
                    # exactly that state. A missing entry fails safely paused.
                    wake_state = prior_resources.get(key, {}).get("state", "PAUSED")
                else:
                    # Bundle schedules deploy paused so deployment cannot wake
                    # a sleeping toolkit. On a fresh installation, this
                    # configured intent is surfaced in the first immutable Wake
                    # reconciliation plan and takes effect only after approval.
                    wake_state = (
                        resources_by_key[key].desired_on_state or value["state"]
                    )
                if wake_state not in {"PAUSED", "UNPAUSED"}:
                    wake_state = "PAUSED"
            result[key] = _target_observation(value, action_type, wake_state)
        return result

    def _validate_stable_observations(
        self, observations: Mapping[str, Mapping[str, Any]]
    ) -> None:
        stable_states = {
            ResourceKind.JOB.value: {"PAUSED", "UNPAUSED"},
            ResourceKind.WAREHOUSE.value: {"RUNNING", "STOPPED"},
            ResourceKind.APP.value: {"ACTIVE", "STOPPED"},
        }
        unstable = [
            f"{key}={observation['state']}"
            for key, observation in observations.items()
            if observation["state"]
            not in stable_states.get(str(observation["resource_type"]), set())
        ]
        if unstable:
            raise StalePlanError(
                "Cannot produce a reversible plan while resource state is transitional "
                "or unknown: " + ", ".join(sorted(unstable))
            )

    def _runtime_snapshot(self, runtime: RuntimeState | None) -> dict[str, Any]:
        if runtime is None:
            return {
                "desired_state": DesiredState.ON.value,
                "actual_state": "UNKNOWN",
                "prior_state": {},
                "version": 0,
            }
        return {
            "desired_state": runtime.desired_state.value,
            "actual_state": runtime.actual_state,
            "prior_state": runtime.prior_state,
            "version": runtime.version,
        }

    def execute(
        self,
        action_id: str,
        supplied_hash: str,
        *,
        actor: Actor | None = None,
        confirmation: str | None = None,
    ) -> RuntimeState:
        record = self.store.get_action(action_id)
        if record is None:
            raise ApprovalRequiredError(f"Unknown action ID: {action_id}")
        self._validate_record(record, supplied_hash)

        if record.status == STATUS_AWAITING_APPROVAL:
            if actor is None:
                raise ApprovalRequiredError("An authorized human must approve this plan")
            if confirmation != record.confirm_phrase:
                raise ApprovalRequiredError(
                    f"Confirmation must exactly equal: {record.confirm_phrase}"
                )
            self.store.approve_action(
                record.action_id,
                record.plan_hash,
                actor,
                confirmation,
                self.clock(),
            )
            record = self.store.get_action(action_id)
            if record is None:
                raise ApprovalRequiredError("Approved action disappeared from durable storage")

        approval = self.store.get_matching_approval(
            record.action_id, record.plan_hash
        )
        if approval is None:
            raise ApprovalRequiredError(
                "Action has no durable human approval for this exact plan hash"
            )
        self._validate_approval_evidence(record, approval)
        verified_approver = self.approver_verifier.actor_for_approval(
            approval, self.approver_group
        )
        if verified_approver.actor_id != approval.approver_id:
            raise ApprovalRequiredError(
                "Live approver identity does not match the durable approval"
            )
        if (
            not approval.approver_email
            or verified_approver.email.lower() != approval.approver_email.lower()
        ):
            raise ApprovalRequiredError(
                "Live approver email does not match the durable approval"
            )

        if record.status == STATUS_SUCCEEDED:
            state = self.store.get_runtime_state(self.workspace_id, self.environment)
            if state is None:
                raise RuntimeControlError("Succeeded action has no durable runtime state")
            return state

        if record.status not in {STATUS_APPROVED, STATUS_EXECUTING, STATUS_VERIFYING}:
            raise ApprovalRequiredError(
                f"Action {action_id} is {record.status}, not approved for execution"
            )

        plan = record.plan
        self._validate_inventory(plan)
        current = self.observe_all()
        mismatches = self._precondition_mismatches(
            plan,
            current,
            allow_target_state=record.status in {STATUS_EXECUTING, STATUS_VERIFYING},
        )
        if mismatches:
            stale_from = (
                {STATUS_APPROVED}
                if record.status == STATUS_APPROVED
                else {STATUS_EXECUTING, STATUS_VERIFYING}
            )
            stale_status = (
                STATUS_STALE if record.status == STATUS_APPROVED else STATUS_FAILED
            )
            self.store.transition(
                action_id,
                stale_from,
                stale_status,
                actor.actor_id if actor else "runtime-controller",
                {"mismatches": mismatches},
                self.clock(),
            )
            raise StalePlanError(
                "Managed resources changed after approval: " + "; ".join(mismatches)
            )

        executor_id = actor.actor_id if actor else "runtime-controller"
        if record.status == STATUS_APPROVED:
            self.store.transition(
                action_id,
                {STATUS_APPROVED},
                STATUS_EXECUTING,
                executor_id,
                {"plan_hash": supplied_hash},
                self.clock(),
            )

        try:
            self.store.append_event(
                action_id,
                "MUTATION_INTENT",
                executor_id,
                {
                    "plan_hash": supplied_hash,
                    "action_type": record.action_type,
                    "targets": plan.get("targets", []),
                },
                self.clock(),
            )
        except Exception as exc:
            try:
                self.store.transition(
                    action_id,
                    {STATUS_EXECUTING},
                    STATUS_FAILED,
                    executor_id,
                    {
                        "error": f"{type(exc).__name__}: {exc}",
                        "mutation_started": False,
                    },
                    self.clock(),
                )
            except Exception:
                pass
            raise

        try:
            if record.action_type == ACTION_HIBERNATE:
                self._hibernate(plan, executor_id)
            elif record.action_type == ACTION_WAKE:
                self._wake(plan, executor_id)
            else:
                raise ApprovalRequiredError(
                    f"Action type {record.action_type!r} is not allowlisted"
                )
            current_record = self.store.get_action(action_id)
            if current_record and current_record.status == STATUS_EXECUTING:
                self.store.transition(
                    action_id,
                    {STATUS_EXECUTING},
                    STATUS_VERIFYING,
                    executor_id,
                    {},
                    self.clock(),
                )
            verified = self._verify_after(plan)
            desired = (
                DesiredState.SLEEPING
                if record.action_type == ACTION_HIBERNATE
                else DesiredState.ON
            )
            state = self._save_stable_state(plan, desired)
            self.store.upsert_inventory(
                self.workspace_id,
                self.environment,
                self.inventory,
                self.clock(),
                verified,
            )
            impact_measurement = _runtime_impact_measurement(
                plan,
                verified,
                self.clock(),
            )
            self.store.append_event(
                action_id,
                "IMPACT_MEASUREMENT",
                executor_id,
                impact_measurement,
                self.clock(),
            )
            self.store.transition(
                action_id,
                {STATUS_VERIFYING},
                STATUS_SUCCEEDED,
                executor_id,
                {
                    "desired_state": desired.value,
                    "impact_measurement": impact_measurement,
                },
                self.clock(),
            )
            return state
        except StalePlanError:
            raise
        except Exception as exc:
            rollback_errors = self._rollback(plan, executor_id)
            to_status = STATUS_ROLLED_BACK if not rollback_errors else STATUS_FAILED
            self.store.transition(
                action_id,
                {STATUS_EXECUTING, STATUS_VERIFYING},
                to_status,
                executor_id,
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "rollback_errors": rollback_errors,
                },
                self.clock(),
            )
            raise

    def _validate_approval_evidence(
        self, record: ActionRecord, approval: ApprovalEvidence
    ) -> None:
        if approval.action_id != record.action_id:
            raise ApprovalRequiredError(
                "Durable approval belongs to a different action"
            )
        if approval.plan_hash != record.plan_hash:
            raise ApprovalRequiredError(
                "Durable approval belongs to a different plan hash"
            )
        recorded_roles = {
            role.strip()
            for role in approval.approver_role.split(",")
            if role.strip()
        }
        if (
            "approver" not in recorded_roles
            and self.approver_group not in recorded_roles
        ):
            raise ApprovalRequiredError(
                "Durable approval does not record an approver role"
            )
        if approval.confirmation != record.confirm_phrase:
            raise ApprovalRequiredError(
                "Durable approval does not contain the exact typed confirmation"
            )

    def _validate_record(self, record: ActionRecord, supplied_hash: str) -> None:
        if record.action_type not in ALLOWED_ACTIONS:
            raise ApprovalRequiredError(
                f"Action type {record.action_type!r} is not allowlisted"
            )
        if supplied_hash != record.plan_hash:
            raise ApprovalRequiredError("Supplied plan hash does not match durable approval")
        if canonical_hash(record.plan) != record.plan_hash:
            raise ApprovalRequiredError("Durable plan payload does not match its hash")
        now = _utc(self.clock())
        if now >= _utc(record.expires_at) and record.status not in {
            STATUS_EXECUTING,
            STATUS_VERIFYING,
            STATUS_SUCCEEDED,
        }:
            if record.status not in {
                STATUS_EXPIRED,
                STATUS_REJECTED,
                STATUS_STALE,
                STATUS_FAILED,
                STATUS_ROLLED_BACK,
                STATUS_SUCCEEDED,
            }:
                self.store.transition(
                    record.action_id,
                    {record.status},
                    STATUS_EXPIRED,
                    "runtime-controller",
                    {},
                    now,
                )
            raise ApprovalRequiredError("Plan approval window has expired")
        if record.plan.get("action_id") != record.action_id:
            raise ApprovalRequiredError("Action ID does not match the immutable plan")
        if record.plan.get("action_type") != record.action_type:
            raise ApprovalRequiredError("Action type does not match the immutable plan")
        if record.plan.get("confirm_phrase") != record.confirm_phrase:
            raise ApprovalRequiredError(
                "Indexed confirmation phrase does not match the immutable plan"
            )
        try:
            planned_expiry = datetime.fromisoformat(
                str(record.plan.get("expires_at", "")).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ApprovalRequiredError(
                "Immutable plan has an invalid expiry"
            ) from exc
        if _utc(planned_expiry) != _utc(record.expires_at):
            raise ApprovalRequiredError("Expiry does not match the immutable plan")
        if record.plan.get("workspace_id") != self.workspace_id:
            raise ApprovalRequiredError("Plan belongs to another workspace")
        if record.plan.get("environment") != self.environment:
            raise ApprovalRequiredError("Plan belongs to another environment")

    def _validate_inventory(self, plan: Mapping[str, Any]) -> None:
        if plan.get("parameters", {}).get("inventory_hash") != self.inventory.inventory_hash:
            raise ApprovalRequiredError("The managed-resource inventory changed")
        planned_targets = sorted(
            plan.get("targets", []), key=lambda resource: resource["resource_key"]
        )
        current_targets = sorted(
            (resource.to_dict() for resource in self.inventory.resources),
            key=lambda resource: resource["resource_key"],
        )
        if planned_targets != current_targets:
            raise ApprovalRequiredError("Plan targets do not match the exact bundle inventory")

    def _precondition_mismatches(
        self,
        plan: Mapping[str, Any],
        current: Mapping[str, Mapping[str, Any]],
        *,
        allow_target_state: bool,
    ) -> list[str]:
        planned = plan["preconditions"]["resources"]
        target = plan["after_state"]["resources"]
        mismatches: list[str] = []
        if set(planned) != set(current):
            return ["resource set changed"]
        for key, observation in current.items():
            expected = planned[key]
            if observation["resource_id"] != expected["resource_id"]:
                mismatches.append(f"{key}: resource ID changed")
            if observation["config_hash"] != expected["config_hash"]:
                mismatches.append(f"{key}: configuration changed")
            allowed_states = {expected["state"]}
            if allow_target_state:
                allowed_states.add(target[key]["state"])
            if observation["state"] not in allowed_states:
                mismatches.append(
                    f"{key}: state {observation['state']} not in "
                    f"{sorted(allowed_states)}"
                )
        return mismatches

    def _hibernate(self, plan: Mapping[str, Any], actor_id: str) -> None:
        before = plan["before_state"]["resources"]
        for resource in self.inventory.jobs:
            if before[resource.resource_key]["state"] == "UNPAUSED":
                self._step_event(
                    plan, "SCHEDULE_PAUSE_INTENT", resource, actor_id
                )
                self.adapter.set_job_paused(resource.resource_id, True)
                self._step_event(plan, "SCHEDULE_PAUSED", resource, actor_id)

        self._drain_owned_activity(plan, actor_id)
        warehouse = self.inventory.warehouse
        self._step_event(plan, "WAREHOUSE_STOP_INTENT", warehouse, actor_id)
        self.adapter.stop_warehouse(warehouse.resource_id)
        self._step_event(plan, "WAREHOUSE_STOPPED", warehouse, actor_id)

        # Persist the inverse plan before stopping the UI that initiated this action.
        self.store.save_runtime_state(
            RuntimeState(
                workspace_id=self.workspace_id,
                environment=self.environment,
                desired_state=DesiredState.SLEEPING,
                actual_state="STOPPING_APP",
                prior_state=dict(plan["before_state"]),
                active_action_id=str(plan["action_id"]),
                last_reconciled_at=None,
                updated_at=_utc(self.clock()),
                version=self._next_runtime_version(),
            )
        )
        app = self.inventory.app
        self._step_event(plan, "APP_STOP_INTENT", app, actor_id)
        self.adapter.stop_app(app.resource_id)
        self._step_event(plan, "APP_STOPPED", app, actor_id)

    def _wake(self, plan: Mapping[str, Any], actor_id: str) -> None:
        self.store.save_runtime_state(
            RuntimeState(
                workspace_id=self.workspace_id,
                environment=self.environment,
                desired_state=DesiredState.ON,
                actual_state="STARTING_WAREHOUSE",
                prior_state=self._prior_state_for_wake(plan),
                active_action_id=str(plan["action_id"]),
                last_reconciled_at=None,
                updated_at=_utc(self.clock()),
                version=self._next_runtime_version(),
            )
        )
        warehouse = self.inventory.warehouse
        self._step_event(plan, "WAREHOUSE_START_INTENT", warehouse, actor_id)
        self.adapter.start_warehouse(warehouse.resource_id)
        self._step_event(plan, "WAREHOUSE_STARTED", warehouse, actor_id)

        app = self.inventory.app
        self._step_event(plan, "APP_START_INTENT", app, actor_id)
        self.adapter.start_app(app.resource_id)
        self._step_event(plan, "APP_STARTED", app, actor_id)
        self._wait_for_app_health(plan, app, actor_id)

        target = plan["after_state"]["resources"]
        for resource in self.inventory.jobs:
            should_pause = target[resource.resource_key]["state"] == "PAUSED"
            self._step_event(
                plan,
                "SCHEDULE_RESTORE_INTENT",
                resource,
                actor_id,
                {"state": "PAUSED" if should_pause else "UNPAUSED"},
            )
            self.adapter.set_job_paused(resource.resource_id, should_pause)
            self._step_event(
                plan,
                "SCHEDULE_RESTORED",
                resource,
                actor_id,
                {"state": "PAUSED" if should_pause else "UNPAUSED"},
            )

    def _wait_for_app_health(
        self,
        plan: Mapping[str, Any],
        app: ManagedResource,
        actor_id: str,
    ) -> None:
        deadline = self.monotonic() + self.app_health_timeout_seconds
        attempts = 0
        last_observation: dict[str, Any] = {}
        while True:
            attempts += 1
            last_observation = self.adapter.observe(app)
            health = last_observation.get("health") or {}
            ready = (
                last_observation.get("state") == "ACTIVE"
                and health.get("application_state") == "RUNNING"
                and health.get("deployment_state") == "SUCCEEDED"
                and int(health.get("running_instances") or 0) >= 1
            )
            if ready:
                self._step_event(
                    plan,
                    "APP_HEALTHY",
                    app,
                    actor_id,
                    {"attempts": attempts, "health": health},
                )
                return
            if self.monotonic() >= deadline:
                raise RuntimeControlError(
                    "Platform Console did not pass compute, deployment, and "
                    "application health checks within "
                    f"{self.app_health_timeout_seconds} seconds: "
                    f"{canonical_json(health)}"
                )
            self.sleeper(self.app_health_poll_seconds)

    def _drain_owned_activity(
        self, plan: Mapping[str, Any], actor_id: str
    ) -> None:
        job_ids = [resource.resource_id for resource in self.inventory.jobs]
        warehouse_id = self.inventory.warehouse.resource_id
        deadline = self.monotonic() + self.drain_timeout_seconds
        seen_runs: dict[str, dict[str, Any]] = {}
        seen_queries: dict[str, dict[str, Any]] = {}
        while True:
            active_runs = self.adapter.active_job_runs(job_ids)
            active_queries = self.adapter.active_queries(warehouse_id)
            for run in active_runs:
                seen_runs[str(run.get("run_id"))] = dict(run)
            for query in active_queries:
                seen_queries[str(query.get("query_id"))] = dict(query)
            if not active_runs and not active_queries:
                self.store.append_event(
                    str(plan["action_id"]),
                    "ACTIVE_ACTIVITY_DRAINED",
                    actor_id,
                    {
                        "drained_job_runs": list(seen_runs.values()),
                        "drained_queries": list(seen_queries.values()),
                    },
                    self.clock(),
                )
                return
            if self.monotonic() >= deadline:
                self.store.append_event(
                    str(plan["action_id"]),
                    "DRAIN_TIMEOUT",
                    actor_id,
                    {
                        "active_job_runs": active_runs,
                        "active_queries": active_queries,
                        "timeout_seconds": self.drain_timeout_seconds,
                        "cancellation_attempted": False,
                    },
                    self.clock(),
                )
                raise DrainTimeoutError(
                    f"{len(active_runs)} owned run(s) and "
                    f"{len(active_queries)} dedicated-warehouse query(s) remained "
                    f"active after {self.drain_timeout_seconds} seconds; "
                    "no runs were cancelled and no queries were cancelled"
                )
            self.sleeper(self.drain_poll_seconds)

    def _rollback(self, plan: Mapping[str, Any], actor_id: str) -> list[str]:
        before = plan["before_state"]["resources"]
        errors: list[str] = []

        # Rollback is itself a bounded mutation. Record the exact inverse
        # state before attempting recovery; a failed append is surfaced as an
        # incomplete rollback by the caller rather than hidden.
        self.store.append_event(
            str(plan["action_id"]),
            "ROLLBACK_INTENT",
            actor_id,
            {"resources": before},
            self.clock(),
        )

        # Compute and app are restored before schedules to avoid launching work
        # into an unavailable dependency.
        for resource in (self.inventory.warehouse, self.inventory.app):
            desired = before[resource.resource_key]["state"]
            try:
                if resource.resource_type is ResourceKind.WAREHOUSE:
                    if desired == "RUNNING":
                        self.adapter.start_warehouse(resource.resource_id)
                    else:
                        self.adapter.stop_warehouse(resource.resource_id)
                elif desired == "ACTIVE":
                    self.adapter.start_app(resource.resource_id)
                else:
                    self.adapter.stop_app(resource.resource_id)
            except Exception as exc:  # rollback must continue across resources
                errors.append(f"{resource.resource_key}: {type(exc).__name__}: {exc}")

        for resource in self.inventory.jobs:
            try:
                desired = before[resource.resource_key]["state"]
                self.adapter.set_job_paused(resource.resource_id, desired == "PAUSED")
            except Exception as exc:  # rollback must continue across resources
                errors.append(f"{resource.resource_key}: {type(exc).__name__}: {exc}")

        self.store.append_event(
            str(plan["action_id"]),
            "ROLLBACK_COMPLETED" if not errors else "ROLLBACK_INCOMPLETE",
            actor_id,
            {"errors": errors},
            self.clock(),
        )
        runtime_before = plan["before_state"].get("runtime", {})
        now = _utc(self.clock())
        desired = DesiredState(runtime_before.get("desired_state", DesiredState.ON.value))
        self.store.save_runtime_state(
            RuntimeState(
                workspace_id=self.workspace_id,
                environment=self.environment,
                desired_state=desired,
                actual_state=desired.value if not errors else "ROLLBACK_INCOMPLETE",
                prior_state=dict(runtime_before.get("prior_state") or {}),
                active_action_id=None,
                last_reconciled_at=now if not errors else None,
                updated_at=now,
                version=self._next_runtime_version(),
            )
        )
        return errors

    def _verify_after(
        self, plan: Mapping[str, Any]
    ) -> dict[str, dict[str, Any]]:
        current = self.observe_all()
        target = plan["after_state"]["resources"]
        mismatches = [
            f"{key}: expected {target[key]['state']}, got {current[key]['state']}"
            for key in current
            if current[key]["state"] != target[key]["state"]
        ]
        if mismatches:
            raise RuntimeControlError("Post-action verification failed: " + "; ".join(mismatches))
        return current

    def _save_stable_state(
        self, plan: Mapping[str, Any], desired_state: DesiredState
    ) -> RuntimeState:
        prior = (
            dict(plan["before_state"])
            if desired_state is DesiredState.SLEEPING
            else self._prior_state_for_wake(plan)
        )
        now = _utc(self.clock())
        state = RuntimeState(
            workspace_id=self.workspace_id,
            environment=self.environment,
            desired_state=desired_state,
            actual_state=desired_state.value,
            prior_state=prior,
            active_action_id=None,
            last_reconciled_at=now,
            updated_at=now,
            version=self._next_runtime_version(),
        )
        self.store.save_runtime_state(state)
        return state

    def _prior_state_for_wake(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        runtime = self.store.get_runtime_state(self.workspace_id, self.environment)
        if runtime and runtime.prior_state:
            return runtime.prior_state
        return {"resources": dict(plan["after_state"]["resources"])}

    def _next_runtime_version(self) -> int:
        current = self.store.get_runtime_state(self.workspace_id, self.environment)
        return (current.version if current else 0) + 1

    def _step_event(
        self,
        plan: Mapping[str, Any],
        event_type: str,
        resource: ManagedResource,
        actor_id: str,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        details = {
            "resource_key": resource.resource_key,
            "resource_type": resource.resource_type.value,
            "resource_id": resource.resource_id,
        }
        details.update(extra or {})
        self.store.append_event(
            str(plan["action_id"]), event_type, actor_id, details, self.clock()
        )


class DatabricksRuntimeAdapter:
    """Thin, exact-ID SDK adapter used by the serverless controller Job."""

    def __init__(self, workspace_client: Any) -> None:
        self.w = workspace_client

    def observe(self, resource: ManagedResource) -> dict[str, Any]:
        if resource.resource_type is ResourceKind.JOB:
            job = self.w.jobs.get(int(resource.resource_id))
            settings = job.settings
            if settings is None:
                raise InventoryError(f"Job {resource.resource_id} has no settings")
            schedule = settings.schedule
            state = (
                str(_enum_value(schedule.pause_status))
                if schedule and schedule.pause_status
                else "NOT_SCHEDULED"
            )
            config = {
                "name": settings.name,
                "quartz_cron_expression": (
                    schedule.quartz_cron_expression if schedule else None
                ),
                "timezone_id": schedule.timezone_id if schedule else None,
                "tags": settings.tags or {},
            }
        elif resource.resource_type is ResourceKind.WAREHOUSE:
            warehouse = self.w.warehouses.get(resource.resource_id)
            state = str(_enum_value(warehouse.state))
            config = {
                "name": warehouse.name,
                "cluster_size": warehouse.cluster_size,
                "auto_stop_mins": warehouse.auto_stop_mins,
                "enable_serverless_compute": warehouse.enable_serverless_compute,
                "warehouse_type": _enum_value(warehouse.warehouse_type),
            }
        else:
            app = self.w.apps.get(resource.resource_id)
            state = (
                str(_enum_value(app.compute_status.state))
                if app.compute_status and app.compute_status.state
                else "UNKNOWN"
            )
            config = {
                "name": app.name,
                "compute_size": _enum_value(app.compute_size),
                "default_source_code_path": app.default_source_code_path,
            }
            health = {
                "compute_state": state,
                "application_state": (
                    _enum_value(app.app_status.state)
                    if app.app_status and app.app_status.state
                    else "UNKNOWN"
                ),
                "running_instances": (
                    app.app_status.running_instances if app.app_status else 0
                ),
                "deployment_state": (
                    _enum_value(app.active_deployment.status.state)
                    if (
                        app.active_deployment
                        and app.active_deployment.status
                        and app.active_deployment.status.state
                    )
                    else "UNKNOWN"
                ),
            }
        observation = {
            "resource_key": resource.resource_key,
            "resource_type": resource.resource_type.value,
            "resource_id": resource.resource_id,
            "state": state,
            "config_hash": canonical_hash(config),
            "config": config,
        }
        if resource.resource_type is ResourceKind.APP:
            observation["health"] = health
        return observation

    def set_job_paused(self, job_id: str, paused: bool) -> None:
        current = self.w.jobs.get(int(job_id))
        schedule = current.settings.schedule if current.settings else None
        if schedule is None:
            raise InventoryError(f"Managed Job {job_id} no longer has a schedule")
        desired = jobs.PauseStatus.PAUSED if paused else jobs.PauseStatus.UNPAUSED
        if schedule.pause_status == desired:
            return
        self.w.jobs.update(
            int(job_id),
            new_settings=jobs.JobSettings(
                schedule=jobs.CronSchedule(
                    quartz_cron_expression=schedule.quartz_cron_expression,
                    timezone_id=schedule.timezone_id,
                    pause_status=desired,
                )
            ),
        )

    def active_job_runs(self, job_ids: Sequence[str]) -> list[dict[str, Any]]:
        active: list[dict[str, Any]] = []
        for job_id in job_ids:
            for run in self.w.jobs.list_runs(
                job_id=int(job_id), active_only=True, limit=100
            ):
                active.append(
                    {
                        "job_id": str(job_id),
                        "run_id": run.run_id,
                        "run_name": run.run_name,
                        "life_cycle_state": (
                            _enum_value(run.state.life_cycle_state)
                            if run.state
                            else None
                        ),
                    }
                )
        return active

    def active_queries(self, warehouse_id: str) -> list[dict[str, Any]]:
        """Return non-terminal statements for one exact warehouse.

        Statement text and user identity are deliberately omitted from the
        approval artifact. Query IDs, state, type, client, and start time are
        enough to assess blast radius without leaking query contents.
        """

        active_statuses = [
            sql.QueryStatus.COMPILED,
            sql.QueryStatus.COMPILING,
            sql.QueryStatus.QUEUED,
            sql.QueryStatus.RUNNING,
            sql.QueryStatus.STARTED,
        ]
        active: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        while True:
            response = self.w.query_history.list(
                filter_by=sql.QueryFilter(
                    warehouse_ids=[warehouse_id],
                    statuses=active_statuses,
                ),
                include_metrics=False,
                max_results=999,
                page_token=page_token,
            )
            for query in response.res or []:
                if query.warehouse_id and query.warehouse_id != warehouse_id:
                    raise InventoryError(
                        "Statement History returned a query from outside the "
                        "dedicated warehouse scope"
                    )
                active.append(
                    {
                        "warehouse_id": warehouse_id,
                        "query_id": query.query_id,
                        "status": _enum_value(query.status),
                        "statement_type": _enum_value(query.statement_type),
                        "client_application": query.client_application,
                        "query_start_time_ms": query.query_start_time_ms,
                    }
                )
            if not response.has_next_page:
                return active
            next_token = response.next_page_token
            if not next_token or next_token in seen_tokens:
                raise InventoryError(
                    "Statement History pagination did not provide a usable next token"
                )
            seen_tokens.add(next_token)
            page_token = next_token

    def stop_warehouse(self, warehouse_id: str) -> None:
        state = str(_enum_value(self.w.warehouses.get(warehouse_id).state))
        if state in {"STOPPED", "DELETED"}:
            return
        self.w.warehouses.stop_and_wait(warehouse_id)

    def start_warehouse(self, warehouse_id: str) -> None:
        state = str(_enum_value(self.w.warehouses.get(warehouse_id).state))
        if state == "RUNNING":
            return
        self.w.warehouses.start_and_wait(warehouse_id)

    def stop_app(self, app_name: str) -> None:
        app = self.w.apps.get(app_name)
        state = str(_enum_value(app.compute_status.state)) if app.compute_status else "UNKNOWN"
        if state == "STOPPED":
            return
        self.w.apps.stop_and_wait(app_name)

    def start_app(self, app_name: str) -> None:
        app = self.w.apps.get(app_name)
        state = str(_enum_value(app.compute_status.state)) if app.compute_status else "UNKNOWN"
        if state == "ACTIVE":
            return
        self.w.apps.start_and_wait(app_name)


class WorkspaceApproverVerifier:
    """Resolve the Job launcher and verify explicit approver-group membership."""

    def __init__(self, workspace_client: Any) -> None:
        self.w = workspace_client

    def actor_for_run(self, run_id: int, required_group: str) -> Actor:
        run = self.w.jobs.get_run(run_id)
        launcher = run.creator_user_name
        if not launcher:
            raise ApprovalRequiredError("The controller run has no human launcher identity")
        escaped = launcher.replace("\\", "\\\\").replace('"', '\\"')
        users = list(self.w.users.list(filter=f'userName eq "{escaped}"'))
        if len(users) != 1 or not users[0].id:
            raise ApprovalRequiredError(f"Cannot resolve launcher identity {launcher!r}")
        return self._actor_from_user(users[0], required_group)

    def actor_for_approval(
        self, approval: ApprovalEvidence, required_group: str
    ) -> Actor:
        try:
            user = self.w.users.get(approval.approver_id)
        except Exception as exc:
            raise ApprovalRequiredError(
                "Cannot re-resolve the durable approver identity"
            ) from exc
        if not user.id or str(user.id) != approval.approver_id:
            raise ApprovalRequiredError(
                "Resolved approver identity does not match the durable approval"
            )
        return self._actor_from_user(user, required_group)

    @staticmethod
    def _actor_from_user(user: Any, required_group: str) -> Actor:
        if user.active is not True:
            raise ApprovalRequiredError("The approving user is no longer active")
        roles = tuple(
            sorted(
                {
                    str(group.display)
                    for group in user.groups or []
                    if group.display is not None
                }
            )
        )
        if required_group not in roles:
            raise ApprovalRequiredError(
                f"{user.user_name!r} is not a member of required group "
                f"{required_group!r}"
            )
        if not user.id or not user.user_name:
            raise ApprovalRequiredError("Approver identity is incomplete")
        return Actor(actor_id=str(user.id), email=str(user.user_name), roles=roles)


def _sql_string(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def _sql_timestamp(value: datetime) -> str:
    normalized = _utc(value).replace(tzinfo=None).isoformat(sep=" ", timespec="microseconds")
    return f"TIMESTAMP {_sql_string(normalized)}"


def _safe_identifier(value: str) -> str:
    if not value or any(not (char.isalnum() or char == "_") for char in value):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return value


class SparkSqlActionStore:
    """Delta-backed action/runtime store for serverless Jobs.

    The schema matches the Mission Control approval foundation. Migrations run
    under the deployment identity; this executor only verifies the tables and
    its append permission, then fails closed when either is absent.
    """

    def __init__(
        self,
        spark: Any,
        catalog: str,
        schema: str,
        *,
        workspace_id: str,
        environment: str,
    ) -> None:
        self.spark = spark
        self.catalog = _safe_identifier(catalog)
        self.schema = _safe_identifier(schema)
        if not workspace_id or not environment:
            raise ValueError("Workspace and environment scope are required")
        self.workspace_id = workspace_id
        self.environment = environment
        self.prefix = f"`{self.catalog}`.`{self.schema}`"

    def verify_schema(self, actor_id: str, verified_at: datetime) -> None:
        required = (
            "action_requests",
            "action_approvals",
            "action_events",
            "managed_resources",
            "platform_runtime_state",
        )
        missing = [
            name
            for name in required
            if not self.spark.catalog.tableExists(
                f"{self.catalog}.{self.schema}.{name}"
            )
        ]
        if missing:
            raise RuntimeControlError(
                "Required control-plane tables are missing: "
                + ", ".join(missing)
                + ". Run the deployment-identity migrations first."
            )
        # A successful append proves SELECT-only access was not accidentally
        # granted to the executor. It is an audit/health event, not a managed
        # resource mutation.
        self._insert_event(
            "__runtime_controller_health__",
            "STORAGE_VERIFIED",
            None,
            None,
            actor_id,
            {"catalog": self.catalog, "schema": self.schema},
            verified_at,
        )

    def upsert_inventory(
        self,
        workspace_id: str,
        environment: str,
        inventory: RuntimeInventory,
        updated_at: datetime,
        observations: Mapping[str, Mapping[str, Any]],
    ) -> None:
        source_rows: list[str] = []
        for resource in inventory.resources:
            metadata = canonical_json({"inventory_hash": inventory.inventory_hash})
            state = str(observations[resource.resource_key]["state"])
            source_rows.append(
                f"""
                SELECT
                  {_sql_string(workspace_id)} AS workspace_id,
                  {_sql_string(environment)} AS environment,
                  {_sql_string(resource.resource_id)} AS resource_id,
                  {_sql_string(resource.resource_type.value)} AS resource_type,
                  {_sql_string(resource.display_name)} AS display_name,
                  {_sql_string(resource.resource_key)} AS bundle_key,
                  'BUNDLE' AS ownership,
                  {str(resource.stoppable).upper()} AS stoppable,
                  {str(resource.protected).upper()} AS protected,
                  {resource.stop_order} AS stop_order,
                  {_sql_string(state)} AS state,
                  {_sql_string(metadata)} AS metadata_json,
                  {_sql_timestamp(updated_at)} AS updated_at
                """
            )
        source_sql = "\nUNION ALL\n".join(source_rows)
        self.spark.sql(
            f"""
            MERGE INTO {self.prefix}.managed_resources AS target
            USING ({source_sql}) AS source
            ON target.workspace_id = source.workspace_id
               AND target.environment = source.environment
               AND target.bundle_key = source.bundle_key
            WHEN MATCHED THEN UPDATE SET
              resource_id = source.resource_id,
              resource_type = source.resource_type,
              display_name = source.display_name,
              ownership = source.ownership,
              stoppable = source.stoppable,
              protected = source.protected,
              stop_order = source.stop_order,
              state = source.state,
              metadata_json = source.metadata_json,
              updated_at = source.updated_at
            WHEN NOT MATCHED THEN INSERT (
              workspace_id, environment, resource_id, resource_type,
              display_name, bundle_key, ownership, stoppable, protected,
              stop_order, state, metadata_json, updated_at
            ) VALUES (
              source.workspace_id, source.environment, source.resource_id,
              source.resource_type, source.display_name, source.bundle_key,
              source.ownership, source.stoppable, source.protected,
              source.stop_order, source.state, source.metadata_json,
              source.updated_at
            )
            WHEN NOT MATCHED BY SOURCE
                 AND target.workspace_id = {_sql_string(workspace_id)}
                 AND target.environment = {_sql_string(environment)}
                 AND target.ownership = 'BUNDLE'
            THEN UPDATE SET
              ownership = 'RETIRED',
              stoppable = FALSE,
              protected = TRUE,
              state = 'OUT_OF_SCOPE',
              metadata_json = {_sql_string(canonical_json({"reason": "not-in-current-bundle"}))},
              updated_at = {_sql_timestamp(updated_at)}
            """
        )

    def create_action(
        self,
        plan: Mapping[str, Any],
        plan_hash: str,
        confirm_phrase: str,
    ) -> None:
        action_id = str(plan["action_id"])
        if (
            str(plan.get("workspace_id")) != self.workspace_id
            or str(plan.get("environment")) != self.environment
        ):
            raise RuntimeControlError(
                "Runtime plan belongs to a different workspace or environment"
            )
        if self.get_action(action_id) is not None:
            raise RuntimeControlError(f"Action ID already exists: {action_id}")
        created_at = datetime.fromisoformat(str(plan["created_at"]).replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(str(plan["expires_at"]).replace("Z", "+00:00"))
        self.spark.sql(
            f"""
            INSERT INTO {self.prefix}.action_requests VALUES (
              {_sql_string(str(plan["workspace_id"]))},
              {_sql_string(str(plan["environment"]))},
              {_sql_string(action_id)},
              {_sql_string(str(plan["action_type"]))},
              {_sql_string(STATUS_AWAITING_APPROVAL)},
              {_sql_string(canonical_json(plan))},
              {_sql_string(plan_hash)},
              {_sql_string(confirm_phrase)},
              {_sql_string(str(plan["risk"]))},
              {_sql_string(str(plan["proposer_id"]))},
              {_sql_string(str(plan.get("proposer_email") or ""))},
              {_sql_timestamp(created_at)},
              {_sql_timestamp(expires_at)},
              {_sql_timestamp(created_at)},
              {_sql_string(str(plan["idempotency_key"]))},
              NULL
            )
            """
        )
        self.append_event(
            action_id,
            "PLAN_CREATED",
            str(plan["proposer_id"]),
            {"plan_hash": plan_hash},
            created_at,
        )

    def get_action(self, action_id: str) -> ActionRecord | None:
        rows = self.spark.sql(
            f"""
            SELECT action_id, action_type, status, plan_json, plan_hash,
                   confirm_phrase, expires_at
            FROM {self.prefix}.action_requests
            WHERE action_id = {_sql_string(action_id)}
              AND workspace_id = {_sql_string(self.workspace_id)}
              AND environment = {_sql_string(self.environment)}
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).collect()
        if not rows:
            return None
        row = rows[0].asDict(recursive=True)
        return ActionRecord(
            action_id=row["action_id"],
            action_type=row["action_type"],
            status=row["status"],
            plan=json.loads(row["plan_json"]),
            plan_hash=row["plan_hash"],
            confirm_phrase=row["confirm_phrase"],
            expires_at=_utc(row["expires_at"]),
        )

    def approve_action(
        self,
        action_id: str,
        plan_hash: str,
        actor: Actor,
        confirmation: str,
        decided_at: datetime,
    ) -> None:
        current = self.get_action(action_id)
        if current is None or current.status != STATUS_AWAITING_APPROVAL:
            raise ApprovalRequiredError("Only an awaiting plan can be approved")
        if current.plan_hash != plan_hash:
            raise ApprovalRequiredError("Approval hash does not match the plan")
        self.transition(
            action_id,
            {STATUS_AWAITING_APPROVAL},
            STATUS_APPROVED,
            actor.actor_id,
            {"plan_hash": plan_hash},
            decided_at,
        )
        # Insert only after winning the optimistic status transition. If this
        # write fails, execution remains fail-closed because it independently
        # requires a matching durable approval row.
        self.spark.sql(
            f"""
            INSERT INTO {self.prefix}.action_approvals (
              workspace_id, environment, approval_id, action_id, plan_hash,
              decision, approver_id, approver_email, approver_role,
              confirmation, decided_at
            ) VALUES (
              {_sql_string(self.workspace_id)}, {_sql_string(self.environment)},
              {_sql_string(str(uuid.uuid4()))}, {_sql_string(action_id)},
              {_sql_string(plan_hash)}, 'APPROVED',
              {_sql_string(actor.actor_id)}, {_sql_string(actor.email)},
              {_sql_string(",".join(actor.roles))}, {_sql_string(confirmation)},
              {_sql_timestamp(decided_at)}
            )
            """
        )

    def get_matching_approval(
        self, action_id: str, plan_hash: str
    ) -> ApprovalEvidence | None:
        rows = self.spark.sql(
            f"""
            SELECT approval_id, action_id, plan_hash, approver_id,
                   approver_email, approver_role, confirmation, decided_at
            FROM {self.prefix}.action_approvals
            WHERE action_id = {_sql_string(action_id)}
              AND plan_hash = {_sql_string(plan_hash)}
              AND workspace_id = {_sql_string(self.workspace_id)}
              AND environment = {_sql_string(self.environment)}
              AND decision = 'APPROVED'
            ORDER BY decided_at DESC
            LIMIT 1
            """
        ).collect()
        if not rows:
            return None
        row = rows[0].asDict(recursive=True)
        return ApprovalEvidence(
            approval_id=str(row["approval_id"]),
            action_id=str(row["action_id"]),
            plan_hash=str(row["plan_hash"]),
            approver_id=str(row["approver_id"]),
            approver_email=(
                str(row["approver_email"]) if row["approver_email"] else None
            ),
            approver_role=str(row["approver_role"]),
            confirmation=(
                str(row["confirmation"]) if row["confirmation"] else None
            ),
            decided_at=_utc(row["decided_at"]),
        )

    def transition(
        self,
        action_id: str,
        allowed_from: set[str],
        to_status: str,
        actor_id: str,
        details: Mapping[str, Any],
        event_at: datetime,
    ) -> None:
        current = self.get_action(action_id)
        if current is None or current.status not in allowed_from:
            actual = current.status if current else "MISSING"
            raise RuntimeControlError(
                f"Invalid action transition {actual} -> {to_status} for {action_id}"
            )
        from_status = current.status
        terminal_reason = (
            canonical_json(details)
            if to_status
            in {
                STATUS_REJECTED,
                STATUS_EXPIRED,
                STATUS_STALE,
                STATUS_FAILED,
                STATUS_ROLLED_BACK,
            }
            else None
        )
        allowed_sql = ", ".join(_sql_string(value) for value in sorted(allowed_from))
        self._insert_event(
            action_id,
            "TRANSITION_INTENT",
            from_status,
            to_status,
            actor_id,
            {"target_status": to_status, **dict(details)},
            event_at,
        )
        self.spark.sql(
            f"""
            UPDATE {self.prefix}.action_requests
            SET status = {_sql_string(to_status)},
                updated_at = {_sql_timestamp(event_at)},
                terminal_reason = {_sql_string(terminal_reason)}
            WHERE action_id = {_sql_string(action_id)}
              AND workspace_id = {_sql_string(self.workspace_id)}
              AND environment = {_sql_string(self.environment)}
              AND status IN ({allowed_sql})
            """
        )
        updated = self.get_action(action_id)
        if updated is None or updated.status != to_status:
            raise RuntimeControlError("Concurrent action transition was rejected")
        self._insert_event(
            action_id,
            "STATUS_CHANGED",
            from_status,
            to_status,
            actor_id,
            details,
            event_at,
        )

    def append_event(
        self,
        action_id: str,
        event_type: str,
        actor_id: str,
        details: Mapping[str, Any],
        event_at: datetime,
    ) -> None:
        self._insert_event(
            action_id, event_type, None, None, actor_id, details, event_at
        )

    def _insert_event(
        self,
        action_id: str,
        event_type: str,
        from_status: str | None,
        to_status: str | None,
        actor_id: str,
        details: Mapping[str, Any],
        event_at: datetime,
    ) -> None:
        self.spark.sql(
            f"""
            INSERT INTO {self.prefix}.action_events (
              workspace_id, environment, event_id, action_id, event_type,
              from_status, to_status, actor_id, details_json, event_ts
            ) VALUES (
              {_sql_string(self.workspace_id)}, {_sql_string(self.environment)},
              {_sql_string(str(uuid.uuid4()))}, {_sql_string(action_id)},
              {_sql_string(event_type)}, {_sql_string(from_status)},
              {_sql_string(to_status)}, {_sql_string(actor_id)},
              {_sql_string(canonical_json(details))}, {_sql_timestamp(event_at)}
            )
            """
        )

    def get_runtime_state(
        self, workspace_id: str, environment: str
    ) -> RuntimeState | None:
        rows = self.spark.sql(
            f"""
            SELECT * FROM {self.prefix}.platform_runtime_state
            WHERE workspace_id = {_sql_string(workspace_id)}
              AND environment = {_sql_string(environment)}
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ).collect()
        if not rows:
            return None
        row = rows[0].asDict(recursive=True)
        return RuntimeState(
            workspace_id=row["workspace_id"],
            environment=row["environment"],
            desired_state=DesiredState(row["desired_state"]),
            actual_state=row["actual_state"],
            prior_state=json.loads(row["prior_state_json"] or "{}"),
            active_action_id=row["active_action_id"],
            last_reconciled_at=(
                _utc(row["last_reconciled_at"])
                if row["last_reconciled_at"] is not None
                else None
            ),
            updated_at=_utc(row["updated_at"]),
            version=int(row["version"]),
        )

    def save_runtime_state(self, state: RuntimeState) -> None:
        self.spark.sql(
            f"""
            MERGE INTO {self.prefix}.platform_runtime_state AS target
            USING (
              SELECT {_sql_string(state.workspace_id)} AS workspace_id,
                     {_sql_string(state.environment)} AS environment
            ) AS source
            ON target.workspace_id = source.workspace_id
               AND target.environment = source.environment
            WHEN MATCHED THEN UPDATE SET
              desired_state = {_sql_string(state.desired_state.value)},
              actual_state = {_sql_string(state.actual_state)},
              prior_state_json = {_sql_string(canonical_json(state.prior_state))},
              active_action_id = {_sql_string(state.active_action_id)},
              last_reconciled_at = {
                  _sql_timestamp(state.last_reconciled_at)
                  if state.last_reconciled_at
                  else "NULL"
              },
              updated_at = {_sql_timestamp(state.updated_at)},
              version = {state.version}
            WHEN NOT MATCHED THEN INSERT (
              workspace_id, environment, desired_state, actual_state,
              prior_state_json, active_action_id, last_reconciled_at, updated_at,
              version
            ) VALUES (
              {_sql_string(state.workspace_id)}, {_sql_string(state.environment)},
              {_sql_string(state.desired_state.value)},
              {_sql_string(state.actual_state)},
              {_sql_string(canonical_json(state.prior_state))},
              {_sql_string(state.active_action_id)},
              {
                  _sql_timestamp(state.last_reconciled_at)
                  if state.last_reconciled_at
                  else "NULL"
              },
              {_sql_timestamp(state.updated_at)}, {state.version}
            )
            """
        )


def _parse_inventory(
    job_values: Sequence[str], warehouse_id: str, app_name: str
) -> RuntimeInventory:
    resources: list[ManagedResource] = []
    for index, value in enumerate(job_values):
        key, separator, resource_id = value.partition("=")
        if not separator or not key or not resource_id:
            raise InventoryError(f"--job must be KEY=EXACT_ID, got {value!r}")
        resources.append(
            ManagedResource(
                resource_key=key,
                resource_type=ResourceKind.JOB,
                resource_id=resource_id,
                display_name=key.replace("_", "-"),
                stop_order=10 + index,
                desired_on_state="UNPAUSED",
            )
        )
    resources.extend(
        [
            ManagedResource(
                resource_key="platform_console_warehouse",
                resource_type=ResourceKind.WAREHOUSE,
                resource_id=warehouse_id,
                display_name="[dbx-platform] mission-control",
                stop_order=100,
            ),
            ManagedResource(
                resource_key="platform_console",
                resource_type=ResourceKind.APP,
                resource_id=app_name,
                display_name=app_name,
                stop_order=200,
            ),
        ]
    )
    return RuntimeInventory(tuple(resources))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operation",
        required=True,
        choices=[
            "plan-hibernate",
            "execute-hibernate",
            "plan-wake",
            "execute-wake",
            "reconcile",
        ],
    )
    parser.add_argument("--plan-id", default="")
    parser.add_argument("--plan-hash", default="")
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--job", action="append", default=[])
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--app-name", required=True)
    parser.add_argument("--environment", default="prod")
    parser.add_argument("--catalog", default="main")
    parser.add_argument("--schema", default="dbx_platform")
    parser.add_argument("--approver-group", default=APPROVER_GROUP)
    parser.add_argument("--expected-executor", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Serverless Job entry point.  It prints the exact review artifact as JSON."""

    args = build_parser().parse_args(argv)
    try:
        from databricks.sdk import WorkspaceClient
        from pyspark.sql import SparkSession

        workspace = WorkspaceClient()
        current_user = workspace.current_user.me()
        current_identities = {
            str(getattr(current_user, "id", "") or ""),
            str(getattr(current_user, "user_name", "") or ""),
            str(getattr(current_user, "application_id", "") or ""),
        }
        if args.expected_executor not in current_identities:
            raise RuntimeControlError(
                "Current job identity is not the configured runtime executor "
                "service principal."
            )
        verifier = WorkspaceApproverVerifier(workspace)
        inventory = _parse_inventory(args.job, args.warehouse_id, args.app_name)

        # The currently running power-controller Job must never enter its own
        # stop/drain inventory.
        controller_run = workspace.jobs.get_run(args.run_id)
        if controller_run.job_id is not None and str(controller_run.job_id) in {
            resource.resource_id for resource in inventory.jobs
        }:
            raise InventoryError("The power-controller Job cannot manage itself")

        spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
        workspace_id = str(workspace.get_workspace_id())
        store = SparkSqlActionStore(
            spark,
            args.catalog,
            args.schema,
            workspace_id=workspace_id,
            environment=args.environment,
        )
        controller = RuntimeController(
            DatabricksRuntimeAdapter(workspace),
            store,
            inventory,
            workspace_id=workspace_id,
            environment=args.environment,
            approver_verifier=verifier,
            approver_group=args.approver_group,
        )
        controller.initialize()

        if args.operation == "plan-hibernate":
            # Planning changes no managed resource. The bound app SP and the
            # approver group may both propose; only approval/execution requires
            # a verified human.
            actor = Actor(
                actor_id=f"job-run:{args.run_id}",
                email=controller_run.creator_user_name or "runtime-proposer",
                roles=("automation-proposer",),
            )
            record = controller.plan_hibernate(actor)
            output = _review_output(record)
        elif args.operation == "plan-wake":
            actor = Actor(
                actor_id=f"job-run:{args.run_id}",
                email=controller_run.creator_user_name or "runtime-proposer",
                roles=("automation-proposer",),
            )
            record = controller.plan_wake(actor)
            output = _review_output(record)
        elif args.operation == "reconcile":
            # Reconciliation only observes resources and creates an
            # AWAITING_APPROVAL plan. It is safe for the deployment identity to
            # propose; execution still requires an explicit human approver.
            actor = Actor(
                actor_id=f"job-run:{args.run_id}",
                email=controller_run.creator_user_name or "deployment-reconciler",
                roles=("automation-proposer",),
            )
            record = controller.plan_reconciliation(actor)
            output = (
                {"status": "ALREADY_RECONCILED"}
                if record is None
                else _review_output(record)
            )
        else:
            expected_action = (
                ACTION_HIBERNATE
                if args.operation == "execute-hibernate"
                else ACTION_WAKE
            )
            if not args.plan_id or not args.plan_hash:
                raise ApprovalRequiredError(
                    "Execution requires --plan-id and --plan-hash from the reviewed plan"
                )
            existing = store.get_action(args.plan_id)
            if existing is None or existing.action_type != expected_action:
                raise ApprovalRequiredError(
                    f"{args.plan_id!r} is not an {expected_action} plan"
                )
            # An app-triggered run executes a plan already approved through the
            # Action Center. A Jobs UI run may atomically approve an awaiting
            # plan, so only that path resolves and verifies the human launcher.
            actor = (
                verifier.actor_for_run(args.run_id, args.approver_group)
                if existing.status == STATUS_AWAITING_APPROVAL
                else None
            )
            state = controller.execute(
                args.plan_id,
                args.plan_hash,
                actor=actor,
                confirmation=args.confirmation,
            )
            output = {
                "action_id": args.plan_id,
                "status": STATUS_SUCCEEDED,
                "desired_state": state.desired_state.value,
                "updated_at": _iso(state.updated_at),
            }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    except RuntimeControlError as exc:
        print(f"runtime control refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"runtime control failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _review_output(record: ActionRecord) -> dict[str, Any]:
    return {
        "action_id": record.action_id,
        "action_type": record.action_type,
        "status": record.status,
        "plan_hash": record.plan_hash,
        "confirm_phrase": record.confirm_phrase,
        "expires_at": _iso(record.expires_at),
        "plan": record.plan,
        "next_step": (
            "Review the full plan, then rerun the power-controller with the matching "
            "execute operation, action ID, plan hash, and confirmation phrase."
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
