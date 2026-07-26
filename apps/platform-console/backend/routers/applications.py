"""Application-centric cost and cross-cloud tag evidence."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from backend import cache, deps
from backend.models import (
    ApplicationEvidenceEnvelope,
    ApplicationListEnvelope,
    ApplicationProfileEnvelope,
    AttributionMethod,
)
from dbx_platform import application_cost

router = APIRouter(prefix="/api/applications")
Window = Literal["7d", "30d", "90d"]
EvidenceFormat = Literal["json", "csv"]
_WINDOW_DAYS: dict[Window, int] = {"7d": 7, "30d": 30, "90d": 90}


def _application_data(
    window: Window,
    refresh: bool,
) -> tuple[list[dict], list[dict], datetime, bool]:
    workspace_id, environment = deps.control_plane_scope()
    settings = deps.get_settings()
    days = _WINDOW_DAYS[window]

    def load():
        return application_cost.read_application_evidence(
            deps.get_ws(),
            deps.warehouse_id(),
            settings.dashboard_catalog,
            settings.dashboard_schema,
            workspace_id=workspace_id,
            environment=environment,
            subscription_id=settings.azure_subscription_id,
            days=days,
            tag_keys=settings.application_tag_key_list(),
        )

    (evidence, health), as_of, was_cached = cache.cached(
        f"applications/{workspace_id}/{environment}/{window}",
        load,
        refresh,
        ttl_seconds=60,
    )
    return evidence, health, as_of, was_cached


def _summary_matches(
    summary: dict,
    *,
    q: str | None,
    environment: str | None,
    source: str | None,
) -> bool:
    if q:
        needle = q.strip().casefold()
        if needle and needle not in " ".join(
            (
                str(summary.get("application_key") or ""),
                str(summary.get("display_name") or ""),
            )
        ).casefold():
            return False
    if environment and environment not in (summary.get("environments") or []):
        return False
    return not (source and source not in (summary.get("sources") or []))


@router.get("", response_model=ApplicationListEnvelope)
def list_applications(
    window: Window = "30d",
    q: str | None = Query(default=None, max_length=200),
    environment: str | None = Query(default=None, max_length=100),
    source: str | None = Query(default=None, max_length=100),
    cursor: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=24, ge=1, le=100),
    refresh: bool = False,
) -> dict:
    q = q if isinstance(q, str) else None
    environment = environment if isinstance(environment, str) else None
    source = source if isinstance(source, str) else None
    cursor = cursor if isinstance(cursor, str) else None
    limit = limit if isinstance(limit, int) else 24
    evidence, health, as_of, was_cached = _application_data(window, refresh)
    all_summaries = application_cost.build_portfolio(
        evidence,
        source_health=health,
    )
    filtered = [
        summary
        for summary in all_summaries
        if _summary_matches(
            summary,
            q=q,
            environment=environment,
            source=source,
        )
    ]
    try:
        page, next_cursor = application_cost.paginate(
            filtered,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = ApplicationListEnvelope.model_validate(
        {
            "data": {
                "applications": page,
                "facets": {
                    "environments": sorted(
                        {
                            item
                            for summary in all_summaries
                            for item in summary["environments"]
                        }
                    ),
                    "sources": sorted(
                        {
                            item
                            for summary in all_summaries
                            for item in summary["sources"]
                        }
                    ),
                },
                "next_cursor": next_cursor,
            },
            "count": len(filtered),
            "as_of": as_of.isoformat(),
            "cached": was_cached,
        }
    )
    return response.model_dump(mode="json")


@router.get("/{application_key}", response_model=ApplicationProfileEnvelope)
def application_profile(
    application_key: str,
    window: Window = "30d",
    refresh: bool = False,
) -> dict:
    evidence, health, as_of, was_cached = _application_data(window, refresh)
    profile = application_cost.build_profile(
        application_key,
        evidence,
        source_health=health,
        days=_WINDOW_DAYS[window],
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Application evidence was not found.")
    response = ApplicationProfileEnvelope.model_validate(
        {
            "data": profile,
            "count": None,
            "as_of": as_of.isoformat(),
            "cached": was_cached,
        }
    )
    return response.model_dump(mode="json")


def _evidence_for_application(
    application_key: str,
    evidence: list[dict],
    *,
    environment: str | None,
    source: str | None,
    q: str | None,
    attribution_method: AttributionMethod | None,
) -> list[dict]:
    key = application_cost.normalize_application_key(application_key)
    exact = [row for row in evidence if row.get("application_key") == key]
    azure_groups = {
        row.get("resource_group")
        for row in exact
        if row.get("source") == "azure" and row.get("resource_group")
    }
    rows = [
        row
        for row in evidence
        if (
            row.get("application_key") == key
            or key in (row.get("conflict_values") or [])
            or (
                row.get("source") == "azure"
                and row.get("resource_group") in azure_groups
                and row.get("attribution_method")
                in {"SHARED_UNALLOCATED", "UNATTRIBUTED"}
            )
        )
    ]
    if environment:
        rows = [row for row in rows if row.get("environment") == environment]
    if source:
        rows = [row for row in rows if row.get("source") == source]
    if attribution_method:
        rows = [
            row
            for row in rows
            if row.get("attribution_method") == attribution_method
        ]
    if q:
        needle = q.strip().casefold()
        if needle:
            rows = [
                row
                for row in rows
                if needle
                in " ".join(
                    str(row.get(field) or "")
                    for field in (
                        "resource_name",
                        "resource_id",
                        "service",
                        "workload",
                        "raw_application",
                        "tag_key",
                    )
                ).casefold()
            ]
    return sorted(
        rows,
        key=lambda row: (
            str(row.get("usage_date") or ""),
            str(row.get("source") or ""),
            str(row.get("resource_id") or ""),
        ),
        reverse=True,
    )


def _csv_response(application_key: str, rows: list[dict]) -> Response:
    fields = [
        "evidence_id",
        "usage_date",
        "source",
        "environment",
        "resource_type",
        "resource_id",
        "resource_name",
        "resource_group",
        "resource_aliases",
        "service",
        "workload",
        "application_key",
        "raw_application",
        "attribution_method",
        "tag_key",
        "tags",
        "identity_tags",
        "tag_observations",
        "cost",
        "cost_known",
        "inventory_only",
        "currency",
        "pricing_basis",
        "evidence_at",
        "scope",
        "snapshot_id",
        "evidence_refs",
        "job_id",
        "run_id",
        "trigger_type",
        "unpriced_usage_quantity",
        "conflict_values",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                **row,
                "tags": json.dumps(row.get("tags") or {}, sort_keys=True),
                "identity_tags": json.dumps(
                    row.get("identity_tags") or {},
                    sort_keys=True,
                ),
                "resource_aliases": json.dumps(
                    row.get("resource_aliases") or {},
                    sort_keys=True,
                ),
                "tag_observations": json.dumps(
                    row.get("tag_observations") or [],
                    sort_keys=True,
                ),
                "evidence_refs": json.dumps(row.get("evidence_refs") or []),
                "conflict_values": json.dumps(row.get("conflict_values") or []),
            }
        )
    safe_name = re_safe_filename(application_key)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{safe_name}-application-evidence.csv"'
            )
        },
    )


def re_safe_filename(value: str) -> str:
    return "".join(
        character
        for character in str(value)
        if character.isalnum() or character in {"-", "_"}
    )[:100] or "application"


@router.get(
    "/{application_key}/evidence",
    response_model=ApplicationEvidenceEnvelope,
    responses={
        200: {
            "content": {
                "text/csv": {
                    "schema": {"type": "string"},
                }
            }
        }
    },
)
def application_evidence(
    application_key: str,
    window: Window = "30d",
    environment: str | None = Query(default=None, max_length=100),
    source: str | None = Query(default=None, max_length=100),
    q: str | None = Query(default=None, max_length=200),
    attribution_method: AttributionMethod | None = None,
    cursor: str | None = Query(default=None, max_length=500),
    limit: int = Query(default=50, ge=1, le=200),
    format: EvidenceFormat = "json",
    refresh: bool = False,
) -> dict | Response:
    environment = environment if isinstance(environment, str) else None
    source = source if isinstance(source, str) else None
    q = q if isinstance(q, str) else None
    attribution_method = (
        attribution_method
        if isinstance(attribution_method, str)
        else None
    )
    cursor = cursor if isinstance(cursor, str) else None
    limit = limit if isinstance(limit, int) else 50
    evidence, _health, as_of, was_cached = _application_data(window, refresh)
    rows = _evidence_for_application(
        application_key,
        evidence,
        environment=environment,
        source=source,
        q=q,
        attribution_method=attribution_method,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Application evidence was not found.")
    if format == "csv":
        return _csv_response(application_key, rows)
    try:
        page, next_cursor = application_cost.paginate(
            rows,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response = ApplicationEvidenceEnvelope.model_validate(
        {
            "data": {"items": page, "next_cursor": next_cursor},
            "count": len(rows),
            "as_of": as_of.isoformat(),
            "cached": was_cached,
        }
    )
    return response.model_dump(mode="json")
