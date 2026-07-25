"""Dedicated executor for approved non-runtime Mission Control actions.

The application can submit only an action ID to this unscheduled serverless
job.  The executor reloads the immutable plan and approval from Unity Catalog,
revalidates current resource state, atomically claims the action, mutates only
an explicit allowlist, verifies the result, and appends lifecycle events.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from importlib import resources
from typing import Any, Protocol

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import JobSettings, PauseStatus

from dbx_platform import governance, housekeeping, security
from dbx_platform.config import Settings

STATUS_APPROVED = "APPROVED"
STATUS_EXECUTING = "EXECUTING"
STATUS_VERIFYING = "VERIFYING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_ROLLED_BACK = "ROLLED_BACK"
JOB_RUN_VERIFICATION_TIMEOUT = timedelta(minutes=55)

ALLOWED_ACTIONS = frozenset(
    {
        "stale-clusters",
        "orphaned-jobs",
        "token-revoke",
        "policy-sync",
        "run-job",
        "configure-budget",
    }
)


class ActionExecutionError(RuntimeError):
    """An invariant failed before an allowlisted resource mutation."""


class AuditStorageUnavailableError(ActionExecutionError):
    """The append-only action record is unavailable, so execution is unsafe."""


class StaleActionError(ActionExecutionError):
    """The resource state no longer matches the approved plan."""


class ActionRolledBackError(ActionExecutionError):
    """A partial mutation failed and its exact captured state was restored."""


def canonical_json(value: Any) -> str:
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
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _impact_measurement(
    plan: Mapping[str, Any],
    verification: Mapping[str, Any],
    measured_at: datetime,
) -> dict[str, Any]:
    """Create an honest expected-versus-observed impact checkpoint.

    Resource verification is available immediately. Billing, risk, and SLO
    outcomes normally arrive later, so those dimensions stay explicitly
    pending instead of being inferred from a successful API response.
    """

    targets = list(plan.get("targets") or [])
    return {
        "status": "INITIAL_VERIFICATION_COMPLETE",
        "measured_at": _utc(measured_at).isoformat(),
        "expected": dict(plan.get("impact") or {}),
        "observed": {
            "verified": bool(verification.get("verified")),
            "verified_target_count": len(targets),
            "target_count": len(targets),
            "verification": dict(verification),
        },
        "follow_up": {
            "status": "PENDING_OBSERVATION_WINDOW",
            "measure_after": (_utc(measured_at) + timedelta(hours=24)).isoformat(),
            "financial_savings": "PENDING_SOURCE_COVERAGE",
            "risk_reduction": "PENDING_SOURCE_COVERAGE",
            "performance_change": "PENDING_SOURCE_COVERAGE",
        },
    }


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")


@dataclass(frozen=True)
class StoredAction:
    action_id: str
    action_type: str
    workspace_id: str
    environment: str
    status: str
    plan: dict[str, Any]
    plan_hash: str
    expires_at: datetime


@dataclass(frozen=True)
class StoredApproval:
    approval_id: str
    approver_id: str
    approver_email: str
    approver_role: str
    confirmation: str


class ActionStore(Protocol):
    def ensure_ready(self) -> None: ...

    def get_action(self, action_id: str) -> StoredAction | None: ...

    def get_matching_approval(
        self,
        action_id: str,
        plan_hash: str,
    ) -> StoredApproval | None: ...

    def transition(
        self,
        action_id: str,
        allowed_from: set[str],
        to_status: str,
        actor_id: str,
        details: Mapping[str, Any],
    ) -> None: ...

    def append_event(
        self,
        action_id: str,
        event_type: str,
        actor_id: str,
        details: Mapping[str, Any],
    ) -> None: ...

    def get_verification_checkpoint(
        self,
        action_id: str,
    ) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class TrustedPlan:
    targets: list[dict[str, Any]]
    execution_payload: Any

    @property
    def state_document(self) -> dict[str, Any]:
        return {
            "targets": self.targets,
            "execution_payload": self.execution_payload,
        }


@dataclass(frozen=True)
class ActionHandler:
    plan: Callable[[], TrustedPlan]
    apply: Callable[[Any], dict[str, Any]]
    verify: Callable[[Any, dict[str, Any]], dict[str, Any]]


class GovernedActionExecutor:
    """Pure orchestration around pluggable storage and trusted handlers."""

    def __init__(
        self,
        store: ActionStore,
        handlers: Mapping[str, ActionHandler],
        *,
        workspace_id: str,
        environment: str,
        executor_id: str,
        approval_validator: Callable[[StoredApproval], bool] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.handlers = handlers
        self.workspace_id = workspace_id
        self.environment = environment
        self.executor_id = executor_id
        self.approval_validator = approval_validator or (lambda _approval: True)
        self.clock = clock or (lambda: datetime.now(UTC))

    def execute(self, action_id: str) -> dict[str, Any]:
        self.store.ensure_ready()
        action = self.store.get_action(action_id)
        if action is None:
            raise ActionExecutionError(f"Unknown action ID: {action_id}")
        self._validate_record(action)
        if action.status == STATUS_SUCCEEDED:
            return {
                "action_id": action_id,
                "status": STATUS_SUCCEEDED,
                "idempotent_replay": True,
            }
        if action.status == STATUS_EXECUTING:
            checkpoint = self.store.get_verification_checkpoint(action.action_id)
            result = checkpoint.get("result") if checkpoint else None
            if isinstance(result, dict):
                # The mutation completed and the write-ahead transition intent
                # durably captured its result, but the executor crashed before
                # action_requests was advanced to VERIFYING. Complete only that
                # status transition and resume read-only verification; never
                # invoke the mutator again.
                self.store.transition(
                    action.action_id,
                    {STATUS_EXECUTING},
                    STATUS_VERIFYING,
                    self.executor_id,
                    {
                        "result": result,
                        "checkpoint": "MUTATION_APPLIED",
                        "recovered_from_transition_intent": True,
                    },
                )
                recovered = self.store.get_action(action.action_id) or action
                return self._resume_verification(recovered)
            # A fresh serverless invocation cannot prove where a previously
            # interrupted process stopped. Reapplying an allegedly idempotent
            # payload could act on expired or drifted targets, so fail closed
            # and require a newly observed and approved plan.
            self.store.transition(
                action.action_id,
                {action.status},
                STATUS_FAILED,
                self.executor_id,
                {
                    "error_type": "InterruptedExecution",
                    "reason": (
                        "A prior executor invocation ended before the durable "
                        "mutation-result checkpoint. The resource outcome is "
                        "unknown and no mutation was retried."
                    ),
                    "resource_outcome": "UNKNOWN_REQUIRES_RECONCILIATION",
                },
            )
            raise ActionExecutionError(
                "Interrupted execution cannot be resumed; create a new plan "
                "after verifying current resource state."
            )
        if action.status == STATUS_VERIFYING:
            return self._resume_verification(action)
        if action.status not in {
            STATUS_APPROVED,
        }:
            raise ActionExecutionError(
                f"Action {action_id} is {action.status}, not executable."
            )
        approval = self.store.get_matching_approval(
            action.action_id,
            action.plan_hash,
        )
        if approval is None:
            raise ActionExecutionError(
                "No durable authorized approval matches this exact plan hash."
            )
        roles = {
            part.strip().lower()
            for part in approval.approver_role.split(",")
            if part.strip()
        }
        if "approver" not in roles:
            raise ActionExecutionError("Stored approval has no approver role.")
        if not self.approval_validator(approval):
            raise ActionExecutionError(
                "Approver identity or current group membership could not be verified."
            )

        handler = self.handlers.get(action.action_type)
        if handler is None or action.action_type not in ALLOWED_ACTIONS:
            raise ActionExecutionError(
                f"Action type {action.action_type!r} is not allowlisted."
            )
        approved_payload = action.plan.get("parameters", {}).get("execution_payload")
        try:
            current = handler.plan()
        except StaleActionError as exc:
            self.store.transition(
                action.action_id,
                {STATUS_APPROVED},
                "STALE",
                self.executor_id,
                {"reason": str(exc)},
            )
            raise
        expected_state_hash = str(
            action.plan.get("preconditions", {}).get("state_sha256") or ""
        )
        actual_state_hash = canonical_hash(current.state_document)
        if not expected_state_hash or expected_state_hash != actual_state_hash:
            self.store.transition(
                action.action_id,
                {STATUS_APPROVED},
                "STALE",
                self.executor_id,
                {
                    "expected_state_sha256": expected_state_hash,
                    "actual_state_sha256": actual_state_hash,
                },
            )
            raise StaleActionError(
                "Targets or their versions changed after planning; create a new plan."
            )
        if canonical_hash(approved_payload) != canonical_hash(
            current.execution_payload
        ):
            self.store.transition(
                action.action_id,
                {STATUS_APPROVED},
                "STALE",
                self.executor_id,
                {"reason": "Approved execution payload is no longer current."},
            )
            raise StaleActionError(
                "The approved execution payload is no longer current."
            )
        self.store.transition(
            action.action_id,
            {STATUS_APPROVED},
            STATUS_EXECUTING,
            self.executor_id,
            {"plan_hash": action.plan_hash},
        )
        action = self.store.get_action(action.action_id) or action
        try:
            # A durable, exact mutation intent is the final gate. If audit
            # storage disappears after the optimistic claim, this append fails
            # before any external resource can change.
            self.store.append_event(
                action.action_id,
                "MUTATION_INTENT",
                self.executor_id,
                {
                    "plan_hash": action.plan_hash,
                    "targets": action.plan.get("targets", []),
                },
            )
            result = handler.apply(approved_payload)
            if action.status == STATUS_EXECUTING:
                self.store.transition(
                    action.action_id,
                    {STATUS_EXECUTING},
                    STATUS_VERIFYING,
                    self.executor_id,
                    {"result": result, "checkpoint": "MUTATION_APPLIED"},
                )
            return self._finish_verification(
                action,
                handler,
                approved_payload,
                result,
                resumed=False,
            )
        except ActionRolledBackError as exc:
            self.store.transition(
                action.action_id,
                {STATUS_EXECUTING, STATUS_VERIFYING},
                STATUS_ROLLED_BACK,
                self.executor_id,
                {"error_type": type(exc).__name__, "rollback": str(exc)},
            )
            raise
        except Exception as exc:
            try:
                self.store.transition(
                    action.action_id,
                    {STATUS_EXECUTING, STATUS_VERIFYING},
                    STATUS_FAILED,
                    self.executor_id,
                    {"error_type": type(exc).__name__, "error": str(exc)},
                )
            except Exception:
                # The original failure remains primary. If audit persistence also
                # fails, the job fails loudly and no retry can claim APPROVED.
                pass
            raise

    def _resume_verification(self, action: StoredAction) -> dict[str, Any]:
        """Resume only the read-only verification phase from durable evidence."""

        handler = self.handlers.get(action.action_type)
        if handler is None or action.action_type not in ALLOWED_ACTIONS:
            raise ActionExecutionError(
                f"Action type {action.action_type!r} is not allowlisted."
            )
        checkpoint = self.store.get_verification_checkpoint(action.action_id)
        result = checkpoint.get("result") if checkpoint else None
        if not isinstance(result, dict):
            self.store.transition(
                action.action_id,
                {STATUS_VERIFYING},
                STATUS_FAILED,
                self.executor_id,
                {
                    "error_type": "MissingVerificationCheckpoint",
                    "resource_outcome": "UNKNOWN_REQUIRES_RECONCILIATION",
                },
            )
            raise ActionExecutionError(
                "Interrupted verification has no durable mutation-result "
                "checkpoint; current resources require reconciliation."
            )
        payload = action.plan.get("parameters", {}).get("execution_payload")
        return self._finish_verification(
            action,
            handler,
            payload,
            result,
            resumed=True,
        )

    def _finish_verification(
        self,
        action: StoredAction,
        handler: ActionHandler,
        payload: Any,
        result: dict[str, Any],
        *,
        resumed: bool,
    ) -> dict[str, Any]:
        try:
            verification = handler.verify(payload, result)
            if not verification.get("verified"):
                raise ActionExecutionError(
                    str(verification.get("reason") or "Action verification failed.")
                )
            impact_measurement = _impact_measurement(
                action.plan,
                verification,
                self.clock(),
            )
            self.store.append_event(
                action.action_id,
                "IMPACT_MEASUREMENT",
                self.executor_id,
                impact_measurement,
            )
            self.store.transition(
                action.action_id,
                {STATUS_VERIFYING},
                STATUS_SUCCEEDED,
                self.executor_id,
                {
                    "verification": verification,
                    "impact_measurement": impact_measurement,
                    "verification_resumed": resumed,
                },
            )
            return {
                "action_id": action.action_id,
                "status": STATUS_SUCCEEDED,
                "result": result,
                "verification": verification,
                "impact_measurement": impact_measurement,
                "verification_resumed": resumed,
                "idempotent_replay": False,
            }
        except Exception as exc:
            try:
                self.store.transition(
                    action.action_id,
                    {STATUS_VERIFYING},
                    STATUS_FAILED,
                    self.executor_id,
                    {
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "mutation_result": result,
                        "verification_resumed": resumed,
                    },
                )
            except Exception:
                pass
            raise

    def _validate_record(self, action: StoredAction) -> None:
        if action.action_id != action.plan.get("action_id"):
            raise ActionExecutionError("Indexed action ID differs from immutable plan.")
        if action.action_type != action.plan.get("action_type"):
            raise ActionExecutionError("Indexed action type differs from immutable plan.")
        if action.workspace_id != self.workspace_id:
            raise ActionExecutionError("Action belongs to a different workspace.")
        if action.environment != self.environment:
            raise ActionExecutionError("Action belongs to a different environment.")
        if action.plan.get("workspace_id") != self.workspace_id:
            raise ActionExecutionError("Immutable plan workspace does not match.")
        if action.plan.get("environment") != self.environment:
            raise ActionExecutionError("Immutable plan environment does not match.")
        if canonical_hash(action.plan) != action.plan_hash:
            raise ActionExecutionError("Immutable plan payload does not match its SHA-256.")
        try:
            immutable_expiry = datetime.fromisoformat(
                str(action.plan["expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ActionExecutionError("Immutable plan expiry is malformed.") from exc
        if _utc(action.expires_at) != _utc(immutable_expiry):
            raise ActionExecutionError(
                "Indexed plan expiry differs from the immutable plan."
            )
        if _utc(self.clock()) >= _utc(immutable_expiry) and action.status not in {
            STATUS_EXECUTING,
            STATUS_VERIFYING,
            STATUS_SUCCEEDED,
        }:
            if action.status == STATUS_APPROVED:
                self.store.transition(
                    action.action_id,
                    {STATUS_APPROVED},
                    "EXPIRED",
                    self.executor_id,
                    {"reason": "Plan TTL elapsed before executor claim."},
                )
            raise ActionExecutionError("The approved plan has expired.")


def _platform_job_plan(
    w: WorkspaceClient,
    approved_target: Mapping[str, Any],
    governed_job_ids: frozenset[int],
) -> TrustedPlan:
    job_id = int(approved_target["job_id"])
    if job_id not in governed_job_ids:
        raise StaleActionError(
            "The exact Job ID is not in the executor's bundle-bound allowlist."
        )
    name = str(approved_target["name"])
    job = w.jobs.get(job_id)
    current_name = (job.settings.name if job.settings else "") or ""
    if current_name != name or "dbx-platform" not in current_name:
        raise StaleActionError("The exact owned job target changed.")
    if job.settings is None:
        raise StaleActionError("The exact owned job has no settings.")
    job_state = {
        "job_id": job_id,
        "settings": job.settings.as_dict(),
        "run_as_user_name": job.run_as_user_name,
    }
    settings_sha256 = canonical_hash(job_state)
    approved_settings_hash = str(
        approved_target.get("settings_sha256") or ""
    )
    if not approved_settings_hash or settings_sha256 != approved_settings_hash:
        raise StaleActionError("The owned job definition changed after planning.")
    if canonical_hash(approved_target.get("job_state")) != settings_sha256:
        raise StaleActionError("The immutable job-state evidence is inconsistent.")
    target = dict(approved_target)
    return TrustedPlan([target], {"job_id": job_id})


def build_handlers(w: WorkspaceClient, settings: Settings) -> dict[str, ActionHandler]:
    def stale_plan() -> TrustedPlan:
        findings = housekeeping.classify_clusters(
            housekeeping.fetch_clusters(w),
            int(time.time() * 1000),
            settings.stale_cluster_days,
            settings.max_uptime_hours,
        )
        items = [
            item for item in findings if item.get("action") == "terminate"
        ]
        return TrustedPlan(items, items)

    def stale_apply(payload: Any) -> dict[str, Any]:
        rows = list(payload or [])
        if any(row.get("action") != "terminate" for row in rows):
            raise ActionExecutionError("Only recoverable cluster termination is allowed.")
        for row in rows:
            cluster_id = str(row["cluster_id"])
            state = _enum_value(w.clusters.get(cluster_id).state)
            if state not in {"TERMINATING", "TERMINATED"}:
                w.clusters.delete(cluster_id=cluster_id)
        return {"terminated_cluster_ids": [str(row["cluster_id"]) for row in rows]}

    def stale_verify(payload: Any, _result: dict[str, Any]) -> dict[str, Any]:
        states = {
            str(row["cluster_id"]): _enum_value(
                w.clusters.get(str(row["cluster_id"])).state
            )
            for row in payload or []
        }
        ok = all(state in {"TERMINATING", "TERMINATED"} for state in states.values())
        return {"verified": ok, "states": states}

    def orphan_plan() -> TrustedPlan:
        findings = housekeeping.find_orphaned_jobs(
            housekeeping.fetch_jobs(w),
            housekeeping.fetch_active_principals(w),
        )
        items = [row for row in findings if row.get("has_schedule")]
        return TrustedPlan(items, items)

    def restore_pause_states(row: Mapping[str, Any]) -> None:
        job_id = int(row["job_id"])
        expected = row.get("pause_states")
        if not isinstance(expected, dict) or not expected:
            raise ActionExecutionError(
                f"Job {job_id} has no exact captured schedule state."
            )
        job = w.jobs.get(job_id)
        settings = job.settings
        if settings is None:
            raise ActionExecutionError(f"Job {job_id} settings are unavailable.")
        update = JobSettings()
        changed = False
        for name, expected_state in expected.items():
            if name not in {"schedule", "trigger", "continuous"}:
                raise ActionExecutionError(
                    f"Job {job_id} contains an unsupported schedule block."
                )
            block = getattr(settings, name, None)
            if block is None:
                raise ActionExecutionError(
                    f"Job {job_id} lost its {name} block during rollback."
                )
            desired = PauseStatus(str(expected_state))
            if block.pause_status != desired:
                block.pause_status = desired
                setattr(update, name, block)
                changed = True
        if changed:
            w.jobs.update(job_id=job_id, new_settings=update)

    def orphan_apply(payload: Any) -> dict[str, Any]:
        rows = list(payload or [])
        if any(
            row.get("has_schedule") is not True
            or not isinstance(row.get("pause_states"), dict)
            or not row["pause_states"]
            for row in rows
        ):
            raise ActionExecutionError(
                "Only jobs with exact captured schedule state can be paused."
            )
        job_ids = [int(row["job_id"]) for row in rows]
        changed: list[int] = []
        try:
            for row in rows:
                job_id = int(row["job_id"])
                if housekeeping.pause_job(w, job_id):
                    changed.append(job_id)
        except Exception as exc:
            rollback_errors: list[str] = []
            by_id = {int(row["job_id"]): row for row in rows}
            for job_id in reversed(changed):
                try:
                    restore_pause_states(by_id[job_id])
                except Exception as rollback_exc:  # noqa: BLE001 - report every failure
                    rollback_errors.append(
                        f"{job_id}: {type(rollback_exc).__name__}: {rollback_exc}"
                    )
            if rollback_errors:
                raise ActionExecutionError(
                    "Pausing orphaned jobs failed and rollback was incomplete: "
                    + "; ".join(rollback_errors)
                ) from exc
            raise ActionRolledBackError(
                "Pausing orphaned jobs failed; exact prior schedule states were restored."
            ) from exc
        return {"job_ids": job_ids, "changed_job_ids": changed}

    def orphan_verify(payload: Any, _result: dict[str, Any]) -> dict[str, Any]:
        states: dict[str, list[str]] = {}
        for row in payload or []:
            job = w.jobs.get(int(row["job_id"]))
            settings = job.settings
            pause_states = [
                _enum_value(getattr(settings, name).pause_status)
                for name in ("schedule", "trigger", "continuous")
                if settings is not None and getattr(settings, name, None) is not None
            ]
            states[str(row["job_id"])] = pause_states
        ok = all(
            statuses and all(status == "PAUSED" for status in statuses)
            for statuses in states.values()
        )
        return {"verified": ok, "pause_statuses": states}

    def token_plan() -> TrustedPlan:
        findings = security.classify_tokens(
            security.fetch_tokens(w),
            int(time.time() * 1000),
            settings.token_max_age_days,
            settings.token_expiry_warn_days,
        )
        items = [item for item in findings if item["over_age"]]
        return TrustedPlan(items, items)

    def token_apply(payload: Any) -> dict[str, Any]:
        rows = list(payload or [])
        if any(row.get("over_age") is not True for row in rows):
            raise ActionExecutionError("Executor can revoke only over-age PAT findings.")
        remaining = {
            str(token.token_id) for token in w.token_management.list()
        }
        for row in rows:
            token_id = str(row["token_id"])
            if token_id in remaining:
                w.token_management.delete(token_id=token_id)
        return {"revoked_token_ids": [str(row["token_id"]) for row in rows]}

    def token_verify(payload: Any, _result: dict[str, Any]) -> dict[str, Any]:
        remaining = {
            str(token.token_id) for token in w.token_management.list()
        }
        requested = {str(row["token_id"]) for row in payload or []}
        found = sorted(requested & remaining)
        return {"verified": not found, "remaining_token_ids": found}

    policies_dir = resources.files("dbx_platform") / "policies"

    def policy_plan() -> TrustedPlan:
        plan = governance.diff_policies(
            governance.load_local_policies(policies_dir),
            governance.fetch_remote_policies(w),
        )
        items = (
            [{"name": row["name"], "action": "create"} for row in plan["create"]]
            + [{"name": row["name"], "action": "update"} for row in plan["update"]]
        )
        return TrustedPlan(items, plan)

    def policy_apply(payload: Any) -> dict[str, Any]:
        plan = dict(payload or {})
        if set(plan) != {"create", "update", "unchanged", "unmanaged"}:
            raise ActionExecutionError("Cluster-policy payload has unexpected operations.")
        current = governance.diff_policies(
            governance.load_local_policies(policies_dir),
            governance.fetch_remote_policies(w),
        )
        approved_names = {
            str(row["name"])
            for operation in ("create", "update")
            for row in plan[operation]
        }
        unexpected = [
            str(row["name"])
            for operation in ("create", "update")
            for row in current[operation]
            if str(row["name"]) not in approved_names
        ]
        if unexpected:
            raise StaleActionError(
                "Unapproved cluster-policy drift appeared during execution: "
                + ", ".join(sorted(unexpected))
            )
        remaining = {
            **current,
            "create": [
                row for row in current["create"] if row["name"] in approved_names
            ],
            "update": [
                row for row in current["update"] if row["name"] in approved_names
            ],
        }
        results = governance.apply_policy_plan(w, remaining)
        return {"changes": results}

    def policy_verify(_payload: Any, _result: dict[str, Any]) -> dict[str, Any]:
        remaining = policy_plan()
        unresolved = [
            row for row in remaining.targets if row.get("action") in {"create", "update"}
        ]
        return {"verified": not unresolved, "unresolved": unresolved}

    def unsupported_job_plan() -> TrustedPlan:
        raise ActionExecutionError("run-job requires the exact target from the plan.")

    def job_verify(_payload: Any, result: dict[str, Any]) -> dict[str, Any]:
        expected_run_id = int(result["run_id"])
        expected_job_id = int(result["job_id"])
        # The launch event is written before this wait, allowing the child Job
        # to validate that it is the exact approved run. The action itself is
        # not successful until Databricks reports that exact run terminated
        # successfully.
        run = w.jobs.wait_get_run_job_terminated_or_skipped(
            expected_run_id,
            timeout=JOB_RUN_VERIFICATION_TIMEOUT,
        )
        run_id = int(getattr(run, "run_id", 0) or 0)
        job_id = int(getattr(run, "job_id", 0) or 0)
        life_cycle_state = _enum_value(
            run.state.life_cycle_state if run.state else None
        )
        result_state = _enum_value(run.state.result_state if run.state else None)
        verified = (
            run_id == expected_run_id
            and job_id == expected_job_id
            and life_cycle_state == "TERMINATED"
            and result_state == "SUCCESS"
        )
        return {
            "verified": verified,
            "reason": (
                None
                if verified
                else (
                    "The exact governed Job run did not terminate with SUCCESS "
                    f"(job_id={job_id}, run_id={run_id}, "
                    f"life_cycle_state={life_cycle_state}, "
                    f"result_state={result_state})."
                )
            ),
            "run_id": run_id,
            "job_id": job_id,
            "life_cycle_state": life_cycle_state,
            "result_state": result_state,
            "state_message": str(
                run.state.state_message if run.state else ""
            ),
        }

    return {
        "stale-clusters": ActionHandler(stale_plan, stale_apply, stale_verify),
        "orphaned-jobs": ActionHandler(orphan_plan, orphan_apply, orphan_verify),
        "token-revoke": ActionHandler(token_plan, token_apply, token_verify),
        "policy-sync": ActionHandler(policy_plan, policy_apply, policy_verify),
        # The plan callable is replaced from the immutable job target at load
        # time by ``bind_action_handler``.
        "run-job": ActionHandler(
            unsupported_job_plan,
            lambda _payload: {},
            job_verify,
        ),
    }


def bind_action_handler(
    action: StoredAction,
    handlers: Mapping[str, ActionHandler],
    w: WorkspaceClient,
    *,
    governed_job_ids: frozenset[int] = frozenset(),
) -> dict[str, ActionHandler]:
    """Bind run-job revalidation to the exact immutable target."""

    bound = dict(handlers)
    if action.action_type == "run-job":
        if len(action.plan.get("targets", [])) != 1:
            raise ActionExecutionError("run-job must contain exactly one target.")
        target = action.plan["targets"][0]
        job_id = int(target["job_id"])
        current = bound["run-job"]

        def run_job(payload: Any) -> dict[str, Any]:
            if not isinstance(payload, dict) or set(payload) != {"job_id"}:
                raise ActionExecutionError("run-job accepts exactly one job_id.")
            if int(payload["job_id"]) != job_id:
                raise ActionExecutionError("run-job payload differs from its exact target.")
            # Databricks deduplicates idempotency tokens across the workspace,
            # not per Job. The app already uses the plan key when it submits
            # this executor, so reusing it here would return the executor's own
            # run instead of launching the governed child Job.
            child_idempotency_token = hashlib.sha256(
                f"run-job:{action.plan['idempotency_key']}:{job_id}".encode()
            ).hexdigest()
            run = w.jobs.run_now(
                job_id=job_id,
                idempotency_token=child_idempotency_token,
                job_parameters={
                    "approved_action_id": action.action_id,
                    "approved_plan_hash": action.plan_hash,
                },
            )
            return {"run_id": int(run.run_id), "job_id": job_id}

        bound["run-job"] = ActionHandler(
            lambda: _platform_job_plan(w, target, governed_job_ids),
            run_job,
            current.verify,
        )
    return bound


_BUDGET_DESIRED_FIELDS = (
    "budget_id",
    "workspace_id",
    "environment",
    "scope_type",
    "scope_value",
    "cost_basis",
    "month",
    "currency",
    "amount",
    "warning_pct",
    "critical_pct",
    "status",
)
_BUDGET_CURRENT_FIELDS = (
    *_BUDGET_DESIRED_FIELDS,
    "plan_hash",
    "updated_by",
    "updated_at",
)


def _json_scalar(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _normalize_budget_row(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    normalized = {
        key: _json_scalar(row.get(key))
        for key in _BUDGET_CURRENT_FIELDS
    }
    if normalized["amount"] is not None:
        normalized["amount"] = float(normalized["amount"])
    for name in ("warning_pct", "critical_pct"):
        if normalized[name] is not None:
            normalized[name] = int(normalized[name])
    return normalized


def _validate_budget_payload(
    action: StoredAction,
    payload: Any,
) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
    if not isinstance(payload, dict) or set(payload) != {
        "operation",
        "budget_id",
        "expected_before",
        "desired",
    }:
        raise ActionExecutionError(
            "configure-budget accepts only the canonical budget upsert payload."
        )
    if payload["operation"] != "UPSERT_LLM_BUDGET":
        raise ActionExecutionError("Unsupported budget operation.")
    budget_id = str(payload["budget_id"])
    desired_raw = payload["desired"]
    if not isinstance(desired_raw, dict) or set(desired_raw) != set(
        _BUDGET_DESIRED_FIELDS
    ):
        raise ActionExecutionError("Budget desired state is malformed.")
    desired = {name: _json_scalar(desired_raw[name]) for name in _BUDGET_DESIRED_FIELDS}
    if budget_id != str(desired["budget_id"]):
        raise ActionExecutionError("Budget ID differs from the desired state.")
    if str(desired["workspace_id"]) != action.workspace_id:
        raise ActionExecutionError("Budget belongs to a different workspace.")
    if str(desired["environment"]) != action.environment:
        raise ActionExecutionError("Budget belongs to a different environment.")
    if desired["cost_basis"] not in {
        "DATABRICKS_LIST",
        "AZURE_ACTUAL",
        "PROVIDER_ESTIMATE",
    }:
        raise ActionExecutionError("Budget cost basis is not allowlisted.")
    if desired["scope_type"] not in {"workspace", "provider", "team", "use_case"}:
        raise ActionExecutionError("Budget scope type is not allowlisted.")
    if desired["status"] != "ACTIVE":
        raise ActionExecutionError("Only active budget upserts are supported.")
    if not (
        isinstance(desired["currency"], str)
        and len(desired["currency"]) == 3
        and desired["currency"].isalpha()
        and desired["currency"].isupper()
    ):
        raise ActionExecutionError("Budget currency is malformed.")
    try:
        amount = float(desired["amount"])
        warning = int(desired["warning_pct"])
        critical = int(desired["critical_pct"])
        date.fromisoformat(str(desired["month"]))
    except (TypeError, ValueError) as exc:
        raise ActionExecutionError("Budget numeric or month value is malformed.") from exc
    if not math.isfinite(amount) or amount <= 0:
        raise ActionExecutionError("Budget amount must be finite and positive.")
    if not 0 <= warning <= critical <= 100:
        raise ActionExecutionError("Budget thresholds are malformed.")
    desired["amount"] = amount
    desired["warning_pct"] = warning
    desired["critical_pct"] = critical
    expected = payload["expected_before"]
    if expected is not None:
        if not isinstance(expected, dict):
            raise ActionExecutionError("Budget before-state is malformed.")
        expected = _normalize_budget_row(expected)
    return budget_id, expected, desired


def _read_budget(
    spark: Any,
    fq: str,
    *,
    budget_id: str,
    workspace_id: str,
    environment: str,
) -> dict[str, Any] | None:
    rows = spark.sql(
        f"""
        SELECT {", ".join(_BUDGET_CURRENT_FIELDS)}
        FROM {fq}.llm_budgets
        WHERE budget_id = {_sql_string(budget_id)}
          AND workspace_id = {_sql_string(workspace_id)}
          AND environment = {_sql_string(environment)}
        LIMIT 2
        """
    ).collect()
    if len(rows) > 1:
        raise ActionExecutionError("Duplicate rows exist for the exact budget target.")
    if not rows:
        return None
    return _normalize_budget_row(rows[0].asDict(recursive=True))


def _budget_matches(
    current: Mapping[str, Any] | None,
    desired: Mapping[str, Any],
    plan_hash: str,
) -> bool:
    return bool(
        current
        and all(current.get(name) == desired.get(name) for name in _BUDGET_DESIRED_FIELDS)
        and current.get("plan_hash") == plan_hash
    )


def bind_budget_handler(
    action: StoredAction,
    handlers: Mapping[str, ActionHandler],
    spark: Any,
    *,
    catalog: str,
    schema: str,
    executor_id: str,
) -> dict[str, ActionHandler]:
    """Bind an exact, idempotent LLM budget upsert to its immutable plan."""

    bound = dict(handlers)
    if action.action_type != "configure-budget":
        return bound
    targets = action.plan.get("targets", [])
    if len(targets) != 1:
        raise ActionExecutionError("configure-budget must contain exactly one target.")
    target = dict(targets[0])
    payload = action.plan.get("parameters", {}).get("execution_payload")
    budget_id, expected_before, desired = _validate_budget_payload(action, payload)
    if (
        target.get("resource_type") != "LLM_BUDGET"
        or str(target.get("resource_id")) != budget_id
        or str(target.get("budget_id")) != budget_id
    ):
        raise ActionExecutionError("Budget target differs from its execution payload.")
    fq = f"`{catalog}`.`{schema}`"

    def current_budget() -> dict[str, Any] | None:
        return _read_budget(
            spark,
            fq,
            budget_id=budget_id,
            workspace_id=action.workspace_id,
            environment=action.environment,
        )

    def budget_plan() -> TrustedPlan:
        current = current_budget()
        current_payload = {
            "operation": "UPSERT_LLM_BUDGET",
            "budget_id": budget_id,
            "expected_before": current,
            "desired": desired,
        }
        return TrustedPlan([target], current_payload)

    def budget_apply(approved_payload: Any) -> dict[str, Any]:
        approved_id, approved_before, approved_desired = _validate_budget_payload(
            action,
            approved_payload,
        )
        if approved_id != budget_id or canonical_hash(approved_desired) != canonical_hash(
            desired
        ):
            raise ActionExecutionError("Budget payload differs from the bound plan.")
        current = current_budget()
        if _budget_matches(current, desired, action.plan_hash):
            return {"budget_id": budget_id, "changed": False}
        if canonical_hash(current) != canonical_hash(approved_before):
            raise StaleActionError("Budget changed after approval.")
        spark.sql(
            f"""
            MERGE INTO {fq}.llm_budgets AS target
            USING (
              SELECT
                {_sql_string(budget_id)} AS budget_id,
                {_sql_string(action.workspace_id)} AS workspace_id,
                {_sql_string(action.environment)} AS environment
            ) AS source
            ON target.budget_id = source.budget_id
              AND target.workspace_id = source.workspace_id
              AND target.environment = source.environment
            WHEN MATCHED THEN UPDATE SET
              scope_type = {_sql_string(desired["scope_type"])},
              scope_value = {_sql_string(desired["scope_value"])},
              cost_basis = {_sql_string(desired["cost_basis"])},
              month = CAST({_sql_string(desired["month"])} AS DATE),
              currency = {_sql_string(desired["currency"])},
              amount = {float(desired["amount"])},
              warning_pct = {int(desired["warning_pct"])},
              critical_pct = {int(desired["critical_pct"])},
              status = 'ACTIVE',
              plan_hash = {_sql_string(action.plan_hash)},
              updated_by = {_sql_string(executor_id)},
              updated_at = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (
              budget_id, workspace_id, environment, scope_type, scope_value,
              cost_basis, month, currency, amount, warning_pct, critical_pct,
              status, plan_hash, updated_by, updated_at
            ) VALUES (
              {_sql_string(budget_id)}, {_sql_string(action.workspace_id)},
              {_sql_string(action.environment)}, {_sql_string(desired["scope_type"])},
              {_sql_string(desired["scope_value"])}, {_sql_string(desired["cost_basis"])},
              CAST({_sql_string(desired["month"])} AS DATE),
              {_sql_string(desired["currency"])}, {float(desired["amount"])},
              {int(desired["warning_pct"])}, {int(desired["critical_pct"])},
              'ACTIVE', {_sql_string(action.plan_hash)}, {_sql_string(executor_id)},
              CURRENT_TIMESTAMP()
            )
            """
        )
        return {"budget_id": budget_id, "changed": True}

    def budget_verify(
        _approved_payload: Any,
        _result: dict[str, Any],
    ) -> dict[str, Any]:
        current = current_budget()
        return {
            "verified": _budget_matches(current, desired, action.plan_hash),
            "budget_id": budget_id,
            "plan_hash": current.get("plan_hash") if current else None,
        }

    bound["configure-budget"] = ActionHandler(
        budget_plan,
        budget_apply,
        budget_verify,
    )
    return bound


def _sql_string(value: Any) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


class SparkActionStore:
    """Minimal Delta store; it validates but never creates audit storage."""

    def __init__(
        self,
        spark: Any,
        catalog: str,
        schema: str,
        *,
        workspace_id: str,
        environment: str,
    ) -> None:
        if not catalog.replace("_", "").isalnum() or not schema.replace("_", "").isalnum():
            raise ValueError("Unsafe Unity Catalog identifier.")
        if not workspace_id or not environment:
            raise ValueError("Workspace and environment scope are required.")
        self.spark = spark
        self.fq = f"`{catalog}`.`{schema}`"
        self.workspace_id = workspace_id
        self.environment = environment

    def ensure_ready(self) -> None:
        try:
            rows = self.spark.sql(f"SHOW TABLES IN {self.fq}").collect()
        except Exception as exc:
            raise AuditStorageUnavailableError(
                "Mission Control audit storage is unavailable."
            ) from exc
        available = {
            str(row.asDict(recursive=True).get("tableName") or "").lower()
            for row in rows
        }
        required = {"action_requests", "action_approvals", "action_events"}
        missing = sorted(required - available)
        if missing:
            raise AuditStorageUnavailableError(
                "Mission Control audit tables are missing: " + ", ".join(missing)
            )
        try:
            # Prove both UPDATE and append-only event permissions before an
            # action can be claimed. The preflight event is intentionally kept.
            self.spark.sql(
                f"UPDATE {self.fq}.action_requests SET status = status WHERE 1 = 0"
            )
            now = datetime.now(UTC)
            self._insert_event(
                action_id=f"storage-preflight:{uuid.uuid4()}",
                event_type="STORAGE_PREFLIGHT",
                from_status=None,
                to_status=None,
                actor_id="action-executor",
                details={"status": "ready"},
                event_at=now,
            )
        except Exception as exc:
            raise AuditStorageUnavailableError(
                "Mission Control audit storage is not writable."
            ) from exc

    def get_action(self, action_id: str) -> StoredAction | None:
        rows = self.spark.sql(
            f"""
            SELECT action_id, action_type, workspace_id, environment, status,
                   plan_json, plan_hash, expires_at
            FROM {self.fq}.action_requests
            WHERE action_id = {_sql_string(action_id)}
              AND workspace_id = {_sql_string(self.workspace_id)}
              AND environment = {_sql_string(self.environment)}
            ORDER BY updated_at DESC
            LIMIT 2
            """
        ).collect()
        if not rows:
            return None
        if len(rows) != 1:
            raise ActionExecutionError(f"Duplicate action ID: {action_id}")
        row = rows[0].asDict(recursive=True)
        try:
            plan = json.loads(row["plan_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ActionExecutionError("Stored plan JSON is malformed.") from exc
        return StoredAction(
            action_id=str(row["action_id"]),
            action_type=str(row["action_type"]),
            workspace_id=str(row["workspace_id"]),
            environment=str(row["environment"]),
            status=str(row["status"]),
            plan=plan,
            plan_hash=str(row["plan_hash"]),
            expires_at=_utc(row["expires_at"]),
        )

    def get_matching_approval(
        self,
        action_id: str,
        plan_hash: str,
    ) -> StoredApproval | None:
        rows = self.spark.sql(
            f"""
            SELECT approval_id, approver_id, approver_email, approver_role, confirmation
            FROM {self.fq}.action_approvals
            WHERE action_id = {_sql_string(action_id)}
              AND plan_hash = {_sql_string(plan_hash)}
              AND workspace_id = {_sql_string(self.workspace_id)}
              AND environment = {_sql_string(self.environment)}
              AND decision = 'APPROVED'
            LIMIT 2
            """
        ).collect()
        if len(rows) != 1:
            return None
        row = rows[0].asDict(recursive=True)
        return StoredApproval(
            approval_id=str(row.get("approval_id") or ""),
            approver_id=str(row.get("approver_id") or ""),
            approver_email=str(row.get("approver_email") or ""),
            approver_role=str(row.get("approver_role") or ""),
            confirmation=str(row.get("confirmation") or ""),
        )

    def get_verification_checkpoint(
        self,
        action_id: str,
    ) -> dict[str, Any] | None:
        rows = self.spark.sql(
            f"""
            SELECT details_json
            FROM {self.fq}.action_events
            WHERE action_id = {_sql_string(action_id)}
              AND workspace_id = {_sql_string(self.workspace_id)}
              AND environment = {_sql_string(self.environment)}
              AND to_status = {_sql_string(STATUS_VERIFYING)}
              AND event_type IN ('STATUS_VERIFYING', 'TRANSITION_INTENT')
            ORDER BY
              CASE WHEN event_type = 'STATUS_VERIFYING' THEN 0 ELSE 1 END,
              event_ts DESC
            LIMIT 1
            """
        ).collect()
        if not rows:
            return None
        raw = rows[0].asDict(recursive=True).get("details_json")
        try:
            details = json.loads(str(raw))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ActionExecutionError(
                "The durable verification checkpoint is malformed."
            ) from exc
        if (
            not isinstance(details, dict)
            or details.get("checkpoint") != "MUTATION_APPLIED"
        ):
            return None
        return details

    def transition(
        self,
        action_id: str,
        allowed_from: set[str],
        to_status: str,
        actor_id: str,
        details: Mapping[str, Any],
    ) -> None:
        current = self.get_action(action_id)
        if current is None or current.status not in allowed_from:
            actual = current.status if current else "MISSING"
            raise ActionExecutionError(
                f"Invalid action transition {actual} -> {to_status}."
            )
        now = datetime.now(UTC)
        allowed = ", ".join(_sql_string(value) for value in sorted(allowed_from))
        terminal = (
            canonical_json(details)
            if to_status in {"STALE", STATUS_FAILED, "ROLLED_BACK"}
            else None
        )
        self._insert_event(
            action_id=action_id,
            event_type="TRANSITION_INTENT",
            from_status=current.status,
            to_status=to_status,
            actor_id=actor_id,
            details={"target_status": to_status, **dict(details)},
            event_at=now,
        )
        self.spark.sql(
            f"""
            UPDATE {self.fq}.action_requests
            SET status = {_sql_string(to_status)},
                updated_at = TIMESTAMP({_sql_string(now.isoformat())}),
                terminal_reason = {_sql_string(terminal)}
            WHERE action_id = {_sql_string(action_id)}
              AND workspace_id = {_sql_string(self.workspace_id)}
              AND environment = {_sql_string(self.environment)}
              AND status IN ({allowed})
            """
        )
        updated = self.get_action(action_id)
        if updated is None or updated.status != to_status:
            raise ActionExecutionError("Concurrent executor claim was rejected.")
        self._insert_event(
            action_id=action_id,
            event_type=f"STATUS_{to_status}",
            from_status=current.status,
            to_status=to_status,
            actor_id=actor_id,
            details=details,
            event_at=now,
        )

    def append_event(
        self,
        action_id: str,
        event_type: str,
        actor_id: str,
        details: Mapping[str, Any],
    ) -> None:
        current = self.get_action(action_id)
        if current is None:
            raise ActionExecutionError("Cannot audit an unknown action.")
        self._insert_event(
            action_id=action_id,
            event_type=event_type,
            from_status=current.status,
            to_status=None,
            actor_id=actor_id,
            details=details,
            event_at=datetime.now(UTC),
        )

    def _insert_event(
        self,
        *,
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
            INSERT INTO {self.fq}.action_events (
              workspace_id, environment, event_id, action_id, event_type,
              from_status, to_status, actor_id, details_json, event_ts
            ) VALUES (
              {_sql_string(self.workspace_id)}, {_sql_string(self.environment)},
              {_sql_string(str(uuid.uuid4()))}, {_sql_string(action_id)},
              {_sql_string(event_type)}, {_sql_string(from_status)},
              {_sql_string(to_status)}, {_sql_string(actor_id)},
              {_sql_string(canonical_json(dict(details)))},
              TIMESTAMP({_sql_string(event_at.isoformat())})
            )
            """
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--environment", default="prod")
    parser.add_argument("--catalog", default="main")
    parser.add_argument("--schema", default="dbx_platform")
    parser.add_argument("--expected-executor", required=True)
    parser.add_argument("--approver-group", default="dbx-platform-approvers")
    parser.add_argument("--approver-group-id", required=True)
    parser.add_argument("--governed-job-id", action="append", type=int, default=[])
    return parser


def _approver_is_current_member(
    w: WorkspaceClient,
    approval: StoredApproval,
    group_name: str,
    group_id: str,
) -> bool:
    """Inspect the one configured account group for the immutable approver ID.

    Cross-user workspace SCIM reads require broader identity-admin access.
    Reading the exact account group through the workspace proxy requires only
    manager access to that one group and avoids enumerating users or groups.
    """

    if not group_id:
        return False
    try:
        group = w.api_client.do(
            "GET",
            f"/api/2.0/account/scim/v2/Groups/{group_id}",
            headers={"Accept": "application/scim+json"},
        )
    except Exception:  # noqa: BLE001 - authorization failures are uniform
        return False
    if (
        not isinstance(group, Mapping)
        or str(group.get("id") or "") != group_id
        or str(group.get("displayName") or "") != group_name
    ):
        return False
    # SCIM member display is optional, so the account-scoped immutable ID is
    # the load-bearing identity check.
    return any(
        str(member.get("value") or "") == approval.approver_id
        for member in (group.get("members") or [])
        if isinstance(member, Mapping)
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from pyspark.sql import SparkSession

        w = WorkspaceClient()
        me = w.current_user.me()
        identities = {
            str(getattr(me, "id", "") or ""),
            str(getattr(me, "user_name", "") or ""),
            str(getattr(me, "application_id", "") or ""),
        }
        if args.expected_executor not in identities:
            raise ActionExecutionError(
                "Current job identity is not the configured executor service principal."
            )
        spark = SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
        workspace_id = str(w.get_workspace_id())
        store = SparkActionStore(
            spark,
            args.catalog,
            args.schema,
            workspace_id=workspace_id,
            environment=args.environment,
        )
        store.ensure_ready()
        action = store.get_action(args.action_id)
        if action is None:
            raise ActionExecutionError(f"Unknown action ID: {args.action_id}")
        handlers = bind_action_handler(
            action,
            build_handlers(w, Settings.from_env()),
            w,
            governed_job_ids=frozenset(args.governed_job_id),
        )
        executor_identity = next(
            (value for value in identities if value == args.expected_executor),
            "",
        )
        handlers = bind_budget_handler(
            action,
            handlers,
            spark,
            catalog=args.catalog,
            schema=args.schema,
            executor_id=executor_identity,
        )
        executor = GovernedActionExecutor(
            store,
            handlers,
            workspace_id=workspace_id,
            environment=args.environment,
            executor_id=executor_identity,
            approval_validator=lambda approval: _approver_is_current_member(
                w,
                approval,
                args.approver_group,
                args.approver_group_id,
            ),
        )
        result = executor.execute(args.action_id)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ActionExecutionError as exc:
        print(f"action execution refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"action execution failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def entry(argv: Sequence[str] | None = None) -> None:
    """Exit only on failure; serverless Spark treats ``SystemExit(0)`` as failed."""

    code = main(argv)
    if code:
        raise SystemExit(code)


if __name__ == "__main__":
    entry()
