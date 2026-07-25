"""Performance views over persisted telemetry and normalized Mission Control findings."""

from __future__ import annotations

from fastapi import APIRouter

from backend import cache, deps
from backend.models import envelope
from dbx_platform import ai_monitor

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


@router.get("/ai-gateway-telemetry")
def ai_gateway_telemetry(days: int = 30, refresh: bool = False) -> dict:
    """Read governed AI Gateway rate samples from the persisted monitor.

    The scheduled ``ai-monitor`` job owns feature detection and ingestion from
    the Beta Gateway system table. Keeping this route on the persisted table
    means a page view never reaches into a preview source directly.
    """

    days = deps.clamp_days(days)
    workspace_id, environment = deps.control_plane_scope()

    def load() -> list[dict]:
        settings = deps.get_settings()
        rows = ai_monitor.read_monitoring(
            deps.get_ws(),
            deps.warehouse_id(),
            settings.dashboard_catalog,
            settings.dashboard_schema,
            workspace_id,
            environment,
            days,
        )
        return [
            {
                "usage_date": row.get("usage_date"),
                "endpoint_name": row.get("endpoint_name"),
                "app": row.get("app"),
                "requests": row.get("requests"),
                "input_tokens": row.get("input_tokens"),
                "output_tokens": row.get("output_tokens"),
                "p95_latency_ms": row.get("p95_latency_ms"),
                "source": row.get("source"),
            }
            for row in rows
            if row.get("source") == ai_monitor.GATEWAY_USAGE_SOURCE
        ]

    data, as_of, hit = cache.cached(
        f"performance/ai-gateway-telemetry/{workspace_id}/{environment}/{days}",
        load,
        refresh,
    )
    response = envelope(data, as_of, hit)
    response["source_status"] = {
        "status": "healthy",
        "source": ai_monitor.GATEWAY_USAGE_SOURCE,
        "notes": (
            "Daily AI Gateway samples persisted by the governed ai-monitor job; "
            "an empty result can also mean there was no Gateway traffic in the window."
        ),
    }
    return response
