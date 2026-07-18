"""Overview: stored findings summary, spend by SKU, digest freshness.

Sections degrade independently — a missing findings table must not blank the
spend chart, so each section resolves to {data} or {error}.
"""

from __future__ import annotations

import logging
from collections import Counter

from fastapi import APIRouter, Request

from backend import cache, deps
from backend.errors import payload
from backend.identity import mask_for_viewer
from backend.models import envelope
from dbx_platform import cost
from dbx_platform.system_tables import run_query

router = APIRouter()
log = logging.getLogger("platform_console")


def _latest_findings() -> list[dict]:
    t = deps.findings_table()
    return run_query(
        deps.get_ws(),
        f"SELECT run_ts, area, check_name, resource, reason, action FROM {t} "
        f"WHERE run_ts = (SELECT MAX(run_ts) FROM {t}) ORDER BY area, check_name",
        deps.warehouse_id(),
    )


def _section(loader):
    try:
        return {"data": loader()}
    except Exception as exc:  # noqa: BLE001 — sections degrade independently
        log.info("overview dependency unavailable", exc_info=exc)
        return {
            "error": payload(
                "dependency_unavailable",
                "This data source is currently unavailable.",
                "Check source health in Settings and run the governed collector.",
            )
        }


@router.get("/api/overview")
def overview(refresh: bool = False) -> dict:
    def load() -> dict:
        w = deps.get_ws()
        s = deps.get_settings()

        def findings() -> dict:
            rows = _latest_findings()
            return {
                "run_ts": rows[0]["run_ts"] if rows else None,
                "total": len(rows),
                "by_area": dict(sorted(Counter(r["area"] for r in rows).items())),
                "by_action": dict(Counter(r["action"] for r in rows).most_common(8)),
            }

        def spend() -> list[dict]:
            rows = cost.usage_report(w, deps.warehouse_id(), s.lookback_days)
            rows.sort(key=lambda r: float(r.get("list_cost_usd") or 0), reverse=True)
            return rows[:10]

        def digest_at() -> str | None:
            rows = run_query(
                w, f"SELECT MAX(run_ts) AS run_ts FROM {deps.digest_table()}",
                deps.warehouse_id(),
            )
            return rows[0]["run_ts"] if rows else None

        return {
            "findings": _section(findings),
            "spend": _section(spend),
            "digest": _section(lambda: {"latest_run_ts": digest_at()}),
        }

    data, as_of, hit = cache.cached("overview", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/api/findings")
def findings(
    request: Request,
    area: str | None = None,
    pillar: str | None = None,
    state: str | None = None,
    limit: int = 500,
    refresh: bool = False,
) -> dict:
    """Canonical findings, with ``area`` retained as a compatibility alias."""
    selected_pillar = pillar or (area.upper() if area else None)

    def load() -> list[dict]:
        return deps.get_control_plane_repository().list_findings(
            pillar=selected_pillar,
            state=state,
            limit=max(1, min(limit, 1000)),
        )

    cache_key = f"findings:{selected_pillar}:{state}:{limit}"
    data, as_of, hit = cache.cached(cache_key, load, refresh)
    actor = deps.require_verified_user(request)
    return envelope([mask_for_viewer(row, actor) for row in data], as_of, hit)
