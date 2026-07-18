"""Read-only adapters for the durable generic action planner.

This module deliberately exposes no HTTP routes and calls no mutators. The
dedicated executor reloads approved durable actions and owns the allowlisted
write implementations.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend import deps
from backend.routers.governance import packaged_policies_dir
from dbx_platform import governance, housekeeping, security

router = APIRouter(prefix="/api/actions")


# --- per-action plan/apply implementations ---------------------------------

def _plan_stale_clusters() -> tuple[list[dict], list[dict], dict]:
    s = deps.get_settings()
    findings = housekeeping.classify_clusters(
        housekeeping.fetch_clusters(deps.get_ws()), deps.now_ms(),
        s.stale_cluster_days, s.max_uptime_hours)
    # Permanent deletion is outside Mission Control v1. Keep those rows as
    # findings, but never place them in an executable plan.
    items = [item for item in findings if item.get("action") == "terminate"]
    summary = {
        "terminate": sum(1 for i in items if i["action"] == "terminate"),
        "deletion-candidates-excluded": sum(
            1 for item in findings if item.get("action") == "review-retention"
        ),
    }
    return items, items, summary


def _plan_orphaned_jobs() -> tuple[list[dict], list[dict], dict]:
    w = deps.get_ws()
    findings = housekeeping.find_orphaned_jobs(
        housekeeping.fetch_jobs(w), housekeeping.fetch_active_principals(w))
    items = [row for row in findings if row.get("has_schedule")]
    return items, items, {
        "pause": len(items),
        "unscheduled-findings-excluded": len(findings) - len(items),
    }


def _plan_token_revoke() -> tuple[list[dict], list[dict], dict]:
    s = deps.get_settings()
    findings = security.classify_tokens(
        security.fetch_tokens(deps.get_ws()), deps.now_ms(),
        s.token_max_age_days, s.token_expiry_warn_days)
    items = [f for f in findings if f["over_age"]]
    return items, items, {"revoke": len(items)}


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


# Registry: plan_fn returns (display items, apply payload, summary counts).
REGISTRY: dict[str, dict] = {
    "stale-clusters": {"plan": _plan_stale_clusters},
    "orphaned-jobs": {"plan": _plan_orphaned_jobs},
    "token-revoke": {"plan": _plan_token_revoke},
    "policy-sync": {"plan": _plan_policy_sync},
}


def _entry(action: str) -> dict:
    entry = REGISTRY.get(action)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown action '{action}'")
    return entry


def build_action_plan(
    action: str, parameters: dict | None = None
) -> tuple[list[dict], object, dict]:
    """Run a registered read-only planner for the generic approval API.

    Existing actions currently have no user-controlled parameters. Rejecting
    unknown inputs prevents a proposal or client from smuggling executor data
    into the immutable plan.
    """
    if parameters:
        raise ValueError(f"Action '{action}' does not accept parameters.")
    return _entry(action)["plan"]()
