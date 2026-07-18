"""Guarded remediation actions — the ONLY module allowed to call mutators.

tests/test_app.py enforces that statically. Every mutation sits behind three
gates that mirror the CLI's dry-run/--apply/--yes semantics:

1. DBX_PLATFORM_CONSOLE_ACTIONS=true (app.yaml, reviewed in git; default off).
2. A plan: a fresh server-side dry-run whose items are stored server-side,
   single-use, and expire after 15 minutes.
3. A typed confirm phrase ("apply <action> <count>") on the apply call.

The actions themselves are the package's deliberately conservative mutators:
orphaned jobs are paused (never deleted), only over-age tokens are revoked,
policy sync never deletes unmanaged policies.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from backend import deps
from backend.errors import payload
from backend.models import ApplyRequest
from backend.plans import PlanExpiredError, PlanNotFoundError, PlanStore
from backend.routers.governance import packaged_policies_dir
from dbx_platform import governance, housekeeping, security

router = APIRouter(prefix="/api/actions")

log = logging.getLogger("platform_console.actions")

plans = PlanStore()


# --- per-action plan/apply implementations ---------------------------------

def _plan_stale_clusters() -> tuple[list[dict], list[dict], dict]:
    s = deps.get_settings()
    items = housekeeping.classify_clusters(
        housekeeping.fetch_clusters(deps.get_ws()), deps.now_ms(),
        s.stale_cluster_days, s.max_uptime_hours)
    summary = {
        "terminate": sum(1 for i in items if i["action"] == "terminate"),
        "permanent-delete": sum(1 for i in items if i["action"] == "permanent-delete"),
    }
    return items, items, summary


def _apply_stale_clusters(items: list[dict]) -> list[str]:
    return housekeeping.apply_cluster_findings(deps.get_ws(), items)


def _plan_orphaned_jobs() -> tuple[list[dict], list[dict], dict]:
    w = deps.get_ws()
    items = housekeeping.find_orphaned_jobs(
        housekeeping.fetch_jobs(w), housekeeping.fetch_active_principals(w))
    return items, items, {"pause": len(items)}


def _apply_orphaned_jobs(items: list[dict]) -> list[str]:
    w = deps.get_ws()
    done = []
    for item in items:
        if housekeeping.pause_job(w, item["job_id"]):
            done.append(f"paused job {item['job_id']} ({item.get('name', '')})")
        else:
            done.append(f"job {item['job_id']} already paused or has no schedule")
    return done


def _plan_token_revoke() -> tuple[list[dict], list[dict], dict]:
    s = deps.get_settings()
    findings = security.classify_tokens(
        security.fetch_tokens(deps.get_ws()), deps.now_ms(),
        s.token_max_age_days, s.token_expiry_warn_days)
    items = [f for f in findings if f["over_age"]]
    return items, items, {"revoke": len(items)}


def _apply_token_revoke(items: list[dict]) -> list[str]:
    return security.revoke_tokens(deps.get_ws(), items)


def _plan_policy_sync() -> tuple[list[dict], dict, dict]:
    plan = governance.diff_policies(
        governance.load_local_policies(packaged_policies_dir()),
        governance.fetch_remote_policies(deps.get_ws()),
    )
    items = (
        [{"name": p["name"], "action": "create"} for p in plan["create"]]
        + [{"name": p["name"], "action": "update"} for p in plan["update"]]
    )
    summary = {
        "create": len(plan["create"]),
        "update": len(plan["update"]),
        "unchanged": len(plan["unchanged"]),
        "unmanaged-untouched": len(plan["unmanaged"]),
    }
    return items, plan, summary


def _apply_policy_sync(plan: dict) -> list[str]:
    return governance.apply_policy_plan(deps.get_ws(), plan)


# Registry: plan_fn returns (display items, apply payload, summary counts).
REGISTRY: dict[str, dict] = {
    "stale-clusters": {"plan": _plan_stale_clusters, "apply": _apply_stale_clusters},
    "orphaned-jobs": {"plan": _plan_orphaned_jobs, "apply": _apply_orphaned_jobs},
    "token-revoke": {"plan": _plan_token_revoke, "apply": _apply_token_revoke},
    "policy-sync": {"plan": _plan_policy_sync, "apply": _apply_policy_sync},
}


def _entry(action: str) -> dict:
    entry = REGISTRY.get(action)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown action '{action}'")
    return entry


@router.post("/{action}/plan")
def plan(action: str, request: Request) -> dict:
    """Dry-run — always allowed, mutates nothing."""
    items, apply_payload, summary = _entry(action)["plan"]()
    stored = plans.create(action, items, apply_payload, summary)
    log.info("plan action=%s plan_id=%s items=%d by=%s", action, stored["plan_id"],
             len(items), request.headers.get("X-Forwarded-Email", "unknown"))
    return {
        "plan_id": stored["plan_id"],
        "action": action,
        "expires_at": stored["expires_at"],
        "items": items,
        "summary": summary,
        "confirm_phrase": stored["confirm_phrase"],
        "actions_enabled": deps.actions_enabled(),
    }


@router.post("/{action}/apply")
def apply(action: str, body: ApplyRequest, request: Request):
    entry = _entry(action)
    if not deps.actions_enabled():
        return JSONResponse(status_code=403, content=payload(
            "actions_disabled",
            "Remediation actions are disabled for this deployment.",
            "Set DBX_PLATFORM_CONSOLE_ACTIONS=true in apps/platform-console/app.yaml "
            "(git-reviewed) and redeploy. See docs/runbook.md for the required grants.",
        ))
    try:
        stored = plans.take(action, body.plan_id)
    except PlanNotFoundError:
        return JSONResponse(status_code=404, content=payload(
            "plan_not_found", "Unknown or already-used plan — create a new plan."))
    except PlanExpiredError:
        return JSONResponse(status_code=410, content=payload(
            "plan_expired", "The plan expired — re-plan to see current state."))
    if body.confirm != stored["confirm_phrase"]:
        return JSONResponse(status_code=409, content=payload(
            "confirmation_mismatch",
            f"Type the exact confirm phrase: '{stored['confirm_phrase']}'."))
    who = request.headers.get("X-Forwarded-Email", "unknown")
    log.info("apply action=%s plan_id=%s items=%d by=%s",
             action, body.plan_id, len(stored["items"]), who)
    applied = entry["apply"](stored["payload"])
    log.info("apply done action=%s plan_id=%s applied=%d by=%s",
             action, body.plan_id, len(applied), who)
    return {"plan_id": body.plan_id, "action": action, "applied": applied}
