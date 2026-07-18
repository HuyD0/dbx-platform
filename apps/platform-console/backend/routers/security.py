"""Security and risk views backed by read-only canonical evidence."""

from __future__ import annotations

from fastapi import APIRouter

from backend import cache, deps
from backend.models import envelope
from dbx_platform import security

router = APIRouter(prefix="/api/security")


def _canonical_security_findings(*terms: str) -> list[dict]:
    rows = deps.get_control_plane_repository().list_findings(
        pillar="SECURITY",
        limit=1000,
    )
    if not terms:
        return rows
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
                )
            ).lower()
            for term in lowered
        )
    ]


@router.get("/token-audit")
def token_audit(refresh: bool = False) -> dict:
    """Read token findings collected by the privileged scheduled detector.

    Listing all workspace PATs requires workspace-admin privileges. The App
    service principal deliberately never receives those privileges.
    """
    def load() -> list[dict]:
        return _canonical_security_findings("token", "pat", "credential")

    data, as_of, hit = cache.cached("security/token-audit", load, refresh)
    response = envelope(data, as_of, hit)
    response["source_status"] = {
        "status": "partial",
        "source": "scheduled security audit",
        "notes": (
            "The read-only App never lists PATs directly. Rows appear after the "
            "privileged detector writes normalized findings."
        ),
    }
    return response


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


def _evidence_route(cache_key: str, terms: tuple[str, ...], refresh: bool) -> dict:
    data, as_of, hit = cache.cached(
        cache_key,
        lambda: _canonical_security_findings(*terms),
        refresh,
    )
    response = envelope(data, as_of, hit)
    response["source_status"] = {
        "status": "partial",
        "source": "platform_findings",
        "notes": (
            "This v1 view exposes normalized findings already collected for "
            "this signal. Absence of rows is not yet proof of full source coverage."
        ),
    }
    return response


@router.get("/privilege-drift")
def privilege_drift(refresh: bool = False) -> dict:
    return _evidence_route(
        "security/privilege-drift",
        ("privilege", "grant", "owner", "policy"),
        refresh,
    )


@router.get("/service-principals")
def service_principals(refresh: bool = False) -> dict:
    return _evidence_route(
        "security/service-principals",
        ("service principal", "orphan", "principal"),
        refresh,
    )


@router.get("/network-egress")
def network_egress(refresh: bool = False) -> dict:
    return _evidence_route(
        "security/network-egress",
        ("egress", "network", "public access"),
        refresh,
    )


@router.get("/audit-anomalies")
def audit_anomalies(refresh: bool = False) -> dict:
    return _evidence_route(
        "security/audit-anomalies",
        ("audit", "anomaly", "unusual"),
        refresh,
    )
