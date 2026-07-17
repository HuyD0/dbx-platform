"""Runtime settings for dbx-platform.

Precedence: CLI flag > environment variable > default. Scheduled jobs pass
values explicitly as task parameters in resources/*.yml so behavior is
reviewable in git; env vars exist for interactive convenience.

All environment variables are prefixed with DBX_PLATFORM_.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

ENV_PREFIX = "DBX_PLATFORM_"


@dataclass
class Settings:
    # Housekeeping
    stale_cluster_days: int = 30
    max_uptime_hours: int = 24
    # Security
    token_max_age_days: int = 90
    token_expiry_warn_days: int = 14
    inactive_user_days: int = 90
    # Governance
    required_tags: str = "team,project"
    # ML / AI workloads
    serving_stale_days: int = 30
    serving_failed_grace_hours: int = 24
    # System-table queries
    warehouse_id: str = ""
    lookback_days: int = 30
    # Dashboards (see dashboards.py): where helper functions/tables live
    dashboard_catalog: str = "main"
    dashboard_schema: str = "dbx_platform"
    # Azure secrets helper
    service_credential: str = ""
    # Wheel distribution
    wheel_volume_path: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        kwargs = {}
        for f in fields(cls):
            raw = os.environ.get(ENV_PREFIX + f.name.upper())
            if raw is None:
                continue
            kwargs[f.name] = int(raw) if f.type in ("int", int) else raw
        return cls(**kwargs)

    def required_tag_list(self) -> list[str]:
        return [t.strip() for t in self.required_tags.split(",") if t.strip()]
