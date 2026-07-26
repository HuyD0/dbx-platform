"""Presentation-ready, read-only AI inventory explorer APIs."""

from __future__ import annotations

import csv
import io
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from backend import cache, deps
from dbx_platform import ai_catalog, ai_inventory, llm_cost

router = APIRouter(prefix="/api/ai-governance")


def _load_snapshots() -> tuple[list[dict], list[dict], list[dict]]:
    settings = deps.get_settings()
    workspace_id, environment = deps.control_plane_scope()
    catalog_rows = ai_catalog.read_catalog(
        deps.get_ws(),
        deps.warehouse_id(),
        settings.dashboard_catalog,
        settings.dashboard_schema,
        workspace_id,
        environment,
    )
    access_failure: str | None = None
    try:
        access_rows = ai_catalog.read_access(
            deps.get_ws(),
            deps.warehouse_id(),
            settings.dashboard_catalog,
            settings.dashboard_schema,
            workspace_id,
            environment,
        )
    except Exception as exc:  # noqa: BLE001 - inventory remains useful without ACLs
        access_rows = []
        access_failure = type(exc).__name__
    try:
        health_rows = llm_cost.read_llm_source_health(
            deps.get_ws(),
            deps.warehouse_id(),
            settings.dashboard_catalog,
            settings.dashboard_schema,
            workspace_id,
            environment,
        )
    except Exception:  # noqa: BLE001 - absence is represented explicitly below
        health_rows = [
            {
                "source_key": "ai-catalog-source-health",
                "source": "AI catalog source health",
                "source_type": "inventory",
                "status": "unavailable",
                "row_count": 0,
                "notes": "Source-health evidence could not be read.",
            }
        ]
    if access_failure:
        health_rows.append(
            {
                "source_key": "ai-catalog-access",
                "source": "Model access evidence",
                "source_type": "inventory",
                "status": "partial",
                "row_count": 0,
                "notes": (
                    "Catalog entities are available, but access evidence could "
                    f"not be read ({access_failure})."
                ),
            }
        )
    return catalog_rows, access_rows, health_rows


def _snapshots(refresh: bool) -> tuple[
    tuple[list[dict], list[dict], list[dict]],
    object,
    bool,
]:
    workspace_id, environment = deps.control_plane_scope()
    return cache.cached(
        f"ai-governance/inventory-snapshots/{workspace_id}/{environment}",
        _load_snapshots,
        refresh,
    )


def _inventory_csv(rows: list[dict]) -> Response:
    fields = [
        "group_label",
        "source",
        "provider",
        "environment",
        "entity_type",
        "display_name",
        "model_key",
        "model_version",
        "endpoint_name",
        "owner",
        "status",
        "ownership",
        "risk",
        "risk_reasons",
        "exposure",
        "key_auth_enabled",
        "usage_tracking",
        "region",
        "subscription_id",
        "resource_group",
        "resource_id",
        "first_seen_at",
        "last_seen_at",
        "tags",
        "details_json",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                **row,
                "risk_reasons": json.dumps(row.get("risk_reasons") or []),
                "exposure": json.dumps(row.get("exposure") or []),
                "tags": json.dumps(row.get("tags") or {}, sort_keys=True),
            }
        )
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="ai-model-inventory.csv"'
        },
    )


@router.get("/inventory", response_model=None)
def inventory(
    q: str = Query(default="", max_length=300),
    source: str | None = Query(default=None, max_length=100),
    provider: str | None = Query(default=None, max_length=100),
    status: str | None = Query(default=None, max_length=100),
    entity_type: str | None = Query(default=None, max_length=100),
    environment: str | None = Query(default=None, max_length=100),
    owner: str | None = Query(default=None, max_length=200),
    exposure: str | None = Query(default=None, max_length=100),
    risk: str | None = Query(default=None, max_length=100),
    view: Literal["managed_or_risky", "customer_managed", "all"] = (
        "managed_or_risky"
    ),
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    export: bool = False,
    format: Literal["json", "csv"] = "json",
    refresh: bool = False,
) -> dict | Response:
    """Return grouped inventory entities, facets, health, and a stable cursor."""

    (catalog_rows, access_rows, health_rows), as_of, hit = _snapshots(refresh)
    try:
        page = ai_inventory.build_inventory_page(
            catalog_rows,
            access_rows,
            health_rows,
            query=q,
            source=source,
            provider=provider,
            status=status,
            entity_type=entity_type,
            environment=environment,
            owner=owner,
            exposure=exposure,
            risk=risk,
            view=view,
            cursor=cursor,
            limit=limit,
            export=export or format == "csv",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if format == "csv":
        return _inventory_csv(page["items"])
    return {
        "data": page,
        "count": page["total"],
        "as_of": as_of.isoformat(),
        "cached": hit,
        "next_cursor": page["next_cursor"],
    }


@router.get("/inventory/{model_key:path}")
def inventory_detail(model_key: str, refresh: bool = False) -> dict:
    """Return one model/deployment with its access graph and source health."""

    (catalog_rows, access_rows, health_rows), as_of, hit = _snapshots(refresh)
    detail = ai_inventory.build_inventory_detail(
        model_key,
        catalog_rows,
        access_rows,
        health_rows,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="Inventory entity not found.")
    return {
        "data": detail,
        "count": 1,
        "as_of": as_of.isoformat(),
        "cached": hit,
    }
