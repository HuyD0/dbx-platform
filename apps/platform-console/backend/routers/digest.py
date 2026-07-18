"""AI digest — stored digests plus background generation with polling.

Generation runs in a background thread because collect_findings plus the
ai_query call can outlast the Apps request proxy timeout; the UI polls the
task until it resolves. When ai_query is unavailable the task still resolves
with the raw findings (same degradation as the CLI).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend import cache, deps
from backend.models import envelope
from backend.plans import TaskStore
from dbx_platform import digest
from dbx_platform.system_tables import run_query

router = APIRouter(prefix="/api/digest")

tasks = TaskStore()


@router.get("")
def stored(limit: int = 5, refresh: bool = False) -> dict:
    limit = max(1, min(20, limit))

    def load() -> list[dict]:
        return run_query(
            deps.get_ws(),
            f"SELECT run_ts, days, model, digest FROM {deps.digest_table()} "
            f"ORDER BY run_ts DESC LIMIT {limit}",
            deps.warehouse_id(),
        )

    data, as_of, hit = cache.cached(f"digest/stored/{limit}", load, refresh)
    return envelope(data, as_of, hit)


def _generate() -> dict:
    w = deps.get_ws()
    s = deps.get_settings()
    wid = deps.warehouse_id()
    findings, skipped = digest.collect_findings(w, s, wid, deps.now_ms(), s.lookback_days)
    prompt = digest.build_digest_prompt(findings, skipped, s.lookback_days)
    try:
        summary = digest.summarize(w, wid, s.digest_model, prompt)
    except Exception as e:  # noqa: BLE001 — digest is garnish, findings still land
        return {"digest": None, "findings": findings, "skipped": skipped,
                "stored": False, "error": str(e)}
    stored_ok = True
    try:
        digest.store_digest(w, wid, s.dashboard_catalog, s.dashboard_schema,
                            s.lookback_days, s.digest_model, summary, findings)
    except Exception:  # noqa: BLE001 — the summary is still worth returning
        stored_ok = False
    return {"digest": summary, "findings": findings, "skipped": skipped, "stored": stored_ok}


@router.post("/generate", status_code=202)
def generate() -> dict:
    return {"task_id": tasks.start(_generate)}


@router.get("/generate/{task_id}")
def generate_status(task_id: str) -> dict:
    status = tasks.get(task_id)
    if status is None:
        raise HTTPException(status_code=404, detail="unknown task")
    return status
