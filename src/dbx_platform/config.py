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
    model_stale_days: int = 90
    model_unaliased_days: int = 30
    ml_max_models: int = 500
    gpu_max_uptime_hours: int = 8
    vector_search_grace_hours: int = 24
    # Right-sizing
    util_cpu_threshold_pct: int = 30
    util_mem_threshold_pct: int = 50
    allpurpose_fixed_workers_max: int = 10
    warehouse_min_queries: int = 50
    warehouse_queue_warn_seconds: int = 5
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
