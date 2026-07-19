"""AI governance — read-only views over the persisted AI inventory tables.

Serves the cross-source model catalog (`ai_model_catalog`), the normalized
"who can invoke which model" access graph (`ai_model_access`), and the per-app
usage rollup (`ai_app_monitoring`) that the scheduled ai-catalog and
ai-monitor jobs maintain. Until those jobs have run, the tables are absent and
the routes degrade through the standard error taxonomy (setup hint included).
"""

from __future__ import annotations

from fastapi import APIRouter

from backend import cache, deps
from backend.models import envelope
from dbx_platform import ai_catalog, ai_monitor

router = APIRouter(prefix="/api/ai-governance")


@router.get("/catalog")
def catalog(source: str | None = None, refresh: bool = False) -> dict:
    if source and source not in ai_catalog.SOURCES:
        raise ValueError(f"source must be one of {sorted(ai_catalog.SOURCES)}")
    workspace_id, environment = deps.control_plane_scope()

    def load() -> list[dict]:
        s = deps.get_settings()
        return ai_catalog.read_catalog(
            deps.get_ws(),
            deps.warehouse_id(),
            s.dashboard_catalog,
            s.dashboard_schema,
            workspace_id,
            environment,
            source=source,
        )

    data, as_of, hit = cache.cached(
        f"ai-governance/catalog/{workspace_id}/{source or 'all'}", load, refresh
    )
    return envelope(data, as_of, hit)


@router.get("/access")
def access(
    model_key: str | None = None,
    principal: str | None = None,
    refresh: bool = False,
) -> dict:
    workspace_id, environment = deps.control_plane_scope()

    def load() -> list[dict]:
        s = deps.get_settings()
        return ai_catalog.read_access(
            deps.get_ws(),
            deps.warehouse_id(),
            s.dashboard_catalog,
            s.dashboard_schema,
            workspace_id,
            environment,
            model_key=model_key,
            principal=principal,
        )

    data, as_of, hit = cache.cached(
        f"ai-governance/access/{workspace_id}/{model_key or 'all'}/{principal or 'all'}",
        load,
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/monitor")
def monitor(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    workspace_id, environment = deps.control_plane_scope()

    def load() -> list[dict]:
        s = deps.get_settings()
        return ai_monitor.report(
            deps.get_ws(),
            deps.warehouse_id(),
            s.dashboard_catalog,
            s.dashboard_schema,
            workspace_id,
            environment,
            days,
        )

    data, as_of, hit = cache.cached(
        f"ai-governance/monitor/{workspace_id}/{days}", load, refresh
    )
    return envelope(data, as_of, hit)
