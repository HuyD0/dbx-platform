"""Immutable action plans and the fail-closed approval state machine.

This module deliberately contains no Databricks SDK calls.  Planners collect
current state, repositories persist it, and executors consume only an approved
``action_id`` plus its hash.  Keeping those responsibilities separate makes
the security invariants easy to unit-test without a workspace.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

PLAN_TTL_SECONDS = 15 * 60
PLAN_SCHEMA_VERSION = 1
DEFAULT_EXECUTION_ALLOWLIST = frozenset(
    {
        "stale-clusters",
        "orphaned-jobs",
        "token-revoke",
        "policy-sync",
        "run-job",
        "configure-budget",
        "runtime.hibernate",
        "runtime.wake",
    }
)


class ActionStatus(StrEnum):
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    SUCCEEDED = "SUCCEEDED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


TERMINAL_STATUSES = {
    ActionStatus.SUCCEEDED,
    ActionStatus.REJECTED,
    ActionStatus.EXPIRED,
    ActionStatus.STALE,
    ActionStatus.FAILED,
    ActionStatus.ROLLED_BACK,
}

ALLOWED_TRANSITIONS: dict[ActionStatus, set[ActionStatus]] = {
    ActionStatus.AWAITING_APPROVAL: {
        ActionStatus.APPROVED,
        ActionStatus.REJECTED,
        ActionStatus.EXPIRED,
        ActionStatus.STALE,
    },
    ActionStatus.APPROVED: {
        ActionStatus.EXECUTING,
        ActionStatus.REJECTED,
        ActionStatus.EXPIRED,
        ActionStatus.STALE,
    },
    ActionStatus.EXECUTING: {
        ActionStatus.VERIFYING,
        ActionStatus.FAILED,
        ActionStatus.ROLLED_BACK,
    },
    ActionStatus.VERIFYING: {
        ActionStatus.SUCCEEDED,
        ActionStatus.FAILED,
        ActionStatus.ROLLED_BACK,
    },
    ActionStatus.FAILED: {ActionStatus.ROLLED_BACK},
}


class ControlPlaneError(Exception):
    """Base class for errors safe to map to the API error envelope."""

    code = "control_plane_error"


class ActionNotFoundError(ControlPlaneError):
    code = "action_not_found"


class ActionConflictError(ControlPlaneError):
    code = "action_conflict"


class ActionExpiredError(ControlPlaneError):
    code = "action_expired"


class PlanIntegrityError(ControlPlaneError):
    code = "plan_integrity_failed"


class PreconditionsChangedError(ControlPlaneError):
    code = "preconditions_changed"


class ExecutionUnavailableError(ControlPlaneError):
    code = "execution_unavailable"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def canonical_json(value: Any) -> str:
    """Return the one wire representation accepted for plan hashing."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_json_default,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class Actor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: str
    email: str | None = None
    roles: frozenset[str] = frozenset()
    verified: bool = True

    def has_role(self, role: str) -> bool:
        return role in self.roles


class ActionRequest(BaseModel):
    """A persisted action request.

    ``plan_hash`` covers every field returned by :meth:`immutable_document`.
    Runtime state (status/update/failure) is intentionally outside the hash and
    is protected by optimistic transitions in the repository.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = PLAN_SCHEMA_VERSION
    action_id: str
    action_type: str
    workspace_id: str
    environment: str
    targets: list[dict[str, Any]]
    parameters: dict[str, Any] = Field(default_factory=dict)
    preconditions: dict[str, Any] = Field(default_factory=dict)
    before_state: Any = None
    after_state: Any = None
    impact: dict[str, Any] = Field(default_factory=dict)
    rollback: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.MEDIUM
    proposer_id: str
    proposer_email: str | None = None
    created_at: datetime
    expires_at: datetime
    idempotency_key: str
    confirm_phrase: str
    plan_hash: str
    status: ActionStatus = ActionStatus.AWAITING_APPROVAL
    updated_at: datetime
    terminal_reason: str | None = None

    @model_validator(mode="after")
    def _valid_times(self) -> ActionRequest:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        return self

    def immutable_document(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "action_type": self.action_type,
            "workspace_id": self.workspace_id,
            "environment": self.environment,
            "targets": self.targets,
            "parameters": self.parameters,
            "preconditions": self.preconditions,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "impact": self.impact,
            "rollback": self.rollback,
            "verification": self.verification,
            "risk": self.risk.value,
            "proposer_id": self.proposer_id,
            "proposer_email": self.proposer_email,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
            "idempotency_key": self.idempotency_key,
            "confirm_phrase": self.confirm_phrase,
        }

    def calculated_hash(self) -> str:
        return sha256_json(self.immutable_document())

    def assert_integrity(self) -> None:
        if not self.plan_hash or self.calculated_hash() != self.plan_hash:
            raise PlanIntegrityError(
                f"Stored plan {self.action_id} no longer matches its immutable hash."
            )

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or utc_now()) > self.expires_at

    @classmethod
    def create(
        cls,
        *,
        action_type: str,
        workspace_id: str,
        environment: str,
        targets: list[dict[str, Any]],
        parameters: dict[str, Any],
        preconditions: dict[str, Any],
        before_state: Any,
        after_state: Any,
        impact: dict[str, Any],
        rollback: dict[str, Any],
        verification: dict[str, Any],
        risk: RiskLevel,
        proposer: Actor,
        now: datetime | None = None,
    ) -> ActionRequest:
        created = (now or utc_now()).astimezone(UTC)
        action_id = str(uuid.uuid4())
        item_count = len(targets)
        confirm_phrase = f"apply {action_type} {item_count}"
        values = {
            "action_id": action_id,
            "action_type": action_type,
            "workspace_id": workspace_id,
            "environment": environment,
            "targets": targets,
            "parameters": parameters,
            "preconditions": preconditions,
            "before_state": before_state,
            "after_state": after_state,
            "impact": impact,
            "rollback": rollback,
            "verification": verification,
            "risk": risk,
            "proposer_id": proposer.actor_id,
            "proposer_email": proposer.email,
            "created_at": created,
            "expires_at": created + timedelta(seconds=PLAN_TTL_SECONDS),
            "idempotency_key": str(uuid.uuid4()),
            "confirm_phrase": confirm_phrase,
            "plan_hash": "pending",
            "updated_at": created,
        }
        request = cls(**values)
        request.plan_hash = request.calculated_hash()
        return request


class ApprovalDecision(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action_id: str
    plan_hash: str
    decision: ApprovalDecision
    approver_id: str
    approver_email: str | None = None
    approver_role: str = "approver"
    confirmation: str | None = None
    decided_at: datetime = Field(default_factory=utc_now)


class ActionEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    action_id: str
    event_type: str
    from_status: ActionStatus | None = None
    to_status: ActionStatus | None = None
    actor_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    event_ts: datetime = Field(default_factory=utc_now)


class Finding(BaseModel):
    """Canonical evidence record with legacy columns retained as extras."""

    model_config = ConfigDict(extra="allow")

    finding_id: str
    workspace_id: str | None = None
    environment: str | None = None
    pillar: str
    severity: str = "MEDIUM"
    likelihood: str = "UNKNOWN"
    financial_impact_usd: float = 0
    slo_impact: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    owner: str | None = None
    affected_resources: list[dict[str, Any]] = Field(default_factory=list)
    evidence: Any = Field(default_factory=dict)
    freshness_at: datetime | str | None = None
    first_seen_at: datetime | str | None = None
    last_seen_at: datetime | str | None = None
    state: str = "OPEN"
    proposed_action_type: str | None = None
    blast_radius: str = "UNKNOWN"

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Finding:
        values = dict(row)

        def decode_json(name: str, fallback: Any) -> Any:
            value = values.pop(name, None)
            if value is None or value == "":
                return fallback
            if isinstance(value, (dict, list)):
                return value
            try:
                return json.loads(str(value))
            except json.JSONDecodeError:
                return {"details": str(value)}

        resource = values.get("resource")
        affected = decode_json(
            "affected_resources_json",
            [{"resource_id": str(resource)}] if resource else [],
        )
        if isinstance(affected, dict):
            affected = [affected]
        evidence = decode_json(
            "evidence_json",
            decode_json("details", {}),
        )
        source_identity = {
            "run_ts": values.get("run_ts"),
            "area": values.get("area"),
            "check_name": values.get("check_name"),
            "resource": resource,
            "reason": values.get("reason"),
            "action": values.get("action"),
        }
        values.update(
            {
                "finding_id": str(
                    values.get("finding_id") or sha256_json(source_identity)
                ),
                "pillar": str(
                    values.get("pillar") or values.get("area") or "RISK"
                ).upper(),
                "severity": str(values.get("severity") or "MEDIUM").upper(),
                "likelihood": str(values.get("likelihood") or "UNKNOWN").upper(),
                "financial_impact_usd": float(
                    values.get("financial_impact_usd") or 0
                ),
                "confidence": float(
                    0.5 if values.get("confidence") is None else values["confidence"]
                ),
                "affected_resources": affected,
                "evidence": evidence,
                "freshness_at": values.get("freshness_at") or values.get("run_ts"),
                "first_seen_at": values.get("first_seen_at") or values.get("run_ts"),
                "last_seen_at": values.get("last_seen_at") or values.get("run_ts"),
                "state": str(values.get("state") or "OPEN").upper(),
                "proposed_action_type": (
                    values.get("proposed_action_type") or values.get("action")
                ),
                "blast_radius": str(values.get("blast_radius") or "UNKNOWN").upper(),
            }
        )
        return cls.model_validate(values)


class PlanSpec(BaseModel):
    """Trusted output from a server-side planner."""

    model_config = ConfigDict(extra="forbid")

    action_type: str
    targets: list[dict[str, Any]]
    parameters: dict[str, Any] = Field(default_factory=dict)
    preconditions: dict[str, Any] = Field(default_factory=dict)
    before_state: Any = None
    after_state: Any = None
    impact: dict[str, Any] = Field(default_factory=dict)
    rollback: dict[str, Any] = Field(default_factory=dict)
    verification: dict[str, Any] = Field(default_factory=dict)
    risk: RiskLevel = RiskLevel.MEDIUM


class ControlPlaneRepository(Protocol):
    proposal_only: bool

    def create_action(self, action: ActionRequest) -> ActionRequest: ...

    def get_action(self, action_id: str) -> ActionRequest | None: ...

    def list_actions(
        self,
        *,
        status: ActionStatus | None = None,
        action_type: str | None = None,
        limit: int = 100,
    ) -> list[ActionRequest]: ...

    def transition(
        self,
        action_id: str,
        *,
        expected: set[ActionStatus],
        target: ActionStatus,
        actor_id: str | None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> ActionRequest: ...

    def add_approval(self, approval: ApprovalRecord) -> None: ...

    def decide_action(
        self,
        action_id: str,
        *,
        expected: ActionStatus,
        target: ActionStatus,
        approval: ApprovalRecord,
        actor_id: str,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> ActionRequest: ...

    def list_approvals(self, action_id: str) -> list[ApprovalRecord]: ...

    def add_event(self, event: ActionEvent) -> None: ...

    def list_events(self, action_id: str) -> list[ActionEvent]: ...

    def list_findings(
        self,
        *,
        pillar: str | None = None,
        state: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    def runtime_state(self, workspace_id: str, environment: str) -> dict[str, Any]: ...

    def managed_resources(
        self, workspace_id: str, environment: str
    ) -> list[dict[str, Any]]: ...


Revalidator = Callable[[ActionRequest], dict[str, Any]]


class ActionService:
    """Enforces lifecycle, integrity, authorization, expiry and freshness."""

    def __init__(
        self,
        repository: ControlPlaneRepository,
        *,
        workspace_id: str,
        environment: str,
        now: Callable[[], datetime] = utc_now,
        execution_allowlist: frozenset[str] = DEFAULT_EXECUTION_ALLOWLIST,
    ) -> None:
        self.repository = repository
        self.workspace_id = workspace_id
        self.environment = environment
        self.now = now
        self.execution_allowlist = execution_allowlist

    def plan(self, spec: PlanSpec, proposer: Actor) -> ActionRequest:
        if not proposer.verified or not proposer.has_role("proposer"):
            raise ActionConflictError(
                "A verified operator or approver is required to create a plan."
            )
        request = ActionRequest.create(
            action_type=spec.action_type,
            workspace_id=self.workspace_id,
            environment=self.environment,
            targets=spec.targets,
            parameters=spec.parameters,
            preconditions=spec.preconditions,
            before_state=spec.before_state,
            after_state=spec.after_state,
            impact=spec.impact,
            rollback=spec.rollback,
            verification=spec.verification,
            risk=spec.risk,
            proposer=proposer,
            now=self.now(),
        )
        created = self.repository.create_action(request)
        self.repository.add_event(
            ActionEvent(
                action_id=created.action_id,
                event_type="PLAN_CREATED",
                to_status=ActionStatus.AWAITING_APPROVAL,
                actor_id=proposer.actor_id,
                details={"plan_hash": created.plan_hash},
                event_ts=self.now(),
            )
        )
        return created

    def _load_valid(self, action_id: str) -> ActionRequest:
        action = self.repository.get_action(action_id)
        if action is None:
            raise ActionNotFoundError(f"Unknown action request {action_id}.")
        action.assert_integrity()
        if (
            action.workspace_id != self.workspace_id
            or action.environment != self.environment
        ):
            raise PlanIntegrityError(
                "Action request belongs to a different workspace or environment."
            )
        return action

    def _expire_if_needed(self, action: ActionRequest, actor_id: str | None) -> None:
        if action.is_expired(self.now()):
            if action.status in {ActionStatus.AWAITING_APPROVAL, ActionStatus.APPROVED}:
                self.repository.transition(
                    action.action_id,
                    expected={action.status},
                    target=ActionStatus.EXPIRED,
                    actor_id=actor_id,
                    reason="Plan TTL elapsed.",
                )
            raise ActionExpiredError(f"Action request {action.action_id} expired.")

    @staticmethod
    def _require_approver(actor: Actor) -> None:
        if not actor.verified or not actor.has_role("approver"):
            raise ActionConflictError("A verified dbx-platform approver is required.")

    @staticmethod
    def _require_confirmation(action: ActionRequest, confirmation: str | None) -> None:
        if action.risk in {RiskLevel.MEDIUM, RiskLevel.HIGH}:
            if confirmation != action.confirm_phrase:
                raise ActionConflictError(
                    f"Type the exact confirmation phrase: '{action.confirm_phrase}'."
                )

    def _revalidate(
        self,
        action: ActionRequest,
        revalidate: Revalidator,
        actor_id: str,
    ) -> None:
        current = revalidate(action)
        expected_hash = sha256_json(action.preconditions)
        actual_hash = sha256_json(current)
        if expected_hash != actual_hash:
            self.repository.transition(
                action.action_id,
                expected={action.status},
                target=ActionStatus.STALE,
                actor_id=actor_id,
                reason="Resource state changed after planning.",
                details={
                    "expected_preconditions_sha256": expected_hash,
                    "actual_preconditions_sha256": actual_hash,
                },
            )
            raise PreconditionsChangedError(
                "Resource state changed after planning; create a new plan."
            )

    def approve(
        self,
        action_id: str,
        *,
        actor: Actor,
        plan_hash: str,
        confirmation: str | None,
        revalidate: Revalidator,
    ) -> ActionRequest:
        self._require_approver(actor)
        action = self._load_valid(action_id)
        if action.status != ActionStatus.AWAITING_APPROVAL:
            raise ActionConflictError(
                f"Action request is {action.status.value}, not awaiting approval."
            )
        if plan_hash != action.plan_hash:
            raise PlanIntegrityError("Approval supplied a different plan hash.")
        self._expire_if_needed(action, actor.actor_id)
        self._require_confirmation(action, confirmation)
        self._revalidate(action, revalidate, actor.actor_id)
        approval = ApprovalRecord(
            action_id=action.action_id,
            plan_hash=action.plan_hash,
            decision=ApprovalDecision.APPROVED,
            approver_id=actor.actor_id,
            approver_email=actor.email,
            confirmation=confirmation,
            decided_at=self.now(),
        )
        return self.repository.decide_action(
            action.action_id,
            expected=ActionStatus.AWAITING_APPROVAL,
            target=ActionStatus.APPROVED,
            approval=approval,
            actor_id=actor.actor_id,
            details={"approval_id": approval.approval_id, "plan_hash": action.plan_hash},
        )

    def reject(
        self,
        action_id: str,
        *,
        actor: Actor,
        plan_hash: str,
        reason: str | None = None,
    ) -> ActionRequest:
        self._require_approver(actor)
        action = self._load_valid(action_id)
        if action.status not in {
            ActionStatus.AWAITING_APPROVAL,
            ActionStatus.APPROVED,
        }:
            raise ActionConflictError(
                f"Action request is {action.status.value} and cannot be rejected."
            )
        if plan_hash != action.plan_hash:
            raise PlanIntegrityError("Rejection supplied a different plan hash.")
        approval = ApprovalRecord(
            action_id=action.action_id,
            plan_hash=action.plan_hash,
            decision=ApprovalDecision.REJECTED,
            approver_id=actor.actor_id,
            approver_email=actor.email,
            decided_at=self.now(),
        )
        return self.repository.decide_action(
            action.action_id,
            expected=action.status,
            target=ActionStatus.REJECTED,
            approval=approval,
            actor_id=actor.actor_id,
            reason=reason or "Rejected by approver.",
            details={"approval_id": approval.approval_id},
        )

    def claim_for_execution(
        self,
        action_id: str,
        *,
        plan_hash: str,
        executor: Actor,
        revalidate: Revalidator,
    ) -> ActionRequest:
        """Internal executor entrypoint; it is intentionally not an HTTP route."""
        if self.repository.proposal_only:
            raise ExecutionUnavailableError(
                "The in-memory repository is proposal-only and cannot execute actions."
            )
        if not executor.verified or not executor.has_role("executor"):
            raise ActionConflictError("A verified executor identity is required.")
        action = self._load_valid(action_id)
        if action.action_type not in self.execution_allowlist:
            raise ActionConflictError(
                f"Action type {action.action_type!r} is not in the executor allowlist."
            )
        if action.status != ActionStatus.APPROVED:
            raise ActionConflictError(
                f"Action request is {action.status.value}, not approved."
            )
        if plan_hash != action.plan_hash:
            raise PlanIntegrityError("Executor supplied a different plan hash.")
        self._expire_if_needed(action, executor.actor_id)
        approvals = self.repository.list_approvals(action.action_id)
        if not any(
            a.decision == ApprovalDecision.APPROVED and a.plan_hash == action.plan_hash
            for a in approvals
        ):
            raise PlanIntegrityError("No matching durable approval exists.")
        self._revalidate(action, revalidate, executor.actor_id)
        return self.repository.transition(
            action.action_id,
            expected={ActionStatus.APPROVED},
            target=ActionStatus.EXECUTING,
            actor_id=executor.actor_id,
            details={"idempotency_key": action.idempotency_key},
        )


def validate_transition(current: ActionStatus, target: ActionStatus) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ActionConflictError(
            f"Invalid action transition {current.value} -> {target.value}."
        )
