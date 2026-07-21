"""Health, safe config subset, and links to the bundle-deployed dashboards."""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

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


@router.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": dbx_platform.__version__,
        "build": _build_info(),
        "actions_enabled": deps.actions_enabled(),
        "environment": os.environ.get("DBX_PLATFORM_ENVIRONMENT", "dev"),
    }


@router.get("/api/config")
def config() -> dict:
    s = deps.get_settings()
    return {
        "warehouse_configured": bool(deps.warehouse_id()),
        "actions_enabled": deps.actions_enabled(),
        "findings_table": deps.findings_table(),
        "digest_model": s.digest_model,
        "chat_model_endpoint": deps.chat_model_endpoint(),
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
