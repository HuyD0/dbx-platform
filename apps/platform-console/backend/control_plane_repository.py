"""Control-plane persistence.

Production uses append-friendly Unity Catalog Delta tables.  The in-memory
implementation is intentionally marked ``proposal_only`` and is selected only
for local development or tests; an executor refuses to operate against it.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from typing import Any

from backend.control_plane import (
    ActionConflictError,
    ActionEvent,
    ActionRequest,
    ActionStatus,
    ApprovalRecord,
    Finding,
    PlanIntegrityError,
    canonical_json,
    utc_now,
    validate_transition,
)
from dbx_platform.system_tables import run_query

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _clone_model(model):
    return type(model).model_validate(model.model_dump(mode="json"))


def _rank_findings(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    severity = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}
    rows.sort(key=lambda row: str(row.get("first_seen_at") or ""))
    rows.sort(
        key=lambda row: float(row.get("financial_impact_usd") or 0),
        reverse=True,
    )
    rows.sort(
        key=lambda row: severity.get(
            str(row.get("severity", "INFO")).upper(), 0
        ),
        reverse=True,
    )
    return rows


class InMemoryControlPlaneRepository:
    """Thread-safe local/test repository; never valid for resource execution."""

    proposal_only = True

    def __init__(
        self,
        workspace_id: str | None = None,
        environment: str | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.environment = environment
        self._actions: dict[str, ActionRequest] = {}
        self._approvals: list[ApprovalRecord] = []
        self._events: list[ActionEvent] = []
        self._findings: list[dict[str, Any]] = []
        self._resources: list[dict[str, Any]] = []
        self._runtime: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.RLock()

    def _in_scope(self, action: ActionRequest) -> bool:
        return (
            (self.workspace_id is None or action.workspace_id == self.workspace_id)
            and (self.environment is None or action.environment == self.environment)
        )

    def create_action(self, action: ActionRequest) -> ActionRequest:
        action.assert_integrity()
        if not self._in_scope(action):
            raise ActionConflictError(
                "Action request belongs to a different workspace or environment."
            )
        with self._lock:
            if action.action_id in self._actions:
                raise ActionConflictError(f"Action request {action.action_id} already exists.")
            self._actions[action.action_id] = _clone_model(action)
        return _clone_model(action)

    def get_action(self, action_id: str) -> ActionRequest | None:
        with self._lock:
            action = self._actions.get(action_id)
            return _clone_model(action) if action and self._in_scope(action) else None

    def list_actions(
        self,
        *,
        status: ActionStatus | None = None,
        action_type: str | None = None,
        limit: int = 100,
    ) -> list[ActionRequest]:
        with self._lock:
            rows = [
                value
                for value in self._actions.values()
                if self._in_scope(value)
                and (status is None or value.status == status)
                and (action_type is None or value.action_type == action_type)
            ]
            rows.sort(key=lambda value: value.created_at, reverse=True)
            return [_clone_model(value) for value in rows[: max(1, min(limit, 500))]]

    def transition(
        self,
        action_id: str,
        *,
        expected: set[ActionStatus],
        target: ActionStatus,
        actor_id: str | None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> ActionRequest:
        with self._lock:
            action = self._actions.get(action_id)
            if action is None or not self._in_scope(action):
                raise ActionConflictError(f"Unknown action request {action_id}.")
            action.assert_integrity()
            if action.status not in expected:
                raise ActionConflictError(
                    f"Action request is {action.status.value}; expected "
                    f"{', '.join(sorted(s.value for s in expected))}."
                )
            validate_transition(action.status, target)
            prior = action.status
            action.status = target
            action.updated_at = utc_now()
            action.terminal_reason = reason
            event = ActionEvent(
                action_id=action_id,
                event_type=f"STATUS_{target.value}",
                from_status=prior,
                to_status=target,
                actor_id=actor_id,
                details={"reason": reason, **(details or {})},
                event_ts=action.updated_at,
            )
            self._events.append(event)
            return _clone_model(action)

    def add_approval(self, approval: ApprovalRecord) -> None:
        with self._lock:
            action = self._actions.get(approval.action_id)
            if action is None or not self._in_scope(action):
                raise ActionConflictError("Cannot approve an out-of-scope action.")
            if any(row.approval_id == approval.approval_id for row in self._approvals):
                raise ActionConflictError(f"Approval {approval.approval_id} already exists.")
            self._approvals.append(_clone_model(approval))

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
    ) -> ActionRequest:
        """Atomically persist the human decision, transition, and audit event."""

        with self._lock:
            action = self._actions.get(action_id)
            if action is None or not self._in_scope(action):
                raise ActionConflictError(f"Unknown action request {action_id}.")
            action.assert_integrity()
            if action.status != expected:
                raise ActionConflictError(
                    f"Action request is {action.status.value}; expected "
                    f"{expected.value}."
                )
            validate_transition(action.status, target)
            expected_decision = (
                "APPROVED" if target == ActionStatus.APPROVED else "REJECTED"
            )
            if (
                approval.action_id != action_id
                or approval.plan_hash != action.plan_hash
                or approval.decision.value != expected_decision
            ):
                raise ActionConflictError(
                    "Approval evidence does not match the exact decision."
                )
            if any(
                row.action_id == action_id and row.plan_hash == action.plan_hash
                for row in self._approvals
            ):
                raise ActionConflictError(
                    "A decision already exists for this exact plan."
                )
            prior = action.status
            action.status = target
            action.updated_at = approval.decided_at
            action.terminal_reason = reason
            self._approvals.append(_clone_model(approval))
            self._events.append(
                ActionEvent(
                    action_id=action_id,
                    event_type=f"STATUS_{target.value}",
                    from_status=prior,
                    to_status=target,
                    actor_id=actor_id,
                    details={"reason": reason, **(details or {})},
                    event_ts=approval.decided_at,
                )
            )
            return _clone_model(action)

    def list_approvals(self, action_id: str) -> list[ApprovalRecord]:
        with self._lock:
            action = self._actions.get(action_id)
            if action is None or not self._in_scope(action):
                return []
            return [
                _clone_model(row) for row in self._approvals if row.action_id == action_id
            ]

    def add_event(self, event: ActionEvent) -> None:
        with self._lock:
            action = self._actions.get(event.action_id)
            if action is None or not self._in_scope(action):
                raise ActionConflictError("Cannot append to an out-of-scope action.")
            if any(row.event_id == event.event_id for row in self._events):
                raise ActionConflictError(f"Event {event.event_id} already exists.")
            self._events.append(_clone_model(event))

    def list_events(self, action_id: str) -> list[ActionEvent]:
        with self._lock:
            action = self._actions.get(action_id)
            if action is None or not self._in_scope(action):
                return []
            rows = [row for row in self._events if row.action_id == action_id]
            rows.sort(key=lambda row: row.event_ts)
            return [_clone_model(row) for row in rows]

    def list_findings(
        self,
        *,
        pillar: str | None = None,
        state: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                Finding.from_row(dict(row)).model_dump(mode="json")
                for row in self._findings
            ]
        rows = [
            row
            for row in rows
            if (pillar is None or row.get("pillar") == pillar)
            and (state is None or row.get("state") == state)
        ]
        _rank_findings(rows)
        return rows[: max(1, min(limit, 1000))]

    def runtime_state(self, workspace_id: str, environment: str) -> dict[str, Any]:
        with self._lock:
            value = self._runtime.get((workspace_id, environment))
            if value:
                return dict(value)
        return {
            "workspace_id": workspace_id,
            "environment": environment,
            "desired_state": "ON",
            "actual_state": "UNKNOWN",
            "prior_state": None,
            "active_action_id": None,
            "last_reconciled_at": None,
            "updated_at": None,
            "version": 0,
            "source": "proposal-only-memory",
        }

    def managed_resources(
        self, workspace_id: str, environment: str
    ) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(row)
                for row in self._resources
                if row.get("workspace_id") == workspace_id
                and row.get("environment") == environment
            ]

    # Test/local seeding helpers. They are not part of the production protocol.
    def add_finding(self, finding: dict[str, Any]) -> None:
        with self._lock:
            self._findings.append(dict(finding))

    def set_runtime_state(self, value: dict[str, Any]) -> None:
        key = (str(value["workspace_id"]), str(value["environment"]))
        with self._lock:
            self._runtime[key] = dict(value)

    def add_managed_resource(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._resources.append(dict(value))


ACTION_REQUEST_COLUMNS = """
workspace_id STRING NOT NULL,
environment STRING NOT NULL,
action_id STRING NOT NULL,
action_type STRING NOT NULL,
status STRING NOT NULL,
plan_json STRING NOT NULL,
plan_hash STRING NOT NULL,
confirm_phrase STRING NOT NULL,
risk STRING NOT NULL,
proposer_id STRING NOT NULL,
proposer_email STRING,
created_at TIMESTAMP NOT NULL,
expires_at TIMESTAMP NOT NULL,
updated_at TIMESTAMP NOT NULL,
idempotency_key STRING NOT NULL,
terminal_reason STRING
"""

ACTION_APPROVAL_COLUMNS = """
workspace_id STRING NOT NULL,
environment STRING NOT NULL,
approval_id STRING NOT NULL,
action_id STRING NOT NULL,
plan_hash STRING NOT NULL,
decision STRING NOT NULL,
approver_id STRING NOT NULL,
approver_email STRING,
approver_role STRING NOT NULL,
confirmation STRING,
decided_at TIMESTAMP NOT NULL
"""

ACTION_EVENT_COLUMNS = """
workspace_id STRING NOT NULL,
environment STRING NOT NULL,
event_id STRING NOT NULL,
action_id STRING NOT NULL,
event_type STRING NOT NULL,
from_status STRING,
to_status STRING,
actor_id STRING,
details_json STRING NOT NULL,
event_ts TIMESTAMP NOT NULL
"""

MANAGED_RESOURCE_COLUMNS = """
workspace_id STRING NOT NULL,
environment STRING NOT NULL,
resource_id STRING NOT NULL,
resource_type STRING NOT NULL,
display_name STRING,
bundle_key STRING NOT NULL,
ownership STRING NOT NULL,
stoppable BOOLEAN NOT NULL,
protected BOOLEAN NOT NULL,
stop_order INT NOT NULL,
state STRING,
metadata_json STRING,
updated_at TIMESTAMP NOT NULL
"""

RUNTIME_STATE_COLUMNS = """
workspace_id STRING NOT NULL,
environment STRING NOT NULL,
desired_state STRING NOT NULL,
actual_state STRING NOT NULL,
prior_state_json STRING,
active_action_id STRING,
last_reconciled_at TIMESTAMP,
updated_at TIMESTAMP NOT NULL,
version BIGINT NOT NULL
"""

_FINDING_COLUMNS: dict[str, str] = {
    "finding_id": "STRING",
    "workspace_id": "STRING",
    "environment": "STRING",
    "pillar": "STRING",
    "severity": "STRING",
    "likelihood": "STRING",
    "financial_impact_usd": "DOUBLE",
    "slo_impact": "STRING",
    "confidence": "DOUBLE",
    "owner": "STRING",
    "affected_resources_json": "STRING",
    "evidence_json": "STRING",
    "freshness_at": "TIMESTAMP",
    "first_seen_at": "TIMESTAMP",
    "last_seen_at": "TIMESTAMP",
    "state": "STRING",
    "proposed_action_type": "STRING",
    "blast_radius": "STRING",
}


class SQLControlPlaneRepository:
    """Unity Catalog implementation using parameterized Statement Execution."""

    proposal_only = False

    def __init__(
        self,
        workspace_client,
        warehouse_id: str,
        catalog: str,
        schema: str,
        *,
        auto_migrate: bool = False,
        use_write_procedures: bool = True,
        workspace_id: str | None = None,
        environment: str | None = None,
    ) -> None:
        for part in (catalog, schema):
            if not _IDENTIFIER_RE.fullmatch(part):
                raise ValueError(f"Unsafe Unity Catalog identifier: {part!r}")
        self.workspace_client = workspace_client
        self.warehouse_id = warehouse_id
        self.fq = f"`{catalog}`.`{schema}`"
        self.auto_migrate = auto_migrate
        self.use_write_procedures = use_write_procedures
        self.workspace_id = workspace_id
        self.environment = environment
        self._initialized = False
        self._init_lock = threading.RLock()

    def _assert_scope(self, action: ActionRequest) -> None:
        if (
            (self.workspace_id is not None and action.workspace_id != self.workspace_id)
            or (
                self.environment is not None
                and action.environment != self.environment
            )
        ):
            raise ActionConflictError(
                "Action request belongs to a different workspace or environment."
            )

    def _scope_sql(self, params: dict[str, int | str]) -> str:
        if self.workspace_id is None or self.environment is None:
            raise ActionConflictError(
                "Workspace and environment scope are required for control-plane reads."
            )
        params["scope_workspace_id"] = self.workspace_id
        params["scope_environment"] = self.environment
        return (
            "workspace_id = :scope_workspace_id "
            "AND environment = :scope_environment"
        )

    def _run(
        self, sql: str, parameters: dict[str, int | str] | None = None
    ) -> list[dict]:
        return run_query(
            self.workspace_client,
            sql,
            self.warehouse_id,
            parameters=parameters,
        )

    def _table(self, name: str) -> str:
        return f"{self.fq}.`{name}`"

    def _procedure(self, name: str) -> str:
        return f"{self.fq}.`{name}`"

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            if not self.auto_migrate:
                # Production app identities stay read-only.  Migrations are
                # applied by the setup/controller job under a separate
                # deployment identity.
                self._initialized = True
                return
            self.migrate()
            self._initialized = True

    def migrate(self) -> None:
        """Apply idempotent DDL under a migration-capable deployment identity."""
        with self._init_lock:
            self._run(f"CREATE SCHEMA IF NOT EXISTS {self.fq}")
            tables = {
                "action_requests": ACTION_REQUEST_COLUMNS,
                "action_approvals": ACTION_APPROVAL_COLUMNS,
                "action_events": ACTION_EVENT_COLUMNS,
                "managed_resources": MANAGED_RESOURCE_COLUMNS,
                "platform_runtime_state": RUNTIME_STATE_COLUMNS,
            }
            for name, columns in tables.items():
                self._run(
                    f"CREATE TABLE IF NOT EXISTS {self._table(name)} ({columns}) USING DELTA"
                )
            for name in ("action_approvals", "action_events"):
                self._ensure_table_columns(
                    name,
                    {"workspace_id": "STRING", "environment": "STRING"},
                )
            self._ensure_findings_columns()
            self._initialized = True

    def _ensure_table_columns(
        self,
        name: str,
        required: dict[str, str],
    ) -> None:
        described = self._run(f"DESCRIBE TABLE {self._table(name)}")
        existing = {
            str(row.get("col_name") or "").strip("`").lower()
            for row in described
            if row.get("col_name") and not str(row["col_name"]).startswith("#")
        }
        missing = [
            f"`{column}` {data_type}"
            for column, data_type in required.items()
            if column not in existing
        ]
        if missing:
            self._run(
                f"ALTER TABLE {self._table(name)} "
                f"ADD COLUMNS ({', '.join(missing)})"
            )

    def _ensure_findings_columns(self) -> None:
        legacy = (
            "run_ts TIMESTAMP, area STRING, check_name STRING, resource STRING, "
            "reason STRING, action STRING, details STRING"
        )
        self._run(
            f"CREATE TABLE IF NOT EXISTS {self._table('platform_findings')} "
            f"({legacy}) USING DELTA"
        )
        described = self._run(f"DESCRIBE TABLE {self._table('platform_findings')}")
        existing = {
            str(row.get("col_name") or "").strip("`").lower()
            for row in described
            if row.get("col_name") and not str(row["col_name"]).startswith("#")
        }
        missing = [
            f"`{name}` {data_type}"
            for name, data_type in _FINDING_COLUMNS.items()
            if name not in existing
        ]
        if missing:
            self._run(
                f"ALTER TABLE {self._table('platform_findings')} "
                f"ADD COLUMNS ({', '.join(missing)})"
            )

    @staticmethod
    def _ts(value: datetime) -> str:
        return value.astimezone(UTC).isoformat()

    def create_action(self, action: ActionRequest) -> ActionRequest:
        self.initialize()
        action.assert_integrity()
        self._assert_scope(action)
        immutable = canonical_json(action.immutable_document())
        parameters = {
            "workspace_id": action.workspace_id,
            "environment": action.environment,
            "action_id": action.action_id,
            "action_type": action.action_type,
            "plan_json": immutable,
            "plan_hash": action.plan_hash,
            "confirm_phrase": action.confirm_phrase,
            "risk": action.risk.value,
            "proposer_id": action.proposer_id,
            "proposer_email": action.proposer_email or "",
            "created_at": self._ts(action.created_at),
            "expires_at": self._ts(action.expires_at),
            "updated_at": self._ts(action.updated_at),
            "idempotency_key": action.idempotency_key,
        }
        if self.use_write_procedures:
            self._run(
                f"CALL {self._procedure('cp_create_action')}("
                ":workspace_id, :environment, :action_id, :action_type, "
                ":plan_json, :plan_hash, :confirm_phrase, :risk, :proposer_id, "
                ":proposer_email, :created_at, :expires_at, :updated_at, "
                ":idempotency_key)",
                parameters,
            )
            return action
        self._run(
            f"""INSERT INTO {self._table("action_requests")} (
workspace_id, environment, action_id, action_type, status, plan_json, plan_hash,
confirm_phrase, risk, proposer_id, proposer_email, created_at, expires_at,
updated_at, idempotency_key, terminal_reason
) VALUES (
:workspace_id, :environment, :action_id, :action_type, :status, :plan_json, :plan_hash,
:confirm_phrase, :risk, :proposer_id, :proposer_email, CAST(:created_at AS TIMESTAMP),
CAST(:expires_at AS TIMESTAMP), CAST(:updated_at AS TIMESTAMP), :idempotency_key, NULL
)""",
            {**parameters, "status": action.status.value},
        )
        return action

    @staticmethod
    def _action_from_row(row: dict[str, Any]) -> ActionRequest:
        try:
            document = json.loads(str(row["plan_json"]))
            action = ActionRequest(
                **document,
                plan_hash=str(row["plan_hash"]),
                status=ActionStatus(str(row["status"])),
                updated_at=row["updated_at"],
                terminal_reason=row.get("terminal_reason"),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PlanIntegrityError("Stored action request is malformed.") from exc
        scalar_checks = {
            "action_id": action.action_id,
            "action_type": action.action_type,
            "workspace_id": action.workspace_id,
            "environment": action.environment,
            "confirm_phrase": action.confirm_phrase,
            "idempotency_key": action.idempotency_key,
        }
        if any(str(row.get(key)) != str(value) for key, value in scalar_checks.items()):
            raise PlanIntegrityError("Stored plan document disagrees with indexed columns.")
        action.assert_integrity()
        return action

    def get_action(self, action_id: str) -> ActionRequest | None:
        self.initialize()
        params: dict[str, int | str] = {"action_id": action_id}
        scope = self._scope_sql(params)
        rows = self._run(
            f"SELECT * FROM {self._table('action_requests')} "
            f"WHERE action_id = :action_id {'AND ' + scope if scope else ''} "
            "ORDER BY created_at DESC LIMIT 2",
            params,
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise PlanIntegrityError(f"Duplicate action request {action_id}.")
        return self._action_from_row(rows[0])

    def list_actions(
        self,
        *,
        status: ActionStatus | None = None,
        action_type: str | None = None,
        limit: int = 100,
    ) -> list[ActionRequest]:
        self.initialize()
        clauses = ["1 = 1"]
        params: dict[str, int | str] = {"limit": max(1, min(limit, 500))}
        scope = self._scope_sql(params)
        if scope:
            clauses.append(scope)
        if status:
            clauses.append("status = :status")
            params["status"] = status.value
        if action_type:
            clauses.append("action_type = :action_type")
            params["action_type"] = action_type
        rows = self._run(
            f"SELECT * FROM {self._table('action_requests')} "
            f"WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT :limit",
            params,
        )
        return [self._action_from_row(row) for row in rows]

    def transition(
        self,
        action_id: str,
        *,
        expected: set[ActionStatus],
        target: ActionStatus,
        actor_id: str | None,
        reason: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> ActionRequest:
        self.initialize()
        current = self.get_action(action_id)
        if current is None:
            raise ActionConflictError(f"Unknown action request {action_id}.")
        if current.status not in expected:
            raise ActionConflictError(
                f"Action request is {current.status.value}; expected "
                f"{', '.join(sorted(s.value for s in expected))}."
            )
        validate_transition(current.status, target)
        now = utc_now()
        event = ActionEvent(
            action_id=action_id,
            event_type=f"STATUS_{target.value}",
            from_status=current.status,
            to_status=target,
            actor_id=actor_id,
            details={"reason": reason, **(details or {})},
            event_ts=now,
        )
        if self.use_write_procedures:
            self._run(
                f"CALL {self._procedure('cp_transition_action')}("
                ":workspace_id, :environment, :action_id, "
                ":expected_statuses, :target_status, :reason, :event_id, "
                ":details_json, :event_at)",
                {
                    "workspace_id": current.workspace_id,
                    "environment": current.environment,
                    "action_id": action_id,
                    "expected_statuses": ",".join(
                        sorted(status.value for status in expected)
                    ),
                    "target_status": target.value,
                    "reason": reason or "",
                    "event_id": event.event_id,
                    "details_json": canonical_json(event.details),
                    "event_at": self._ts(event.event_ts),
                },
            )
            updated = self.get_action(action_id)
            if updated is None or updated.status != target:
                raise ActionConflictError(
                    "Concurrent action update rejected this transition."
                )
            return updated
        expected_values = sorted(status.value for status in expected)
        markers = ", ".join(f":expected_{index}" for index in range(len(expected_values)))
        parameters: dict[str, int | str] = {
            "action_id": action_id,
            "target": target.value,
            "updated_at": self._ts(now),
            "reason": reason or "",
        }
        parameters.update(
            {f"expected_{index}": value for index, value in enumerate(expected_values)}
        )
        scope = self._scope_sql(parameters)
        self._run(
            f"UPDATE {self._table('action_requests')} "
            "SET status = :target, updated_at = CAST(:updated_at AS TIMESTAMP), "
            "terminal_reason = NULLIF(:reason, '') "
            f"WHERE action_id = :action_id AND status IN ({markers}) "
            f"{'AND ' + scope if scope else ''}",
            parameters,
        )
        updated = self.get_action(action_id)
        if updated is None or updated.status != target:
            raise ActionConflictError("Concurrent action update rejected this transition.")
        self.add_event(event)
        return updated

    def add_approval(self, approval: ApprovalRecord) -> None:
        self.initialize()
        if self.use_write_procedures:
            raise ActionConflictError(
                "Human approvals must use the atomic security-definer decision procedure."
            )
        action = self.get_action(approval.action_id)
        if action is None:
            raise ActionConflictError("Cannot approve an out-of-scope action.")
        self._run(
            f"""INSERT INTO {self._table("action_approvals")} (
workspace_id, environment, approval_id, action_id, plan_hash, decision,
approver_id, approver_email,
approver_role, confirmation, decided_at
) VALUES (
:workspace_id, :environment, :approval_id, :action_id, :plan_hash, :decision,
:approver_id, :approver_email,
:approver_role, :confirmation, CAST(:decided_at AS TIMESTAMP)
)""",
            {
                "workspace_id": action.workspace_id,
                "environment": action.environment,
                "approval_id": approval.approval_id,
                "action_id": approval.action_id,
                "plan_hash": approval.plan_hash,
                "decision": approval.decision.value,
                "approver_id": approval.approver_id,
                "approver_email": approval.approver_email or "",
                "approver_role": approval.approver_role,
                "confirmation": approval.confirmation or "",
                "decided_at": self._ts(approval.decided_at),
            },
        )

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
    ) -> ActionRequest:
        """Persist one exact decision atomically through the UC write broker."""

        self.initialize()
        current = self.get_action(action_id)
        if current is None:
            raise ActionConflictError(f"Unknown action request {action_id}.")
        if current.status != expected:
            raise ActionConflictError(
                f"Action request is {current.status.value}; expected {expected.value}."
            )
        validate_transition(current.status, target)
        expected_decision = (
            "APPROVED" if target == ActionStatus.APPROVED else "REJECTED"
        )
        if (
            approval.action_id != action_id
            or approval.plan_hash != current.plan_hash
            or approval.decision.value != expected_decision
        ):
            raise ActionConflictError(
                "Approval evidence does not match the exact decision."
            )
        event = ActionEvent(
            action_id=action_id,
            event_type=f"STATUS_{target.value}",
            from_status=current.status,
            to_status=target,
            actor_id=actor_id,
            details={"reason": reason, **(details or {})},
            event_ts=approval.decided_at,
        )
        if not self.use_write_procedures:
            # Compatibility path for migration/unit-test repositories. Runtime
            # application repositories always use the security-definer broker.
            self.transition(
                action_id,
                expected={expected},
                target=target,
                actor_id=actor_id,
                reason=reason,
                details=details,
            )
            self.add_approval(approval)
            updated = self.get_action(action_id)
            if updated is None:
                raise ActionConflictError("The decided action disappeared.")
            return updated
        self._run(
            f"CALL {self._procedure('cp_decide_action')}("
            ":workspace_id, :environment, :action_id, :expected_status, "
            ":target_status, :plan_hash, :approval_id, :decision, "
            ":approver_id, :approver_email, :confirmation, :reason, "
            ":event_id, :details_json, :decided_at)",
            {
                "workspace_id": current.workspace_id,
                "environment": current.environment,
                "action_id": current.action_id,
                "expected_status": expected.value,
                "target_status": target.value,
                "plan_hash": current.plan_hash,
                "approval_id": approval.approval_id,
                "decision": approval.decision.value,
                "approver_id": approval.approver_id,
                "approver_email": approval.approver_email or "",
                "confirmation": approval.confirmation or "",
                "reason": reason or "",
                "event_id": event.event_id,
                "details_json": canonical_json(event.details),
                "decided_at": self._ts(approval.decided_at),
            },
        )
        updated = self.get_action(action_id)
        if updated is None or updated.status != target:
            raise ActionConflictError(
                "Concurrent action update rejected this decision."
            )
        return updated

    def list_approvals(self, action_id: str) -> list[ApprovalRecord]:
        self.initialize()
        if self.get_action(action_id) is None:
            return []
        parameters: dict[str, int | str] = {"action_id": action_id}
        scope = self._scope_sql(parameters)
        rows = self._run(
            f"SELECT * FROM {self._table('action_approvals')} "
            f"WHERE action_id = :action_id AND {scope} ORDER BY decided_at",
            parameters,
        )
        return [
            ApprovalRecord(
                approval_id=str(row["approval_id"]),
                action_id=str(row["action_id"]),
                plan_hash=str(row["plan_hash"]),
                decision=str(row["decision"]),
                approver_id=str(row["approver_id"]),
                approver_email=row.get("approver_email") or None,
                approver_role=str(row["approver_role"]),
                confirmation=row.get("confirmation") or None,
                decided_at=row["decided_at"],
            )
            for row in rows
        ]

    def add_event(self, event: ActionEvent) -> None:
        self.initialize()
        action = self.get_action(event.action_id)
        if action is None:
            raise ActionConflictError("Cannot append to an out-of-scope action.")
        if self.use_write_procedures:
            self._run(
                f"CALL {self._procedure('cp_append_event')}("
                ":workspace_id, :environment, :action_id, :event_id, "
                ":event_type, :from_status, :to_status, :details_json, :event_at)",
                {
                    "workspace_id": action.workspace_id,
                    "environment": action.environment,
                    "action_id": event.action_id,
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "from_status": (
                        event.from_status.value if event.from_status else ""
                    ),
                    "to_status": event.to_status.value if event.to_status else "",
                    "details_json": canonical_json(event.details),
                    "event_at": self._ts(event.event_ts),
                },
            )
            return
        self._run(
            f"""INSERT INTO {self._table("action_events")} (
workspace_id, environment, event_id, action_id, event_type, from_status,
to_status, actor_id, details_json, event_ts
) VALUES (
:workspace_id, :environment, :event_id, :action_id, :event_type, :from_status,
:to_status, :actor_id,
:details_json, CAST(:event_ts AS TIMESTAMP)
)""",
            {
                "workspace_id": action.workspace_id,
                "environment": action.environment,
                "event_id": event.event_id,
                "action_id": event.action_id,
                "event_type": event.event_type,
                "from_status": event.from_status.value if event.from_status else "",
                "to_status": event.to_status.value if event.to_status else "",
                "actor_id": event.actor_id or "",
                "details_json": canonical_json(event.details),
                "event_ts": self._ts(event.event_ts),
            },
        )

    def list_events(self, action_id: str) -> list[ActionEvent]:
        self.initialize()
        if self.get_action(action_id) is None:
            return []
        parameters: dict[str, int | str] = {"action_id": action_id}
        scope = self._scope_sql(parameters)
        rows = self._run(
            f"SELECT * FROM {self._table('action_events')} "
            f"WHERE action_id = :action_id AND {scope} ORDER BY event_ts",
            parameters,
        )
        return [
            ActionEvent(
                event_id=str(row["event_id"]),
                action_id=str(row["action_id"]),
                event_type=str(row["event_type"]),
                from_status=row.get("from_status") or None,
                to_status=row.get("to_status") or None,
                actor_id=row.get("actor_id") or None,
                details=json.loads(row.get("details_json") or "{}"),
                event_ts=row["event_ts"],
            )
            for row in rows
        ]

    def list_findings(
        self,
        *,
        pillar: str | None = None,
        state: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        self.initialize()
        described = self._run(f"DESCRIBE TABLE {self._table('platform_findings')}")
        columns = {
            str(row.get("col_name") or "").strip("`").lower()
            for row in described
            if row.get("col_name") and not str(row["col_name"]).startswith("#")
        }
        if "finding_id" not in columns:
            if not {"workspace_id", "environment"}.issubset(columns):
                raise PlanIntegrityError(
                    "The findings table is not workspace-aware; run the "
                    "control-plane migration before reading it."
                )
            parameters: dict[str, int | str] = {
                "limit": max(1, min(limit, 1000))
            }
            scope = self._scope_sql(parameters)
            rows = self._run(
                f"SELECT run_ts, area, check_name, resource, reason, action, details "
                f"FROM {self._table('platform_findings')} "
                f"WHERE {scope} "
                "ORDER BY run_ts DESC LIMIT :limit",
                parameters,
            )
            normalized = [
                Finding.from_row(row).model_dump(mode="json") for row in rows
            ]
            normalized = [
                row
                for row in normalized
                if (pillar is None or row["pillar"] == pillar)
                and (state is None or row["state"] == state)
            ]
            return _rank_findings(normalized)
        clauses = ["1 = 1"]
        params: dict[str, int | str] = {"limit": max(1, min(limit, 1000))}
        clauses.append(self._scope_sql(params))
        if pillar:
            clauses.append("COALESCE(pillar, UPPER(area)) = :pillar")
            params["pillar"] = pillar
        if state:
            clauses.append("COALESCE(state, 'OPEN') = :state")
            params["state"] = state
        rows = self._run(
            f"""SELECT
COALESCE(finding_id, SHA2(CONCAT_WS('|', CAST(run_ts AS STRING), area, check_name,
  resource, reason, action), 256)) AS finding_id,
workspace_id, environment, COALESCE(pillar, UPPER(area), 'RISK') AS pillar,
COALESCE(severity, 'MEDIUM') AS severity, COALESCE(likelihood, 'UNKNOWN') AS likelihood,
COALESCE(financial_impact_usd, 0) AS financial_impact_usd, slo_impact,
COALESCE(confidence, 0.5) AS confidence, owner,
COALESCE(affected_resources_json, TO_JSON(ARRAY(NAMED_STRUCT(
  'resource_id', resource)))) AS affected_resources_json,
COALESCE(evidence_json, details) AS evidence_json,
COALESCE(freshness_at, run_ts) AS freshness_at,
COALESCE(first_seen_at, run_ts) AS first_seen_at,
COALESCE(last_seen_at, run_ts) AS last_seen_at,
COALESCE(state, 'OPEN') AS state,
COALESCE(proposed_action_type, action) AS proposed_action_type,
COALESCE(blast_radius, 'UNKNOWN') AS blast_radius,
area, check_name, resource, reason, action, details, run_ts
FROM {self._table("platform_findings")}
WHERE {' AND '.join(clauses)}
ORDER BY
  CASE UPPER(COALESCE(severity, 'MEDIUM'))
    WHEN 'CRITICAL' THEN 5 WHEN 'HIGH' THEN 4 WHEN 'MEDIUM' THEN 3
    WHEN 'LOW' THEN 2 ELSE 1 END DESC,
  COALESCE(financial_impact_usd, 0) DESC,
  COALESCE(first_seen_at, run_ts) ASC
LIMIT :limit""",
            params,
        )
        return [Finding.from_row(row).model_dump(mode="json") for row in rows]

    def runtime_state(self, workspace_id: str, environment: str) -> dict[str, Any]:
        self.initialize()
        rows = self._run(
            f"SELECT * FROM {self._table('platform_runtime_state')} "
            "WHERE workspace_id = :workspace_id AND environment = :environment "
            "ORDER BY updated_at DESC LIMIT 1",
            {"workspace_id": workspace_id, "environment": environment},
        )
        if not rows:
            return {
                "workspace_id": workspace_id,
                "environment": environment,
                "desired_state": "ON",
                "actual_state": "UNKNOWN",
                "prior_state": None,
                "active_action_id": None,
                "last_reconciled_at": None,
                "updated_at": None,
                "version": 0,
                "source": "unity-catalog",
            }
        row = dict(rows[0])
        row["prior_state"] = json.loads(row.pop("prior_state_json") or "null")
        row["source"] = "unity-catalog"
        return row

    def managed_resources(
        self, workspace_id: str, environment: str
    ) -> list[dict[str, Any]]:
        self.initialize()
        rows = self._run(
            f"SELECT * FROM {self._table('managed_resources')} "
            "WHERE workspace_id = :workspace_id AND environment = :environment "
            "ORDER BY stop_order, resource_type, display_name",
            {"workspace_id": workspace_id, "environment": environment},
        )
        for row in rows:
            row["metadata"] = json.loads(row.pop("metadata_json") or "{}")
        return rows
