"""Performance and SLO views over normalized Mission Control findings."""

from __future__ import annotations

from fastapi import APIRouter

from backend import cache, deps
from backend.models import envelope

router = APIRouter(prefix="/api/performance")


def _findings(*terms: str) -> list[dict]:
    rows = deps.get_control_plane_repository().list_findings(
        pillar="PERFORMANCE",
        limit=1000,
    )
    lowered = tuple(term.lower() for term in terms)
    return [
        row
        for row in rows
        if any(
            term in " ".join(
                str(row.get(field) or "")
                for field in (
                    "check_name",
                    "reason",
                    "proposed_action_type",
                    "action",
                    "resource",
                    "slo_impact",
                )
            ).lower()
            for term in lowered
        )
    ]


def _route(cache_key: str, terms: tuple[str, ...], days: int, refresh: bool) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = cache.cached(
        f"{cache_key}/{days}",
        lambda: _findings(*terms),
        refresh,
    )
    response = envelope(data, as_of, hit)
    response["source_status"] = {
        "status": "partial",
        "source": "platform_findings",
        "notes": (
            "Only normalized findings emitted by deployed detectors are shown. "
            "Regression baseline collectors are not enabled in every workspace."
        ),
    }
    return response


@router.get("/job-regressions")
def job_regressions(days: int = 30, refresh: bool = False) -> dict:
    return _route(
        "performance/job-regressions",
        ("job", "duration", "queue", "retry", "sla"),
        days,
        refresh,
    )


@router.get("/query-regressions")
def query_regressions(days: int = 30, refresh: bool = False) -> dict:
    return _route(
        "performance/query-regressions",
        ("query", "scan", "warehouse", "queue"),
        days,
        refresh,
    )


@router.get("/serving-slo")
def serving_slo(days: int = 30, refresh: bool = False) -> dict:
    return _route(
        "performance/serving-slo",
        ("serving", "latency", "error", "retry", "slo"),
        days,
        refresh,
    )
