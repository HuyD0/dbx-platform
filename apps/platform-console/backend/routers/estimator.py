"""AI Cost Planner API: pattern catalog, deterministic estimates, extraction.

Estimates are pure math over the latest stored price snapshot — the app never
writes pricing (the app service principal has no table MODIFY; the persisted
refresh path is the governed ``estimator-prices-pull`` job). Every response
carries the snapshot date, engine version and rate-card version so a shown
number is always reproducible. The one AI-powered route (``/extract``) is
operator-gated like chat and only feeds the human review screen.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend import cache, deps
from backend.errors import payload
from backend.models import envelope
from dbx_platform import estimator, estimator_pricing, llm_cost

log = logging.getLogger("platform_console")

router = APIRouter(prefix="/api/estimator")

_SNAPSHOT_HINT = (
    "Run the '[dbx-platform] estimator-prices-pull' job once (schedule or an "
    "approved manual run) to store a price snapshot, then retry."
)


class EstimateRequest(BaseModel):
    requirements: dict
    rigor_pct: int = Field(default=10, ge=0, le=100)


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


def _snapshot_rows(currency: str, refresh: bool = False):
    workspace_id, environment = deps.control_plane_scope()

    def load() -> list[dict]:
        return estimator_pricing.read_latest_snapshot(
            deps.get_ws(),
            deps.warehouse_id(),
            deps.get_settings().dashboard_catalog,
            deps.get_settings().dashboard_schema,
            environment=environment,
            currency=currency,
        )

    key = f"estimator/snapshot/{workspace_id}/{environment}/{currency}"
    return cache.cached(key, load, refresh)


@router.get("/patterns")
def patterns() -> dict:
    catalog = estimator.load_patterns()
    data = [
        {"pattern": key, **{k: p[k] for k in ("label", "description", "example_prompt")},
         "defaults": p["defaults"]}
        for key, p in sorted(catalog["patterns"].items())
    ]
    value, as_of, hit = cache.cached("estimator/patterns", lambda: data)
    return envelope(value, as_of, hit)


@router.post("/estimate")
def estimate(body: EstimateRequest, refresh: bool = False):
    try:
        requirements = estimator.validate_requirements(body.requirements)
    except ValueError as error:
        return JSONResponse(
            status_code=422, content=payload("invalid_requirements", str(error))
        )
    try:
        rows, as_of, hit = _snapshot_rows(requirements.currency, refresh)
    except Exception as error:  # noqa: BLE001 - migration may be pending
        log.info("estimator price snapshot unavailable", exc_info=error)
        return JSONResponse(
            status_code=503,
            content=payload(
                "pricing_snapshot_unavailable",
                "The price snapshot table is unavailable.",
                _SNAPSHOT_HINT,
            ),
        )
    if not rows:
        return JSONResponse(
            status_code=503,
            content=payload(
                "pricing_snapshot_missing",
                f"No stored price snapshot exists for {requirements.currency}.",
                _SNAPSHOT_HINT,
            ),
        )
    book = estimator.build_price_book(rows, estimator.load_rate_card())
    matrix = estimator.compute_matrix(
        requirements, rigor_pct=body.rigor_pct, price_book=book
    )
    return envelope(matrix, as_of, hit)


@router.get("/pricing-status")
def pricing_status(refresh: bool = False) -> dict:
    _, environment = deps.control_plane_scope()

    def load() -> dict:
        try:
            sources = estimator_pricing.read_snapshot_status(
                deps.get_ws(),
                deps.warehouse_id(),
                deps.get_settings().dashboard_catalog,
                deps.get_settings().dashboard_schema,
                environment=environment,
            )
        except Exception as error:  # noqa: BLE001 - migration may be pending
            return {
                "sources": [],
                "coverage_findings": [],
                "notes": [],
                "health": llm_cost.coverage_record(
                    "Estimator price snapshots",
                    "unavailable",
                    freshness="unknown",
                    retention_days=None,
                    notes=(
                        "Snapshot table unavailable; run the schema migration and "
                        f"the estimator-prices-pull job ({error.__class__.__name__})."
                    ),
                ),
            }
        rows, _, _ = _snapshot_rows("USD")
        snapshot_date = max((str(r.get("snapshot_date")) for r in rows), default="")
        findings, notes = estimator_pricing.classify_price_coverage(
            estimator.load_rate_card(), rows, snapshot_date
        )
        return {
            "sources": sources,
            "snapshot_date": snapshot_date or None,
            "coverage_findings": findings,
            "notes": notes,
            "health": llm_cost.coverage_record(
                "Estimator price snapshots",
                "available" if rows and not findings else "partial" if rows else "unavailable",
                freshness=snapshot_date or "never",
                retention_days=None,
                row_count=len(rows),
            ),
        }

    value, as_of, hit = cache.cached(f"estimator/status/{environment}", load, refresh)
    return envelope(value, as_of, hit)


def _extraction_model():
    """CAN_QUERY-bound chat model; imported lazily so the API stays testable
    without the App's LangChain dependencies installed."""

    import os

    from backend.agent_runtime import DatabricksChatModel, configure_mlflow_tracing

    experiment_id = os.environ.get("MLFLOW_EXPERIMENT_ID", "").strip()
    if experiment_id:
        # Extraction traces land in the same App-bound experiment as chat.
        configure_mlflow_tracing(experiment_id)
    return DatabricksChatModel(
        endpoint=deps.chat_endpoint(),
        workspace_client=deps.get_ws(),
        temperature=0.0,
        max_tokens=1200,
    )


@router.post("/extract", dependencies=[Depends(deps.require_operator)])
def extract(body: ExtractRequest, request: Request):
    from backend import estimator_extraction

    del request  # authenticated via the global boundary + operator dependency
    try:
        requirements, warnings = estimator_extraction.extract_requirements(
            _extraction_model(), body.text
        )
    except estimator_extraction.ExtractionError as error:
        return JSONResponse(
            status_code=422, content=payload("extraction_failed", str(error))
        )
    except Exception as error:  # noqa: BLE001 - endpoint is optional; degrade politely
        log.info("estimator extraction endpoint unavailable", exc_info=error)
        return JSONResponse(
            status_code=503,
            content=payload(
                "extraction_unavailable",
                "The AI extraction step is currently unavailable; fill in the "
                "wizard manually instead.",
            ),
        )
    return {"requirements": requirements, "warnings": warnings}
