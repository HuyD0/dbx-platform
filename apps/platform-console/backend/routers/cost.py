"""Cost & right-sizing checks — read-only system-table queries."""

from __future__ import annotations

from fastapi import APIRouter

from backend import cache, deps
from backend.models import envelope
from dbx_platform import cost

router = APIRouter(prefix="/api/cost")


@router.get("/usage")
def usage(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = cache.cached(
        f"cost/usage/{days}",
        lambda: cost.usage_report(deps.get_ws(), deps.warehouse_id(), days),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/top-jobs")
def top_jobs(days: int = 30, limit: int = 20, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    limit = max(1, min(100, limit))
    data, as_of, hit = cache.cached(
        f"cost/top-jobs/{days}/{limit}",
        lambda: cost.top_jobs(deps.get_ws(), deps.warehouse_id(), days, limit),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/cluster-utilization")
def cluster_utilization(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)

    def load() -> list[dict]:
        s = deps.get_settings()
        rows = cost.cluster_utilization(deps.get_ws(), deps.warehouse_id(), days)
        return cost.classify_cluster_utilization(
            rows, s.util_cpu_threshold_pct, s.util_mem_threshold_pct)

    data, as_of, hit = cache.cached(f"cost/cluster-utilization/{days}", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/warehouse-utilization")
def warehouse_utilization(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)

    def load() -> list[dict]:
        s = deps.get_settings()
        rows = cost.warehouse_utilization(deps.get_ws(), deps.warehouse_id(), days)
        return cost.classify_warehouse_utilization(
            rows, s.warehouse_min_queries, s.warehouse_queue_warn_seconds)

    data, as_of, hit = cache.cached(f"cost/warehouse-utilization/{days}", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/failed-run-waste")
def failed_run_waste(days: int = 30, limit: int = 20, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    limit = max(1, min(100, limit))
    data, as_of, hit = cache.cached(
        f"cost/failed-run-waste/{days}/{limit}",
        lambda: cost.failed_run_waste(deps.get_ws(), deps.warehouse_id(), days, limit),
        refresh,
    )
    return envelope(data, as_of, hit)
