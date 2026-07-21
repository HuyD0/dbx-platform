"""Owned job inventory and governed manual-run compatibility endpoint."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from backend import cache, deps
from backend.control_plane import sha256_json
from backend.errors import payload
from backend.models import envelope

router = APIRouter(
    prefix="/api/jobs",
    dependencies=[Depends(deps.require_verified_user)],
)


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _job_ids_from_env(name: str) -> set[int]:
    """Parse an exact, deployment-bound set of Job IDs."""

    raw = os.getenv(name, "")
    ids: set[int] = set()
    for value in raw.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            job_id = int(value)
        except ValueError as exc:
            raise RuntimeError(
                f"{name} must contain exact comma-separated numeric Job IDs"
            ) from exc
        if job_id <= 0:
            raise RuntimeError(f"{name} contains an invalid Job ID")
        ids.add(job_id)
    return ids


def _governed_manual_job_ids() -> set[int]:
    """Return exact bundle-bound Jobs that are runnable but never hibernated."""

    return _job_ids_from_env("DBX_PLATFORM_GOVERNED_MANUAL_JOB_IDS")


def is_low_risk_manual_job(job_id: int) -> bool:
    """Return whether a bundle-bound report Job may use click confirmation."""

    return job_id in _job_ids_from_env("DBX_PLATFORM_LOW_RISK_JOB_IDS")


def _platform_jobs() -> list[dict]:
    workspace_id, environment = deps.control_plane_scope()
    inventory = deps.get_control_plane_repository().managed_resources(
        workspace_id,
        environment,
    )
    owned_ids = {
        int(resource["resource_id"])
        for resource in inventory
        if str(resource.get("resource_type") or "").upper() == "JOB"
        and str(resource.get("ownership") or "").upper() == "BUNDLE"
        and not _as_bool(resource.get("protected"))
    }
    # Manual stateful Jobs (for example forecast training) are protected from
    # Hibernate but may still be planned through Action Center. Their exact
    # bundle resource IDs are injected into the app; no name-based discovery
    # or broad workspace scan can add a runnable target.
    owned_ids.update(_governed_manual_job_ids())
    out = []
    for j in deps.get_ws().jobs.list():
        settings = j.settings
        name = (settings.name if settings else "") or ""
        if j.job_id is not None and int(j.job_id) in owned_ids:
            schedule = getattr(settings, "schedule", None) if settings else None
            pause_status = getattr(schedule, "pause_status", None)
            schedule_status = str(
                getattr(pause_status, "value", pause_status) or "UNSCHEDULED"
            ).upper()
            out.append({
                "job_id": j.job_id,
                "name": name,
                "schedule_status": schedule_status,
                "schedule_type": "CRON" if schedule is not None else "MANUAL_ONLY",
            })
    return sorted(out, key=lambda x: x["name"])


@router.get("")
def jobs(refresh: bool = False) -> dict:
    workspace_id, environment = deps.control_plane_scope()
    data, as_of, hit = cache.cached(
        f"jobs:{workspace_id}:{environment}",
        _platform_jobs,
        refresh,
        ttl_seconds=120,
    )
    return envelope(data, as_of, hit)


@router.get("/{job_id}/runs")
def runs(job_id: int, limit: int = 5) -> dict:
    governed_ids = {int(job["job_id"]) for job in _platform_jobs()}
    if job_id not in governed_ids:
        raise HTTPException(status_code=404, detail="not a governed dbx-platform job")
    limit = max(1, min(20, limit))
    out = []
    for r in deps.get_ws().jobs.list_runs(job_id=job_id, limit=limit):
        state = r.state
        out.append({
            "run_id": r.run_id,
            "state": state.life_cycle_state.value if state and state.life_cycle_state else "",
            "result": state.result_state.value if state and state.result_state else "",
            "state_message": state.state_message if state else "",
            "started_ms": r.start_time or 0,
            "duration_ms": (r.end_time - r.start_time)
            if r.end_time and r.start_time else None,
        })
    return {"data": out, "count": len(out)}


def build_job_run_plan(
    job_id: int,
    claimed_name: str | None = None,
) -> tuple[list[dict], dict, dict]:
    jobs_by_id = {int(job["job_id"]): job for job in _platform_jobs()}
    job = jobs_by_id.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="not a [dbx-platform] job")
    if claimed_name and claimed_name != job["name"]:
        raise ValueError("The requested job name does not match the current workspace job.")
    details = deps.get_ws().jobs.get(job_id)
    if details.settings is None:
        raise ValueError("The requested job has no readable settings.")
    settings = details.settings.as_dict()
    current_name = str(settings.get("name") or "")
    if current_name != job["name"]:
        raise ValueError("The requested job changed while its plan was being prepared.")
    job_state = {
        "job_id": job_id,
        "settings": settings,
        "run_as_user_name": details.run_as_user_name,
    }
    target = {
        "resource_type": "JOB",
        "resource_id": str(job_id),
        "job_id": job_id,
        "name": current_name,
        "action": "RUN_NOW",
        "settings_sha256": sha256_json(job_state),
        "job_state": job_state,
    }
    return [target], {"job_id": job_id}, {"run": 1}


@router.post("/{job_id}/run_now")
def run_now(job_id: int) -> JSONResponse:
    """Compatibility route that can no longer invoke an unapproved write."""
    build_job_run_plan(job_id)
    return JSONResponse(
        status_code=409,
        content=payload(
            "approval_required",
            "Manual job runs require an approved immutable action request.",
            "Create action type 'run-job' with this job_id in Action Center.",
        ),
    )
