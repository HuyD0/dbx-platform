"""Security checks — token audit and inactive users."""

from __future__ import annotations

from fastapi import APIRouter

from backend import cache, deps
from backend.models import envelope
from dbx_platform import security

router = APIRouter(prefix="/api/security")


@router.get("/token-audit")
def token_audit(refresh: bool = False) -> dict:
    def load() -> list[dict]:
        s = deps.get_settings()
        return security.classify_tokens(
            security.fetch_tokens(deps.get_ws()), deps.now_ms(),
            s.token_max_age_days, s.token_expiry_warn_days)

    data, as_of, hit = cache.cached("security/token-audit", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/inactive-users")
def inactive_users(days: int | None = None, refresh: bool = False) -> dict:
    def load() -> list[dict]:
        w = deps.get_ws()
        s = deps.get_settings()
        window = deps.clamp_days(days or s.inactive_user_days, lo=7, hi=365)
        return security.find_inactive_users(
            security.fetch_workspace_users(w),
            security.fetch_user_activity(w, deps.warehouse_id(), window),
            window,
        )

    data, as_of, hit = cache.cached(f"security/inactive-users/{days}", load, refresh)
    return envelope(data, as_of, hit)
