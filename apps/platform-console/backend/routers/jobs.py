"""Job kick-off — restricted to the bundle's own [dbx-platform] jobs.

run_now is deliberately outside the actions gate: the scheduled jobs are
report-only by definition (no --apply ever appears in resources/*.yml), so
triggering one early is safe. Jobs outside the name filter are refused.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from backend import cache, deps
from backend.models import envelope

router = APIRouter(prefix="/api/jobs")

log = logging.getLogger("platform_console.jobs")

JOB_MARKER = "dbx-platform"


def _platform_jobs() -> list[dict]:
    out = []
    for j in deps.get_ws().jobs.list():
        name = (j.settings.name if j.settings else "") or ""
        if JOB_MARKER in name:
            out.append({"job_id": j.job_id, "name": name})
    return sorted(out, key=lambda x: x["name"])


@router.get("")
def jobs(refresh: bool = False) -> dict:
    data, as_of, hit = cache.cached("jobs", _platform_jobs, refresh, ttl_seconds=120)
    return envelope(data, as_of, hit)


@router.get("/{job_id}/runs")
def runs(job_id: int, limit: int = 5) -> dict:
    limit = max(1, min(20, limit))
    out = []
    for r in deps.get_ws().jobs.list_runs(job_id=job_id, limit=limit):
        state = r.state
        out.append({
            "run_id": r.run_id,
            "state": state.life_cycle_state.value if state and state.life_cycle_state else "",
            "result": state.result_state.value if state and state.result_state else "",
            "started_ms": r.start_time or 0,
            "duration_ms": (r.end_time - r.start_time)
            if r.end_time and r.start_time else None,
        })
    return {"data": out, "count": len(out)}


@router.post("/{job_id}/run_now")
def run_now(job_id: int, request: Request) -> dict:
    allowed = {j["job_id"] for j in _platform_jobs()}
    if job_id not in allowed:
        raise HTTPException(status_code=404, detail="not a [dbx-platform] job")
    run = deps.get_ws().jobs.run_now(job_id=job_id)
    log.info("run_now job_id=%s run_id=%s by=%s", job_id, run.run_id,
             request.headers.get("X-Forwarded-Email", "unknown"))
    return {"run_id": run.run_id}
