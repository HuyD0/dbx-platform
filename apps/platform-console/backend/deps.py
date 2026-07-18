"""Lazy singletons and small config helpers.

The workspace client and settings are created on first use, never at import
time — tests/test_app.py enforces this statically, and it keeps app startup
independent of workspace availability.
"""

from __future__ import annotations

import os
import time
from functools import lru_cache

from dbx_platform.client import get_client
from dbx_platform.config import Settings


@lru_cache(maxsize=1)
def get_ws():
    return get_client(None)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()


def now_ms() -> int:
    return int(time.time() * 1000)


def warehouse_id() -> str:
    return os.environ.get("DBX_PLATFORM_WAREHOUSE_ID", "") or get_settings().warehouse_id


def findings_table() -> str:
    s = get_settings()
    return f"{s.dashboard_catalog}.{s.dashboard_schema}.platform_findings"


def digest_table() -> str:
    s = get_settings()
    return f"{s.dashboard_catalog}.{s.dashboard_schema}.platform_digest"


def actions_enabled() -> bool:
    """Deployment-level opt-in for the guarded remediation actions, reviewed
    in git via app.yaml. Off by default: the console stays report-only."""
    return os.environ.get("DBX_PLATFORM_CONSOLE_ACTIONS", "").strip().lower() == "true"


def agent_endpoint() -> str:
    """Serving endpoint name of the platform agent. agents.deploy names it
    agents_{catalog}-{schema}-{model}; override with DBX_PLATFORM_AGENT_ENDPOINT."""
    explicit = os.environ.get("DBX_PLATFORM_AGENT_ENDPOINT", "").strip()
    if explicit:
        return explicit
    s = get_settings()
    return f"agents_{s.dashboard_catalog}-{s.dashboard_schema}-platform_agent"


def clamp_days(days: int, lo: int = 7, hi: int = 90) -> int:
    return max(lo, min(hi, days))
