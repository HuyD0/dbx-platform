"""Mission Control evidence, approval, audit and runtime read APIs."""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend import cache, deps
from backend.control_plane import (
    ActionConflictError,
    ActionEvent,
    ActionExpiredError,
    ActionNotFoundError,
    ActionRequest,
    ActionService,
    ActionStatus,
    ControlPlaneError,
    PlanIntegrityError,
    PlanSpec,
    PreconditionsChangedError,
    RiskLevel,
    sha256_json,
    utc_now,
)
from backend.errors import payload
from backend.identity import UnauthenticatedError, UnauthorizedError, mask_for_viewer
from backend.models import (
    ActionApprovalRequest,
    ActionPlanRequest,
    ActionRejectRequest,
    envelope,
)
from backend.routers import actions, jobs
from dbx_platform import llm_cost
from dbx_platform.resource_identifiers import (
    extract_resource_ids,
    parse_resource_ids,
)
from dbx_platform.system_tables import run_query

router = APIRouter()
log = logging.getLogger("platform_console.control_plane")

_ACTION_RISK = {
    "stale-clusters": RiskLevel.MEDIUM,
    "orphaned-jobs": RiskLevel.MEDIUM,
    "token-revoke": RiskLevel.HIGH,
    "policy-sync": RiskLevel.MEDIUM,
    "run-job": RiskLevel.MEDIUM,
    "configure-budget": RiskLevel.MEDIUM,
}
_RUNTIME_ACTIONS = {"runtime.hibernate", "runtime.wake"}
_BUDGET_SCOPE_TYPES = frozenset({"workspace", "provider", "team", "use_case"})
_BUDGET_ALLOWED_PARAMETERS = frozenset(
    {
        "budget_id",
        "scope_type",
        "scope_value",
        "cost_basis",
        "month",
        "currency",
        "amount",
        "warning_threshold_pct",
        "critical_threshold_pct",
    }
)
_BUDGET_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}")
_MONTH_PATTERN = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")
_ACTION_ALIASES = {
    "hibernate": "runtime.hibernate",
    "wake": "runtime.wake",
}
_EXPIRABLE_STATUSES = frozenset(
    {ActionStatus.AWAITING_APPROVAL, ActionStatus.APPROVED}
)
_RISK_RANK = {
    RiskLevel.HIGH: 3,
    RiskLevel.MEDIUM: 2,
    RiskLevel.LOW: 1,
}
_EVIDENCE_RESPONSE_LIMIT = 50
_EVIDENCE_READ_LIMIT = 1000


def _normalize_action_type(action_type: str) -> str:
    return _ACTION_ALIASES.get(action_type, action_type)


def _error(exc: Exception) -> JSONResponse:
    if isinstance(exc, UnauthenticatedError):
        status = 401
    elif isinstance(exc, UnauthorizedError):
        status = 403
    elif isinstance(exc, (ActionNotFoundError, HTTPException)):
        status = 404
    elif isinstance(exc, ActionExpiredError):
        status = 410
    elif isinstance(
        exc,
        (
            ActionConflictError,
            PlanIntegrityError,
            PreconditionsChangedError,
        ),
    ):
        status = 409
    elif isinstance(exc, ValueError):
        status = 400
    else:
        status = 503
    if isinstance(exc, ControlPlaneError):
        code, message = exc.code, str(exc)
    elif isinstance(exc, HTTPException):
        code, message = "action_not_found", str(exc.detail)
    elif isinstance(exc, ValueError):
        code, message = "bad_request", str(exc)
    else:
        code, message = (
            "control_plane_unavailable",
            "Mission Control storage is unavailable.",
        )
        log.exception("control-plane request failed", exc_info=exc)
    return JSONResponse(status_code=status, content=payload(code, message))


def _repository():
    return deps.get_control_plane_repository()


def _write_repository(request: Request):
    return deps.get_user_control_plane_repository(request)


def _service(repository=None) -> ActionService:
    workspace_id, environment = deps.control_plane_scope()
    return ActionService(
        repository or _repository(),
        workspace_id=workspace_id,
        environment=environment,
    )


def _actor(
    request: Request,
    *,
    approver: bool = False,
    proposer: bool = False,
):
    actor = deps.require_verified_user(request)
    if approver and not actor.has_role("approver"):
        raise UnauthorizedError(
            "Membership in dbx-platform-approvers is required."
        )
    if proposer and not actor.has_role("proposer"):
        raise UnauthorizedError(
            "Membership in dbx-platform-operators or "
            "dbx-platform-approvers is required to propose actions."
        )
    return actor


def _budget_threshold(parameters: dict[str, Any], name: str, default: int) -> int:
    value = parameters.get(name, default)
    if isinstance(value, bool):
        raise ValueError(f"Budget {name} must be an integer from 0 to 100.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Budget {name} must be an integer from 0 to 100."
        ) from exc
    if str(parsed) != str(value).strip() or not 0 <= parsed <= 100:
        raise ValueError(f"Budget {name} must be an integer from 0 to 100.")
    return parsed


def _json_budget_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _normalize_current_budget(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = (
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
        "plan_hash",
        "updated_by",
        "updated_at",
    )
    return {key: _json_budget_value(row.get(key)) for key in keys}


def _read_budget_by_id(
    budget_id: str,
    workspace_id: str,
    environment: str,
) -> dict[str, Any] | None:
    settings = deps.get_settings()
    rows = run_query(
        deps.get_ws(),
        (
            "SELECT budget_id, workspace_id, environment, scope_type, scope_value, "
            "cost_basis, month, currency, amount, warning_pct, critical_pct, "
            "status, plan_hash, updated_by, updated_at "
            f"FROM {settings.dashboard_catalog}.{settings.dashboard_schema}.llm_budgets "
            "WHERE budget_id = :budget_id "
            "AND workspace_id = :workspace_id "
            "AND environment = :environment "
            "LIMIT 2"
        ),
        deps.warehouse_id(),
        {
            "budget_id": budget_id,
            "workspace_id": workspace_id,
            "environment": environment,
        },
    )
    if len(rows) > 1:
        raise ValueError(
            "Budget storage contains duplicate rows for this exact budget ID."
        )
    return _normalize_current_budget(rows[0] if rows else None)


def _budget_plan(parameters: dict[str, Any]) -> PlanSpec:
    unknown = set(parameters) - _BUDGET_ALLOWED_PARAMETERS
    if unknown:
        raise ValueError(
            f"Action 'configure-budget' received unsupported parameters: {sorted(unknown)}."
        )
    required = {
        "scope_type",
        "scope_value",
        "cost_basis",
        "month",
        "currency",
        "amount",
    }
    missing = sorted(
        name
        for name in required
        if name not in parameters or parameters[name] in (None, "")
    )
    if missing:
        raise ValueError(
            f"Action 'configure-budget' is missing required parameters: {missing}."
        )

    scope_type = str(parameters["scope_type"]).strip()
    if scope_type not in _BUDGET_SCOPE_TYPES:
        raise ValueError(
            "Budget scope_type must be one of workspace, provider, team, or use_case."
        )
    scope_value = str(parameters["scope_value"]).strip()
    if not scope_value or len(scope_value) > 256 or any(
        ord(character) < 32 for character in scope_value
    ):
        raise ValueError(
            "Budget scope_value must be a non-empty value of at most 256 characters."
        )

    cost_basis = str(parameters["cost_basis"]).strip()
    if cost_basis not in llm_cost.COST_BASES:
        raise ValueError(
            "Budget cost_basis must be DATABRICKS_LIST, AZURE_ACTUAL, "
            "or PROVIDER_ESTIMATE."
        )
    currency = str(parameters["currency"]).strip()
    if not _CURRENCY_PATTERN.fullmatch(currency):
        raise ValueError("Budget currency must be a three-letter uppercase code.")

    month_value = str(parameters["month"]).strip()
    match = _MONTH_PATTERN.fullmatch(month_value)
    if match is None:
        raise ValueError("Budget month must use the ISO YYYY-MM format.")
    month = date(int(match.group(1)), int(match.group(2)), 1)
    current_month = utc_now().date().replace(day=1)
    if month < current_month:
        raise ValueError("Budget month must be the current month or a future month.")

    amount_value = parameters["amount"]
    if isinstance(amount_value, bool):
        raise ValueError("Budget amount must be a finite number greater than zero.")
    try:
        amount = float(amount_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Budget amount must be a finite number greater than zero."
        ) from exc
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("Budget amount must be a finite number greater than zero.")

    warning_pct = _budget_threshold(parameters, "warning_threshold_pct", 80)
    critical_pct = _budget_threshold(parameters, "critical_threshold_pct", 100)
    if critical_pct < warning_pct:
        raise ValueError(
            "Budget critical_threshold_pct must be greater than or equal to "
            "warning_threshold_pct."
        )

    workspace_id, environment = deps.control_plane_scope()
    key = {
        "workspace_id": workspace_id,
        "environment": environment,
        "scope_type": scope_type,
        "scope_value": scope_value,
        "cost_basis": cost_basis,
        "month": month.isoformat(),
        "currency": currency,
    }
    supplied_budget_id = parameters.get("budget_id")
    if supplied_budget_id is None:
        budget_id = f"budget-{sha256_json(key)[:24]}"
    else:
        budget_id = str(supplied_budget_id).strip()
        if not _BUDGET_ID_PATTERN.fullmatch(budget_id):
            raise ValueError(
                "Budget budget_id contains unsupported characters or is too long."
            )

    before = _read_budget_by_id(budget_id, workspace_id, environment)
    desired = {
        "budget_id": budget_id,
        **key,
        "amount": amount,
        "warning_pct": warning_pct,
        "critical_pct": critical_pct,
        "status": "ACTIVE",
    }
    target = {
        "resource_type": "LLM_BUDGET",
        "resource_id": budget_id,
        "budget_id": budget_id,
        "scope_type": scope_type,
        "scope_value": scope_value,
        "cost_basis": cost_basis,
        "month": month.isoformat(),
        "currency": currency,
        "action": "UPDATE" if before is not None else "CREATE",
    }
    execution_payload = {
        "operation": "UPSERT_LLM_BUDGET",
        "budget_id": budget_id,
        "expected_before": before,
        "desired": desired,
    }
    state_hash = sha256_json(
        {"targets": [target], "execution_payload": execution_payload}
    )
    return PlanSpec(
        action_type="configure-budget",
        targets=[target],
        parameters={
            "request": dict(parameters),
            "execution_payload": execution_payload,
        },
        preconditions={
            "state_sha256": state_hash,
            "planner": "configure-budget",
            "budget_id": budget_id,
            "expected_before": before,
        },
        before_state={"budget": before},
        after_state={"budget": desired},
        impact={
            "summary": {
                "budget_changes": 1,
                "scope": f"{scope_type}:{scope_value}",
                "amount": amount,
                "currency": currency,
            },
            "target_count": 1,
        },
        rollback={
            "supported": False,
            "description": (
                "No automatic rollback. Restore the exact prior budget row with "
                "a separately approved plan."
                if before is not None
                else (
                    "No automatic rollback; deactivate the new budget with a "
                    "separately approved plan."
                )
            ),
        },
        verification={
            "strategy": (
                "Re-read this exact budget ID and verify every desired field and "
                "the executor-recorded plan hash."
            )
        },
        risk=RiskLevel.MEDIUM,
    )


def _planner(action_type: str, parameters: dict[str, Any]) -> PlanSpec:
    action_type = _normalize_action_type(action_type)
    if action_type in _RUNTIME_ACTIONS:
        raise ValueError(
            "Runtime plans must be produced by the out-of-band power-controller."
        )
    if action_type == "configure-budget":
        return _budget_plan(parameters)
    if action_type == "run-job":
        unknown = set(parameters) - {"job_id", "job_name"}
        if unknown:
            raise ValueError(
                f"Action 'run-job' received unsupported parameters: {sorted(unknown)}."
            )
        try:
            job_id = int(parameters["job_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Action 'run-job' requires an integer job_id.") from exc
        claimed_name = parameters.get("job_name")
        items, execution_payload, summary = jobs.build_job_run_plan(
            job_id,
            str(claimed_name) if claimed_name is not None else None,
        )
        risk = (
            RiskLevel.LOW
            if jobs.is_low_risk_manual_job(job_id)
            else RiskLevel.MEDIUM
        )
    else:
        items, execution_payload, summary = actions.build_action_plan(
            action_type, parameters
        )
        risk = _ACTION_RISK.get(action_type, RiskLevel.HIGH)
    state_hash = sha256_json(
        {
            "targets": items,
            "execution_payload": execution_payload,
        }
    )
    rollback_supported = action_type == "orphaned-jobs"
    return PlanSpec(
        action_type=action_type,
        targets=items,
        parameters={
            "request": parameters,
            "execution_payload": execution_payload,
        },
        preconditions={
            "state_sha256": state_hash,
            "planner": action_type,
        },
        before_state=items,
        after_state={"summary": summary},
        impact={"summary": summary, "target_count": len(items)},
        rollback={
            "supported": rollback_supported,
            "description": (
                "If a pause fails part-way, restore every changed job's exact "
                "captured schedule state before recording ROLLED_BACK."
                if rollback_supported
                else (
                    "No rollback: revoked PATs cannot be restored."
                    if action_type == "token-revoke"
                    else "No automatic rollback is implemented for this action."
                )
            ),
        },
        verification={
            "strategy": "Re-read each target after execution and record its resulting state."
        },
        risk=risk,
    )


def _revalidate(action: ActionRequest) -> dict[str, Any]:
    if action.action_type in _RUNTIME_ACTIONS:
        # The controller performs an exact observation against its
        # preconditions immediately before any write. The app cannot safely
        # reproduce that inventory adapter, so it preserves the reviewed
        # precondition document and delegates the write-time check.
        return dict(action.preconditions)
    request_parameters = action.parameters.get("request")
    current_parameters = (
        dict(request_parameters) if isinstance(request_parameters, dict) else {}
    )
    if action.action_type == "run-job":
        # The claimed display name was validated when planning. Revalidation
        # intentionally fetches the current name so a rename changes the
        # target hash instead of becoming a client-input validation error.
        current_parameters.pop("job_name", None)
    spec = _planner(
        action.action_type,
        current_parameters,
    )
    return dict(spec.preconditions)


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _effective_status(
    action: ActionRequest,
    evaluated_at: datetime,
) -> ActionStatus:
    if (
        action.status in _EXPIRABLE_STATUSES
        and action.is_expired(evaluated_at)
    ):
        return ActionStatus.EXPIRED
    return action.status


def _can_approve(
    action: ActionRequest,
    *,
    actor,
    evaluated_at: datetime,
    repository,
) -> bool:
    return (
        _effective_status(action, evaluated_at)
        == ActionStatus.AWAITING_APPROVAL
        and actor.has_role("approver")
        and deps.actions_enabled()
        and not repository.proposal_only
    )


def _finding_resource_value(finding: dict[str, Any]) -> Any:
    if "affected_resources" in finding:
        return finding.get("affected_resources")
    return finding.get("affected_resources_json")


def _matching_findings(
    action: ActionRequest,
    findings: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], str]]:
    target_ids = extract_resource_ids(action.targets)
    if not target_ids:
        return []
    matches: list[tuple[dict[str, Any], str]] = []
    for finding in findings:
        finding_ids = parse_resource_ids(_finding_resource_value(finding))
        if not target_ids.intersection(finding_ids):
            continue
        finding_action = _normalize_action_type(
            str(
                finding.get("proposed_action_type")
                or finding.get("action")
                or ""
            )
        )
        match_type = (
            "supports_action"
            if finding_action == action.action_type
            else "same_target"
        )
        matches.append((finding, match_type))
    return matches


def _freshest_finding_at(
    findings: list[dict[str, Any]],
) -> str | None:
    candidates: list[tuple[datetime, str]] = []
    fallback: list[str] = []
    for finding in findings:
        value = (
            finding.get("freshness_at")
            or finding.get("last_seen_at")
            or finding.get("run_ts")
        )
        if not value:
            continue
        text = value.isoformat() if isinstance(value, datetime) else str(value)
        parsed = _as_utc(value)
        if parsed is not None:
            candidates.append((parsed, text))
        else:
            fallback.append(text)
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    return max(fallback) if fallback else None


def _evidence_summary(
    action: ActionRequest,
    findings: list[dict[str, Any]],
    *,
    available: bool,
) -> dict[str, Any]:
    if not available:
        return {
            "matched_count": 0,
            "pillars": [],
            "freshest_at": None,
            "coverage_status": "UNAVAILABLE",
        }
    matches = _matching_findings(action, findings)
    matched_findings = [finding for finding, _match_type in matches]
    return {
        "matched_count": len(matches),
        "pillars": sorted(
            {
                str(finding.get("pillar") or "RISK").upper()
                for finding in matched_findings
            }
        ),
        "freshest_at": _freshest_finding_at(matched_findings),
        "coverage_status": (
            "MATCHED"
            if matches
            else (
                "NO_MATCH"
                if extract_resource_ids(action.targets)
                else "NO_TARGETS"
            )
        ),
    }


def _action_evidence(
    action: ActionRequest,
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    matches = _matching_findings(action, findings)
    items = []
    for finding, match_type in matches[:_EVIDENCE_RESPONSE_LIMIT]:
        items.append(
            {
                "finding_id": finding.get("finding_id"),
                "check_name": finding.get("check_name"),
                "pillar": str(finding.get("pillar") or "RISK").upper(),
                "severity": str(
                    finding.get("severity") or "MEDIUM"
                ).upper(),
                "confidence": finding.get("confidence"),
                "owner": finding.get("owner"),
                "reason": finding.get("reason"),
                "state": str(finding.get("state") or "OPEN").upper(),
                "freshness_at": (
                    finding.get("freshness_at")
                    or finding.get("last_seen_at")
                    or finding.get("run_ts")
                ),
                "proposed_action_type": (
                    finding.get("proposed_action_type")
                    or finding.get("action")
                ),
                "affected_resources": finding.get(
                    "affected_resources",
                    [],
                ),
                "match_type": match_type,
            }
        )
    return {
        "coverage_status": (
            "MATCHED"
            if matches
            else (
                "NO_MATCH"
                if extract_resource_ids(action.targets)
                else "NO_TARGETS"
            )
        ),
        "total": len(matches),
        "truncated": len(matches) > _EVIDENCE_RESPONSE_LIMIT,
        "items": items,
    }


def _unavailable_action_evidence() -> dict[str, Any]:
    return {
        "coverage_status": "UNAVAILABLE",
        "total": 0,
        "truncated": False,
        "items": [],
    }


def _action_body(
    action: ActionRequest,
    *,
    actor,
    detail: bool = False,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    evaluated_at = (evaluated_at or utc_now()).astimezone(UTC)
    repo = _repository()
    effective_status = _effective_status(action, evaluated_at)
    body = action.model_dump(mode="json")
    body.update(
        {
            # Compatibility fields for the existing ActionPlanDialog while it
            # moves from /api/actions to the durable generic API.
            "plan_id": action.action_id,
            "action": action.action_type,
            "items": action.targets,
            "summary": action.impact.get("summary", {}),
            "actions_enabled": (
                deps.actions_enabled() and not repo.proposal_only
            ),
            "approver_required": True,
            "risk": action.risk.value.lower(),
            "raw_status": action.status.value,
            "effective_status": effective_status.value,
            "evaluated_at": evaluated_at.isoformat(),
            "can_approve": _can_approve(
                action,
                actor=actor,
                evaluated_at=evaluated_at,
                repository=repo,
            ),
        }
    )
    if effective_status == ActionStatus.EXPIRED:
        body["expiry_guidance"] = (
            "This immutable plan has expired. Create a new exact plan from "
            "current evidence before requesting approval."
        )
    if detail:
        body["approvals"] = [
            row.model_dump(mode="json") for row in repo.list_approvals(action.action_id)
        ]
        body["events"] = [
            row.model_dump(mode="json") for row in repo.list_events(action.action_id)
        ]
        try:
            findings = repo.list_findings(limit=_EVIDENCE_READ_LIMIT)
            body["evidence_correlation"] = _action_evidence(action, findings)
        except Exception:  # noqa: BLE001 - action history remains independently useful
            log.warning(
                "Action evidence correlation is unavailable",
                exc_info=True,
            )
            body["evidence_correlation"] = _unavailable_action_evidence()
    return mask_for_viewer(body, actor)


@router.get("/api/mission-control")
def mission_control(request: Request, refresh: bool = False) -> Any:
    try:
        actor = _actor(request)
        repo = _repository()
        workspace_id, environment = deps.control_plane_scope()
        evaluated_at = utc_now().astimezone(UTC)
        health: list[dict[str, Any]] = []
        source_as_of: list[datetime] = []
        source_hits: list[bool] = []
        source_failed = False
        findings_available = False
        try:
            raw_findings, fetched_at, hit = cache.cached(
                f"mission-control/open-findings/{workspace_id}/{environment}",
                lambda: repo.list_findings(state="OPEN", limit=500),
                refresh,
                ttl_seconds=30,
            )
            findings_available = True
            findings = [
                mask_for_viewer(row, actor)
                for row in raw_findings
            ]
            source_as_of.append(fetched_at)
            source_hits.append(hit)
            health.append(
                {
                    "source": "Platform findings",
                    "status": "healthy",
                    "freshness": fetched_at.isoformat(),
                    "notes": "Canonical evidence is available.",
                }
            )
        except Exception:  # noqa: BLE001 - an independent degraded section
            source_failed = True
            log.warning("Mission Control findings are unavailable", exc_info=True)
            raw_findings = []
            findings = []
            health.append(
                {
                    "source": "Platform findings",
                    "status": "unavailable",
                    "notes": "Run the control-plane migration and reporting jobs.",
                }
            )
        try:
            (
                pending_candidates,
                approved_candidates,
                expired,
                changes,
            ), fetched_at, hit = cache.cached(
                f"mission-control/actions-v3/{workspace_id}/{environment}",
                lambda: (
                    repo.list_actions(
                        status=ActionStatus.AWAITING_APPROVAL,
                        limit=500,
                    ),
                    repo.list_actions(status=ActionStatus.APPROVED, limit=500),
                    repo.list_actions(status=ActionStatus.EXPIRED, limit=500),
                    repo.list_actions(status=ActionStatus.SUCCEEDED, limit=5),
                ),
                refresh,
                ttl_seconds=15,
            )
            source_as_of.append(fetched_at)
            source_hits.append(hit)
            health.append(
                {
                    "source": "Approval ledger",
                    "status": "proposal_only" if repo.proposal_only else "healthy",
                    "freshness": fetched_at.isoformat(),
                    "notes": (
                        "Local/test plans cannot execute."
                        if repo.proposal_only
                        else "Durable Unity Catalog audit trail."
                    ),
                }
            )
        except Exception:  # noqa: BLE001 - an independent degraded section
            source_failed = True
            log.warning("Mission Control approval ledger is unavailable", exc_info=True)
            pending_candidates, approved_candidates, expired, changes = (
                [],
                [],
                [],
                [],
            )
            health.append(
                {
                    "source": "Approval ledger",
                    "status": "unavailable",
                    "notes": "Run the power-controller setup to create durable tables.",
                }
            )
        try:
            runtime, fetched_at, hit = cache.cached(
                f"mission-control/runtime/{workspace_id}/{environment}",
                lambda: repo.runtime_state(workspace_id, environment),
                refresh,
                ttl_seconds=15,
            )
            source_as_of.append(fetched_at)
            source_hits.append(hit)
            health.append(
                {
                    "source": "Runtime inventory",
                    "status": (
                        "healthy"
                        if runtime.get("source") == "unity-catalog"
                        else "unknown"
                    ),
                    "freshness": runtime.get("updated_at") or fetched_at.isoformat(),
                    "notes": runtime.get("source", "unknown"),
                }
            )
        except Exception:  # noqa: BLE001 - an independent degraded section
            source_failed = True
            log.warning("Mission Control runtime state is unavailable", exc_info=True)
            runtime = {
                "workspace_id": workspace_id,
                "environment": environment,
                "desired_state": "UNKNOWN",
                "actual_state": "UNKNOWN",
                "source": "unavailable",
            }
            health.append(
                {
                    "source": "Runtime inventory",
                    "status": "unavailable",
                    "notes": "Initialize the unscheduled power-controller job.",
                }
            )
        pending = [
            action
            for action in pending_candidates
            if _effective_status(action, evaluated_at)
            == ActionStatus.AWAITING_APPROVAL
        ]
        derived_expired = [
            action
            for action in [*pending_candidates, *approved_candidates]
            if _effective_status(action, evaluated_at) == ActionStatus.EXPIRED
        ]
        pending.sort(
            key=lambda action: (
                -_RISK_RANK.get(action.risk, 0),
                action.expires_at,
                action.created_at,
                action.action_id,
            )
        )
        expiring_soon_count = sum(
            1
            for action in pending
            if action.expires_at - evaluated_at <= timedelta(minutes=5)
        )
        expired_count = len(
            {
                action.action_id
                for action in [*expired, *derived_expired]
            }
        )
        decision_items = [
            mask_for_viewer(
                {
                    "action_id": action.action_id,
                    "action_type": action.action_type,
                    "status": action.status.value,
                    "raw_status": action.status.value,
                    "effective_status": _effective_status(
                        action,
                        evaluated_at,
                    ).value,
                    "risk": action.risk.value.lower(),
                    "target_count": len(action.targets),
                    "proposer_id": action.proposer_id,
                    "proposer_email": action.proposer_email,
                    "created_at": action.created_at.isoformat(),
                    "expires_at": action.expires_at.isoformat(),
                    "can_approve": _can_approve(
                        action,
                        actor=actor,
                        evaluated_at=evaluated_at,
                        repository=repo,
                    ),
                    "impact": action.impact,
                    "evidence_summary": _evidence_summary(
                        action,
                        raw_findings,
                        available=findings_available,
                    ),
                },
                actor,
            )
            for action in pending
        ]
        by_pillar = Counter(str(row.get("pillar") or "RISK") for row in findings)
        by_severity = Counter(str(row.get("severity") or "MEDIUM") for row in findings)
        by_action = Counter(
            str(row.get("proposed_action_type") or row.get("action") or "review")
            for row in findings
        )
        outcomes = {}
        for pillar, count in sorted(by_pillar.items()):
            pillar_rows = [
                row
                for row in findings
                if str(row.get("pillar") or "RISK") == pillar
            ]
            critical = sum(
                1
                for row in pillar_rows
                if str(row.get("severity") or "").upper() == "CRITICAL"
            )
            outcomes[pillar.lower()] = {
                "open_findings": count,
                "critical_findings": critical,
                "value": count,
                "status": "critical" if critical else ("attention" if count else "healthy"),
            }
        freshness = [
            str(row.get("freshness_at") or row.get("run_ts") or "")
            for row in findings
            if row.get("freshness_at") or row.get("run_ts")
        ]
        data = {
            "scope": {
                "workspace": workspace_id,
                "workspace_id": workspace_id,
                "environment": environment,
            },
            "outcomes": outcomes,
            "pending_approvals": len(pending),
            "decision_queue": {
                "evaluated_at": evaluated_at.isoformat(),
                "ranking": "risk-expiry-created-v1",
                "active_count": len(pending),
                "expiring_soon_count": expiring_soon_count,
                "expired_count": expired_count,
                "items": decision_items,
            },
            "decisions": findings[:3],
            "changes": [
                _action_body(
                    row,
                    actor=actor,
                    evaluated_at=evaluated_at,
                )
                for row in changes
            ],
            "findings": {
                "data": {
                    "run_ts": max(freshness) if freshness else None,
                    "total": len(findings),
                    "by_area": {
                        key.lower(): value for key, value in sorted(by_pillar.items())
                    },
                    "by_action": dict(by_action.most_common(8)),
                    "by_severity": dict(sorted(by_severity.items())),
                }
            },
            "runtime": runtime,
            "data_health": health,
        }
        response_as_of = min(source_as_of) if source_as_of else utc_now()
        return envelope(
            data,
            response_as_of,
            bool(source_hits) and all(source_hits) and not source_failed,
        )
    except Exception as exc:  # noqa: BLE001 - mapped to stable envelope
        return _error(exc)


@router.get("/api/action-requests")
def list_action_requests(
    request: Request,
    status: ActionStatus | None = None,
    action_type: str | None = None,
    limit: int = 100,
) -> Any:
    try:
        actor = _actor(request)
        evaluated_at = utc_now().astimezone(UTC)
        requested_limit = max(1, min(limit, 500))
        repository_status = (
            None if status == ActionStatus.EXPIRED else status
        )
        rows = _repository().list_actions(
            status=repository_status,
            action_type=action_type,
            limit=500 if status == ActionStatus.EXPIRED else requested_limit,
        )
        if status is not None:
            rows = [
                row
                for row in rows
                if _effective_status(row, evaluated_at) == status
            ]
        rows = rows[:requested_limit]
        return envelope(
            [
                _action_body(
                    row,
                    actor=actor,
                    evaluated_at=evaluated_at,
                )
                for row in rows
            ],
            evaluated_at,
            False,
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.get("/api/action-requests/{action_id}")
def get_action_request(action_id: str, request: Request) -> Any:
    try:
        actor = _actor(request)
        action = _repository().get_action(action_id)
        if action is None:
            raise ActionNotFoundError(f"Unknown action request {action_id}.")
        action.assert_integrity()
        return _action_body(
            action,
            actor=actor,
            detail=True,
            evaluated_at=utc_now(),
        )
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/api/action-requests/plan")
def plan_action_request(
    body: ActionPlanRequest,
    request: Request,
) -> Any:
    try:
        actor = _actor(request, proposer=True)
        action_type = _normalize_action_type(body.action_type)
        if action_type in _RUNTIME_ACTIONS:
            if body.parameters:
                raise ValueError(f"Action '{body.action_type}' accepts no parameters.")
            action = deps.get_runtime_controller_client().submit_plan(action_type)
            workspace_id, environment = deps.control_plane_scope()
            if (
                action.workspace_id != workspace_id
                or action.environment != environment
            ):
                raise PlanIntegrityError(
                    "Controller plan belongs to a different workspace or environment."
                )
            _write_repository(request).add_event(
                ActionEvent(
                    action_id=action.action_id,
                    event_type="PLAN_REQUESTED_FROM_APP",
                    actor_id=actor.actor_id,
                    details={"requester_email": actor.email},
                )
            )
        else:
            spec = _planner(action_type, body.parameters)
            action = _service(_write_repository(request)).plan(spec, actor)
        return _action_body(action, actor=actor)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/api/action-requests/{action_id}/approve")
def approve_action_request(
    action_id: str,
    body: ActionApprovalRequest,
    request: Request,
) -> Any:
    try:
        actor = _actor(request, approver=True)
        if not deps.actions_enabled():
            return JSONResponse(
                status_code=403,
                content=payload(
                    "actions_disabled",
                    "Approval and executor submission are disabled for this deployment.",
                    "Enable DBX_PLATFORM_CONSOLE_ACTIONS through reviewed deployment config.",
                ),
            )
        write_repository = _write_repository(request)
        action = _service(write_repository).approve(
            action_id,
            actor=actor,
            plan_hash=body.plan_hash,
            confirmation=body.confirmation,
            revalidate=_revalidate,
        )
        execution_id = None
        try:
            if action.action_type in _RUNTIME_ACTIONS:
                execution_id = deps.get_runtime_controller_client().submit_execute(
                    action
                )
            else:
                execution_id = deps.get_action_executor_client().submit(action)
            write_repository.add_event(
                ActionEvent(
                    action_id=action.action_id,
                    event_type="EXECUTION_SUBMITTED",
                    actor_id=actor.actor_id,
                    details={"run_id": execution_id},
                )
            )
        except Exception as exc:  # noqa: BLE001 - approval remains durable
            log.exception("action execution submission failed", exc_info=exc)
            try:
                write_repository.add_event(
                    ActionEvent(
                        action_id=action.action_id,
                        event_type="EXECUTION_SUBMISSION_FAILED",
                        actor_id=actor.actor_id,
                        details={"error_type": type(exc).__name__},
                    )
                )
            except Exception:  # noqa: BLE001 - original failure stays primary
                log.exception("failed to audit execution submission failure")
            executor_name = (
                "power-controller"
                if action.action_type in _RUNTIME_ACTIONS
                else "action-executor"
            )
            body = payload(
                "execution_submission_failed",
                "The plan was approved, but the executor run could not be submitted.",
                f"Use the {executor_name} Jobs UI with this action ID.",
            )
            body.update(
                {
                    "action_id": action.action_id,
                    "plan_hash": action.plan_hash,
                    "status": action.status.value,
                }
            )
            return JSONResponse(status_code=503, content=body)
        response = _action_body(action, actor=actor, detail=True)
        if execution_id is not None:
            response["execution_id"] = execution_id
        return response
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/api/action-requests/{action_id}/reject")
def reject_action_request(
    action_id: str,
    body: ActionRejectRequest,
    request: Request,
) -> Any:
    try:
        actor = _actor(request, approver=True)
        action = _service(_write_repository(request)).reject(
            action_id,
            actor=actor,
            plan_hash=body.plan_hash,
            reason=body.reason,
        )
        return _action_body(action, actor=actor, detail=True)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.get("/api/runtime/state")
def runtime_state(request: Request) -> Any:
    try:
        _actor(request)
        workspace_id, environment = deps.control_plane_scope()
        data = _repository().runtime_state(workspace_id, environment)
        data["current_state"] = data.get("actual_state")
        data["active_operation"] = data.get("active_action_id")
        return envelope(data, utc_now(), False)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.get("/api/runtime/inventory")
def runtime_inventory(request: Request) -> Any:
    try:
        _actor(request)
        workspace_id, environment = deps.control_plane_scope()
        rows = _repository().managed_resources(workspace_id, environment)
        return envelope(rows, utc_now(), False)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
