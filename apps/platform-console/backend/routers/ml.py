"""AI/ML checks — serving, registry hygiene, GPU, vector search, spend."""

from __future__ import annotations

from fastapi import APIRouter

from backend import cache, deps
from backend.models import envelope
from dbx_platform import ml

router = APIRouter(prefix="/api/ml")


@router.get("/endpoint-audit")
def endpoint_audit(refresh: bool = False) -> dict:
    def load() -> list[dict]:
        s = deps.get_settings()
        return ml.classify_serving_endpoints(
            ml.fetch_serving_endpoints(deps.get_ws()), deps.now_ms(),
            s.serving_failed_grace_hours)

    data, as_of, hit = cache.cached("ml/endpoint-audit", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/stale-endpoints")
def stale_endpoints(refresh: bool = False) -> dict:
    def load() -> list[dict]:
        w = deps.get_ws()
        s = deps.get_settings()
        usage = ml.endpoint_token_usage(w, deps.warehouse_id(), s.serving_stale_days)
        return ml.find_stale_endpoints(
            ml.fetch_serving_endpoints(w), usage, deps.now_ms(), s.serving_stale_days)

    data, as_of, hit = cache.cached("ml/stale-endpoints", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/model-hygiene")
def model_hygiene(
    catalog: str | None = None, schema: str | None = None, refresh: bool = False
) -> dict:
    def load() -> dict:
        w = deps.get_ws()
        s = deps.get_settings()
        models, truncated = ml.fetch_registered_models(w, catalog, schema, s.ml_max_models)
        served = ml.served_entity_names(ml.fetch_serving_endpoints(w))
        findings = ml.classify_models(
            models, served, deps.now_ms(), s.model_stale_days, s.model_unaliased_days)
        return {"findings": findings, "truncated": truncated}

    data, as_of, hit = cache.cached(f"ml/model-hygiene/{catalog}/{schema}", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/gpu-audit")
def gpu_audit(refresh: bool = False) -> dict:
    def load() -> list[dict]:
        w = deps.get_ws()
        s = deps.get_settings()
        return ml.classify_gpu_clusters(
            ml.fetch_clusters_with_node_types(w), ml.fetch_gpu_node_types(w),
            deps.now_ms(), s.gpu_max_uptime_hours)

    data, as_of, hit = cache.cached("ml/gpu-audit", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/vector-search-audit")
def vector_search_audit(refresh: bool = False) -> dict:
    def load() -> list[dict]:
        s = deps.get_settings()
        return ml.find_vector_search_findings(
            ml.fetch_vector_search(deps.get_ws()), deps.now_ms(),
            s.vector_search_grace_hours)

    data, as_of, hit = cache.cached("ml/vector-search-audit", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/serving-cost")
def serving_cost(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = cache.cached(
        f"ml/serving-cost/{days}",
        lambda: ml.serving_cost(deps.get_ws(), deps.warehouse_id(), days),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/token-usage")
def token_usage(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = cache.cached(
        f"ml/token-usage/{days}",
        lambda: ml.endpoint_token_usage(deps.get_ws(), deps.warehouse_id(), days),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/gpu-spend")
def gpu_spend(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = cache.cached(
        f"ml/gpu-spend/{days}",
        lambda: ml.gpu_spend(deps.get_ws(), deps.warehouse_id(), days),
        refresh,
    )
    return envelope(data, as_of, hit)
