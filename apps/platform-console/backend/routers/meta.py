"""Health, safe config subset, and links to the bundle-deployed dashboards."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Request

import dbx_platform
from backend import cache, deps
from backend.models import envelope

router = APIRouter()

DASHBOARD_MARKER = "[dbx-platform]"

# Written by the deploy workflow next to app.yaml; absent in local dev.
_BUILD_INFO = Path(__file__).resolve().parents[2] / "build_info.json"


def _build_info() -> dict | None:
    try:
        info = json.loads(_BUILD_INFO.read_text())
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


# Cached on success only, so a workspace blip at startup cannot pin health to
# a null scope for the process lifetime.
_workspace_id_cache: str | None = None


def _workspace_id() -> str | None:
    global _workspace_id_cache
    if _workspace_id_cache is None:
        try:
            _workspace_id_cache, _ = deps.control_plane_scope()
        except Exception:  # noqa: BLE001 — health must answer even without the workspace
            return None
    return _workspace_id_cache


@router.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": dbx_platform.__version__,
        "build": _build_info(),
        "actions_enabled": deps.actions_enabled(),
        "environment": os.environ.get("DBX_PLATFORM_ENVIRONMENT", "dev"),
        "workspace_id": _workspace_id(),
    }


@router.get("/api/config")
def config() -> dict:
    s = deps.get_settings()
    return {
        "warehouse_configured": bool(deps.warehouse_id()),
        "actions_enabled": deps.actions_enabled(),
        "findings_table": deps.findings_table(),
        "digest_model": s.digest_model,
        # Retain the response key for UI/API compatibility; the value is now
        # the App-hosted LangGraph agent's bound foundation-model endpoint.
        "agent_endpoint": deps.chat_endpoint(),
        "lookback_days": s.lookback_days,
        "required_tags": s.required_tag_list(),
        "thresholds": {
            "stale_cluster_days": s.stale_cluster_days,
            "max_uptime_hours": s.max_uptime_hours,
            "token_max_age_days": s.token_max_age_days,
            "inactive_user_days": s.inactive_user_days,
            "util_cpu_threshold_pct": s.util_cpu_threshold_pct,
            "util_mem_threshold_pct": s.util_mem_threshold_pct,
            "gpu_max_uptime_hours": s.gpu_max_uptime_hours,
        },
    }


@router.get("/api/workspaces")
def workspaces(request: Request) -> dict:
    actor = request.state.actor
    workspace_id = _workspace_id()
    environment = os.environ.get("DBX_PLATFORM_ENVIRONMENT", "dev")
    is_admin = actor.has_role("operator") or actor.has_role("approver")
    capabilities = [
        {
            "id": "personal-evidence",
            "label": "View workspace evidence",
            "description": (
                "See health, cost, governance, AI, and operational evidence with "
                "viewer-safe redaction."
            ),
            "enabled": True,
        },
        {
            "id": "propose-actions",
            "label": "Draft governed changes",
            "description": (
                "Create immutable proposals for workspace changes; execution still "
                "requires approval."
            ),
            "enabled": actor.has_role("proposer"),
        },
        {
            "id": "approve-actions",
            "label": "Approve exact plans",
            "description": (
                "Approve one current, immutable plan before a dedicated executor "
                "can run it."
            ),
            "enabled": actor.has_role("approver"),
        },
        {
            "id": "admin-console",
            "label": "Platform admin view",
            "description": (
                "Review all workspace evidence and pending governed actions for "
                "this control plane."
            ),
            "enabled": is_admin,
        },
    ]
    workspace = {
        "workspace_id": workspace_id,
        "name": (
            os.environ.get("DBX_PLATFORM_WORKSPACE_NAME")
            or workspace_id
            or "Current workspace"
        ),
        "environment": environment,
        "relationship": "platform_admin" if is_admin else "workspace_user",
        "roles": sorted(actor.roles),
        "capabilities": capabilities,
        "management_mode": "governed_approval" if is_admin else "viewer_safe",
    }
    return {
        "actor": {
            "actor_id": actor.actor_id,
            "email": actor.email,
            "roles": sorted(actor.roles),
            "view": "platform_admin" if is_admin else "workspace_user",
        },
        "workspaces": [workspace],
        "source_status": {
            "status": "partial",
            "source": "databricks_app_obo",
            "notes": (
                "Uses the Databricks Apps forwarded user token as OBO passthrough "
                "for this workspace. Account-wide workspace discovery can be "
                "added behind the same entitlement model."
            ),
        },
    }


@router.get("/api/dashboards")
def dashboards(refresh: bool = False) -> dict:
    def load() -> list[dict]:
        w = deps.get_ws()
        host = (w.config.host or "").rstrip("/")
        out = []
        try:
            for d in w.lakeview.list():
                name = d.display_name or ""
                if DASHBOARD_MARKER in name and d.dashboard_id:
                    out.append({
                        "name": name,
                        "url": f"{host}/sql/dashboardsv3/{d.dashboard_id}",
                        # Iframe-embeddable only after a workspace admin approves
                        # the app's domain for embedding (docs/runbook.md).
                        "embed_url": f"{host}/embed/dashboardsv3/{d.dashboard_id}",
                    })
        except Exception:  # noqa: BLE001 — links are garnish, never break the page
            return []
        return sorted(out, key=lambda d: d["name"])

    data, as_of, hit = cache.cached("dashboards", load, refresh)
    return envelope(data, as_of, hit)
