"""Governance checks — policy drift, tag compliance and recommendations."""

from __future__ import annotations

from importlib import resources

from fastapi import APIRouter

from backend import cache, deps
from backend.models import envelope
from dbx_platform import governance

router = APIRouter(prefix="/api/governance")


def packaged_policies_dir() -> str:
    return str(resources.files("dbx_platform") / "policies")


@router.get("/policy-drift")
def policy_drift(refresh: bool = False) -> dict:
    def load() -> dict:
        plan = governance.diff_policies(
            governance.load_local_policies(packaged_policies_dir()),
            governance.fetch_remote_policies(deps.get_ws()),
        )
        # Names only — policy definitions stay server-side until an apply plan.
        return {
            bucket: [{"name": p["name"]} for p in plan[bucket]]
            for bucket in ("create", "update", "unchanged", "unmanaged")
        }

    data, as_of, hit = cache.cached("governance/policy-drift", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/tag-compliance")
def tag_compliance(refresh: bool = False) -> dict:
    def load() -> list[dict]:
        s = deps.get_settings()
        return governance.find_missing_tags(
            governance.fetch_taggable_resources(deps.get_ws()), s.required_tag_list())

    data, as_of, hit = cache.cached("governance/tag-compliance", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/tag-recommendations")
def tag_recommendations(refresh: bool = False) -> dict:
    def load() -> list[dict]:
        s = deps.get_settings()
        return governance.recommend_tags(
            governance.fetch_taggable_resources(deps.get_ws()),
            s.required_tag_list(),
            min_ratio=s.tag_suggestion_min_ratio_pct / 100,
            owner_keys=tuple(s.tag_owner_key_list()),
        )

    data, as_of, hit = cache.cached("governance/tag-recommendations", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/untagged-spend")
def untagged_spend(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = cache.cached(
        f"governance/untagged-spend/{days}",
        lambda: governance.untagged_spend(deps.get_ws(), deps.warehouse_id(), days),
        refresh,
    )
    return envelope(data, as_of, hit)
