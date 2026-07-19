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

from fastapi import APIRouter, Depends, Request, UploadFile
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


class LinkDeploymentRequest(BaseModel):
    estimate_id: str = Field(min_length=1, max_length=64)
    tier: str
    scenario: str
    anchor_kind: str
    anchor_value: str = Field(min_length=1, max_length=200)


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


# --- saved-estimate library ---------------------------------------------------


class SaveEstimateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    requirements: dict
    rigor_pct: int = Field(default=10, ge=0, le=100)


def _computed_matrix(requirements, rigor_pct: int):
    """Recompute an estimate server-side; returns (matrix, error_response)."""

    try:
        rows, _, _ = _snapshot_rows(requirements.currency)
    except Exception as error:  # noqa: BLE001 - migration may be pending
        log.info("estimator price snapshot unavailable", exc_info=error)
        rows = []
    if not rows:
        return None, JSONResponse(
            status_code=503,
            content=payload(
                "pricing_snapshot_missing",
                "An estimate cannot be saved without a stored price snapshot.",
                _SNAPSHOT_HINT,
            ),
        )
    book = estimator.build_price_book(rows, estimator.load_rate_card())
    return estimator.compute_matrix(requirements, rigor_pct=rigor_pct, price_book=book), None


@router.post("/estimates/record", dependencies=[Depends(deps.require_operator)])
def save_estimate(body: SaveEstimateRequest, request: Request):
    """Persist a confirmed estimate — always recomputed server-side.

    Client-sent totals are never stored: the saved results are the engine's
    own output for the saved requirements, so the library can never contain a
    number the engine would not reproduce.
    """
    import json as jsonlib
    import uuid

    actor = deps.require_verified_user(request)
    try:
        requirements = estimator.validate_requirements(body.requirements)
    except ValueError as error:
        return JSONResponse(
            status_code=422, content=payload("invalid_requirements", str(error))
        )
    matrix, error_response = _computed_matrix(requirements, body.rigor_pct)
    if error_response is not None:
        return error_response
    workspace_id, environment = deps.control_plane_scope()
    record = {
        "workspace_id": workspace_id,
        "environment": environment,
        "estimate_id": uuid.uuid4().hex,
        "created_by": actor.actor_id,
        "title": body.title.strip(),
        "pattern": requirements.pattern,
        "monthly_requests": requirements.monthly_requests,
        "corpus_gb": requirements.corpus_gb,
        "requirements_json": jsonlib.dumps(
            matrix["requirements"], sort_keys=True, separators=(",", ":")
        ),
        "requirements_hash": matrix["requirements_hash"],
        "engine_version": matrix["engine_version"],
        "rate_card_version": matrix["rate_card_version"],
        "snapshot_date": matrix["snapshot_date"],
        "rigor_pct": matrix["rigor_pct"],
        "results_json": jsonlib.dumps(matrix, default=str),
    }
    deps.get_user_control_plane_repository(request).record_estimate(record)
    return {
        "estimate_id": record["estimate_id"],
        "requirements_hash": record["requirements_hash"],
        "snapshot_date": record["snapshot_date"],
    }


@router.get("/estimates")
def list_estimates(request: Request, limit: int = 100) -> dict:
    from datetime import UTC, datetime

    # Never cached: a just-saved estimate must appear immediately.
    rows = deps.get_user_control_plane_repository(request).list_estimates(
        limit=max(1, min(limit, 500))
    )
    return envelope(rows, datetime.now(UTC), False)


@router.get("/estimates/similar")
def similar_estimates(
    request: Request,
    pattern: str,
    monthly_requests: int,
    requirements_hash: str = "",
):
    """Exact + same-order-of-magnitude matches for the review screen.

    Deterministic, structured matching only — never semantic: two
    similar-sounding descriptions with different numbers must never share an
    estimate.
    """
    if pattern not in estimator.load_patterns()["patterns"]:
        return JSONResponse(
            status_code=422,
            content=payload("invalid_requirements", f"Unknown pattern '{pattern}'."),
        )
    lo, hi = estimator.similar_bracket_bounds(max(1, monthly_requests))
    rows = deps.get_user_control_plane_repository(request).find_similar_estimates(
        pattern=pattern,
        lo=lo,
        hi=hi,
        requirements_hash=requirements_hash.strip().lower(),
    )
    exact = [
        row
        for row in rows
        if requirements_hash
        and row.get("requirements_hash") == requirements_hash.strip().lower()
    ]
    return {
        "exact_match": exact[0] if exact else None,
        "similar": [row for row in rows if row not in exact],
        "bracket": {"lo": lo, "hi": hi},
    }


# --- document upload (PDF / Markdown / plain text) ----------------------------

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post("/extract-document", dependencies=[Depends(deps.require_operator)])
async def extract_document(request: Request, file: UploadFile):
    """Read one uploaded document into the same two-stage extraction flow.

    The size cap is enforced while streaming (no global body limit exists in
    this app), and parsing happens in the wheel (`text_from_document`) so the
    upload path and free-text path share every downstream rule — bounded
    text, forced tool calls, validation, human review.
    """
    from backend import estimator_extraction

    del request  # authenticated via the global boundary + operator dependency
    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        received += len(chunk)
        if received > MAX_UPLOAD_BYTES:
            return JSONResponse(
                status_code=413,
                content=payload(
                    "document_too_large",
                    "Documents up to 10 MB are supported - export the key "
                    "section or paste the relevant text instead.",
                ),
            )
        chunks.append(chunk)
    from dbx_platform.estimator_extract import text_from_document

    filename = file.filename or "upload"
    data = b"".join(chunks)
    is_diagram = estimator_extraction.image_mime(filename) is not None
    try:
        if is_diagram:
            # Architecture diagrams go through the vision path (describe →
            # extract); the bound chat endpoint is already multimodal.
            requirements, warnings = estimator_extraction.extract_from_image(
                _extraction_model(), filename, data
            )
        else:
            text = text_from_document(filename, data)
            requirements, warnings = estimator_extraction.extract_requirements(
                _extraction_model(), text
            )
    except estimator_extraction.ExtractionError as error:
        return JSONResponse(
            status_code=422, content=payload("extraction_failed", str(error))
        )
    except Exception as error:  # noqa: BLE001 - endpoint is optional; degrade politely
        log.info("estimator document extraction unavailable", exc_info=error)
        return JSONResponse(
            status_code=503,
            content=payload(
                "extraction_unavailable",
                "The AI extraction step is currently unavailable; fill in the "
                "wizard manually instead.",
            ),
        )
    return {
        "requirements": requirements,
        "warnings": warnings,
        "filename": filename,
        "source": "diagram" if is_diagram else "document",
    }


# --- deploy-link (estimate → real cost anchor) --------------------------------


@router.post("/deployments/link", dependencies=[Depends(deps.require_operator)])
def link_deployment(body: LinkDeploymentRequest, request: Request):
    """Link a saved estimate to the cost anchor it was deployed under.

    The projected monthly total is read server-side from the estimate's own
    stored results for the chosen tier + scenario — never trusted from the
    client — so the drift check compares against exactly what the engine
    projected.
    """
    import uuid

    if body.tier not in estimator.TIERS or body.scenario not in estimator.SCENARIOS:
        return JSONResponse(
            status_code=422,
            content=payload("invalid_deployment", "Unknown tier or scenario."),
        )
    if body.anchor_kind not in estimator.ANCHOR_KINDS:
        return JSONResponse(
            status_code=422,
            content=payload(
                "invalid_deployment",
                f"anchor_kind must be one of {', '.join(estimator.ANCHOR_KINDS)}.",
            ),
        )
    actor = deps.require_verified_user(request)
    repo = deps.get_user_control_plane_repository(request)
    estimate = repo.get_estimate(body.estimate_id)
    if estimate is None:
        return JSONResponse(
            status_code=422,
            content=payload("invalid_deployment", "That saved estimate was not found."),
        )
    import json as jsonlib

    try:
        results = jsonlib.loads(estimate.get("results_json") or "{}")
        projected = float(
            results["tiers"][body.tier]["scenarios"][body.scenario]["totals_by_env"]["prod"]
        )
    except (KeyError, TypeError, ValueError):
        return JSONResponse(
            status_code=422,
            content=payload(
                "invalid_deployment",
                "The saved estimate has no production total for that tier and scenario.",
            ),
        )
    workspace_id, environment = deps.control_plane_scope()
    record = {
        "workspace_id": workspace_id,
        "environment": environment,
        "deployment_id": uuid.uuid4().hex,
        "estimate_id": body.estimate_id,
        "created_by": actor.actor_id,
        "tier": body.tier,
        "scenario": body.scenario,
        "anchor_kind": body.anchor_kind,
        "anchor_value": body.anchor_value.strip(),
        "monthly_projected_usd": round(projected, 2),
        "currency": str(estimate.get("currency") or "USD"),
        "active": True,
    }
    repo.link_deployment(record)
    return {
        "deployment_id": record["deployment_id"],
        "monthly_projected_usd": record["monthly_projected_usd"],
    }


@router.get("/deployments")
def list_deployments(request: Request) -> dict:
    from datetime import UTC, datetime

    rows = deps.get_user_control_plane_repository(request).list_deployments()
    return envelope(rows, datetime.now(UTC), False)
