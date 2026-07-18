"""Housekeeping checks — fresh fetch + classify, same composition as the CLI."""

from __future__ import annotations

from fastapi import APIRouter

from backend import cache, deps
from backend.models import envelope
from dbx_platform import housekeeping

router = APIRouter(prefix="/api/housekeeping")


@router.get("/stale-clusters")
def stale_clusters(refresh: bool = False) -> dict:
    def load() -> list[dict]:
        s = deps.get_settings()
        return housekeeping.classify_clusters(
            housekeeping.fetch_clusters(deps.get_ws()), deps.now_ms(),
            s.stale_cluster_days, s.max_uptime_hours)

    data, as_of, hit = cache.cached("housekeeping/stale-clusters", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/orphaned-jobs")
def orphaned_jobs(refresh: bool = False) -> dict:
    def load() -> list[dict]:
        w = deps.get_ws()
        return housekeeping.find_orphaned_jobs(
            housekeeping.fetch_jobs(w), housekeeping.fetch_active_principals(w))

    data, as_of, hit = cache.cached("housekeeping/orphaned-jobs", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/jobs-on-all-purpose")
def jobs_on_all_purpose(refresh: bool = False) -> dict:
    def load() -> list[dict]:
        s = deps.get_settings()
        return housekeeping.find_jobs_on_all_purpose(
            housekeeping.fetch_jobs_with_clusters(deps.get_ws()),
            s.allpurpose_fixed_workers_max)

    data, as_of, hit = cache.cached("housekeeping/jobs-on-all-purpose", load, refresh)
    return envelope(data, as_of, hit)
