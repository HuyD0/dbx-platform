"""Read-only LangChain tools over the dbx_platform checks.

The tool set is read-only *by construction*: no apply/mutate function is
wrapped here, so the served agent can diagnose and recommend but has nothing
it could call to change the workspace. Auth is ambient (the serving
endpoint's service principal / local unified auth); the warehouse comes from
DBX_PLATFORM_WAREHOUSE_ID.
"""

from __future__ import annotations

import time
from functools import lru_cache
from importlib import resources

from langchain_core.tools import tool

from dbx_platform import cost, governance, housekeeping, ml
from dbx_platform.client import get_client
from dbx_platform.config import Settings

from .formatting import rows_to_text


@lru_cache(maxsize=1)
def _client():
    return get_client(None)


def _settings() -> Settings:
    return Settings.from_env()


def _now_ms() -> int:
    return int(time.time() * 1000)


@tool
def get_cost_report(days: int = 30) -> str:
    """DBU and list-price cost by SKU and workspace over the last N days."""
    s = _settings()
    return rows_to_text(cost.usage_report(_client(), s.warehouse_id, days))


@tool
def get_top_jobs(days: int = 30, limit: int = 20) -> str:
    """The most expensive jobs by list cost over the last N days."""
    s = _settings()
    return rows_to_text(cost.top_jobs(_client(), s.warehouse_id, days, limit))


@tool
def get_cluster_utilization(days: int = 30) -> str:
    """Under-utilized clusters (low CPU/memory for their size), ranked by cost."""
    s = _settings()
    rows = cost.cluster_utilization(_client(), s.warehouse_id, days)
    return rows_to_text(cost.classify_cluster_utilization(
        rows, s.util_cpu_threshold_pct, s.util_mem_threshold_pct))


@tool
def get_failed_run_waste(days: int = 30) -> str:
    """List cost burned on failed or timed-out job runs over the last N days."""
    s = _settings()
    return rows_to_text(cost.failed_run_waste(_client(), s.warehouse_id, days, 20))


@tool
def get_serving_findings() -> str:
    """Model serving endpoint audit: failed endpoints, missing scale-to-zero,
    missing inference tables, missing AI Gateway limits."""
    s = _settings()
    return rows_to_text(ml.classify_serving_endpoints(
        ml.fetch_serving_endpoints(_client()), _now_ms(), s.serving_failed_grace_hours))


@tool
def get_model_hygiene(catalog: str | None = None, schema: str | None = None) -> str:
    """UC registered-model hygiene: stale, ownerless, unaliased, never served."""
    s = _settings()
    w = _client()
    models, truncated = ml.fetch_registered_models(w, catalog, schema, s.ml_max_models)
    served = ml.served_entity_names(ml.fetch_serving_endpoints(w))
    text = rows_to_text(ml.classify_models(
        models, served, _now_ms(), s.model_stale_days, s.model_unaliased_days))
    if truncated:
        text += f"\n(listing truncated at {s.ml_max_models} models)"
    return text


@tool
def get_gpu_findings() -> str:
    """Interactive GPU clusters running without autotermination or past the
    GPU uptime threshold."""
    s = _settings()
    w = _client()
    return rows_to_text(ml.classify_gpu_clusters(
        ml.fetch_clusters_with_node_types(w), ml.fetch_gpu_node_types(w),
        _now_ms(), s.gpu_max_uptime_hours))


@tool
def get_policy_drift() -> str:
    """Cluster-policy drift between git (source of truth) and the workspace.
    Dry-run diff only."""
    packaged = resources.files("dbx_platform") / "policies"
    plan = governance.diff_policies(
        governance.load_local_policies(str(packaged)),
        governance.fetch_remote_policies(_client()),
    )
    rows = (
        [{"action": "create", "name": p["name"]} for p in plan["create"]]
        + [{"action": "update", "name": p["name"]} for p in plan["update"]]
    )
    return rows_to_text(rows)


@tool
def get_stale_clusters() -> str:
    """Stale terminated clusters and long-running interactive clusters."""
    s = _settings()
    return rows_to_text(housekeeping.classify_clusters(
        housekeeping.fetch_clusters(_client()), _now_ms(),
        s.stale_cluster_days, s.max_uptime_hours))


ALL_TOOLS = [
    get_cost_report,
    get_top_jobs,
    get_cluster_utilization,
    get_failed_run_waste,
    get_serving_findings,
    get_model_hygiene,
    get_gpu_findings,
    get_policy_drift,
    get_stale_clusters,
]
