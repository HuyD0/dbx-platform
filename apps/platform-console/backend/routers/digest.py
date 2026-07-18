"""Stored AI digests and the governed manual-generation compatibility route."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from backend import cache, deps
from backend.errors import payload
from backend.models import envelope
from dbx_platform.system_tables import run_query

router = APIRouter(
    prefix="/api/digest",
    dependencies=[Depends(deps.require_operator)],
)

@router.get("")
def stored(limit: int = 5, refresh: bool = False) -> dict:
    limit = max(1, min(20, limit))
    workspace_id, environment = deps.control_plane_scope()

    def load() -> list[dict]:
        return run_query(
            deps.get_ws(),
            f"SELECT run_ts, days, model, digest FROM {deps.digest_table()} "
            "WHERE workspace_id = :workspace_id AND environment = :environment "
            "ORDER BY run_ts DESC LIMIT :limit",
            deps.warehouse_id(),
            {
                "workspace_id": workspace_id,
                "environment": environment,
                "limit": limit,
            },
        )

    data, as_of, hit = cache.cached(
        f"digest/stored/{workspace_id}/{environment}/{limit}",
        load,
        refresh,
    )
    return envelope(data, as_of, hit)


@router.post("/generate", status_code=202)
def generate() -> JSONResponse:
    """Compatibility route; costly stateful generation must use an approved job."""
    return JSONResponse(
        status_code=409,
        content=payload(
            "approval_required",
            "Manual digest generation requires an approved job action.",
            "Plan the bundle-owned digest job through Action Center.",
        ),
    )
