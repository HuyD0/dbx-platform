"""Read-only LangChain tools over the dbx_platform checks.

The tool set is read-only *by construction*: no apply/mutate function is
wrapped here, so the served agent can diagnose and recommend but has nothing
it could call to change the workspace. The propose_* tools are dry-runs that
end their output with a machine-readable marker line; the Platform Console
parses those markers into confirm-gated cards, and a human performs the
actual apply there. Auth is ambient (the serving endpoint's service
principal / local unified auth); the warehouse comes from
DBX_PLATFORM_WAREHOUSE_ID.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from functools import lru_cache
from importlib import resources

from langchain_core.tools import tool

from dbx_platform import cost, governance, housekeeping, llm_cost, ml, security
from dbx_platform.client import get_client
from dbx_platform.config import Settings

try:
    from .formatting import rows_to_text
except ImportError:  # pragma: no cover - MLflow standalone code layout
    from formatting import rows_to_text


def _ambient_client():
    return get_client(None)


_client_factory: Callable[[], object] = _ambient_client
_settings_factory: Callable[[], Settings] = Settings.from_env


def configure_runtime(
    *,
    client_factory: Callable[[], object] | None = None,
    settings_factory: Callable[[], Settings] | None = None,
) -> None:
    """Inject the hosting runtime's authenticated client and settings.

    The FastAPI App uses its resource-bound service principal. A separately
    served model can retain ambient Databricks authentication.
    """
    global _client_factory, _settings_factory
    if client_factory is not None:
        _client_factory = client_factory
    if settings_factory is not None:
        _settings_factory = settings_factory
    _client.cache_clear()


@lru_cache(maxsize=1)
def _client():
    return _client_factory()


def _settings() -> Settings:
    return _settings_factory()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _render(rows: list[dict], tool_name: str, source: str) -> str:
    return rows_to_text(rows, tool_name=tool_name, source=source)


@tool
def get_cost_report(days: int = 30) -> str:
    """DBU and list-price cost by SKU and workspace over the last N days."""
    s = _settings()
    return _render(
        cost.usage_report(_client(), s.warehouse_id, days),
        "get_cost_report",
        "system.billing.usage + system.billing.list_prices",
    )


@tool
def get_top_jobs(days: int = 30, limit: int = 20) -> str:
    """The most expensive jobs by list cost over the last N days."""
    s = _settings()
    return _render(
        cost.top_jobs(_client(), s.warehouse_id, days, limit),
        "get_top_jobs",
        "system.billing.usage + system.lakeflow jobs",
    )


@tool
def get_cluster_utilization(days: int = 30) -> str:
    """Under-utilized clusters (low CPU/memory for their size), ranked by cost."""
    s = _settings()
    rows = cost.cluster_utilization(_client(), s.warehouse_id, days)
    return _render(
        cost.classify_cluster_utilization(
            rows,
            s.util_cpu_threshold_pct,
            s.util_mem_threshold_pct,
        ),
        "get_cluster_utilization",
        "system.compute.node_timeline + system.billing.usage",
    )


@tool
def get_failed_run_waste(days: int = 30) -> str:
    """List cost burned on failed or timed-out job runs over the last N days."""
    s = _settings()
    return _render(
        cost.failed_run_waste(_client(), s.warehouse_id, days, 20),
        "get_failed_run_waste",
        "system.lakeflow.job_run_timeline + system.billing.usage",
    )


@tool
def get_llm_cost_and_efficiency(days: int = 30) -> str:
    """LLM list cost, tokens, requests and efficiency recommendations.

    Financial bases are reported separately and requesters are not returned.
    """

    s = _settings()
    w = _client()
    workspace_id = str(w.get_workspace_id())
    try:
        cost_rows = llm_cost.databricks_cost(
            w,
            s.warehouse_id,
            days,
            gateway_enriched=True,
            workspace_id=workspace_id,
        )
    except Exception:  # noqa: BLE001 - compatibility with pre-Gateway schemas
        cost_rows = llm_cost.databricks_cost(
            w,
            s.warehouse_id,
            days,
            gateway_enriched=False,
            workspace_id=workspace_id,
        )
    try:
        usage_rows = llm_cost.gateway_usage(
            w,
            s.warehouse_id,
            min(days, 90),
            workspace_id=workspace_id,
        )
    except Exception:  # noqa: BLE001 - compatibility with serving usage
        usage_rows = llm_cost.endpoint_usage(
            w,
            s.warehouse_id,
            min(days, 90),
            workspace_id=workspace_id,
        )
    costs = llm_cost.normalize_cost_rows(
        cost_rows,
        "system.billing.usage",
        "DATABRICKS_LIST",
        environment=s.environment,
        workspace_id=workspace_id,
    )
    usage = llm_cost.normalize_usage_rows(
        usage_rows,
        "model usage",
        environment=s.environment,
        workspace_id=workspace_id,
    )
    summary = llm_cost.summarize(costs, usage, days)
    efficiency_report = llm_cost.efficiency(costs, usage)
    rows = [
        *summary["totals"],
        {
            "requests": summary["requests"],
            "input_tokens": summary["input_tokens"],
            "output_tokens": summary["output_tokens"],
            "cached_tokens": summary["cached_tokens"],
            "reasoning_tokens": summary["reasoning_tokens"],
            "cost_per_request": summary["cost_per_request"],
            "cost_per_million_tokens": summary["cost_per_million_tokens"],
        },
        *efficiency_report["recommendations"],
    ]
    return _render(
        rows,
        "get_llm_cost_and_efficiency",
        "system.billing.usage + system.ai_gateway + system.serving.endpoint_usage",
    )


@tool
def get_serving_findings() -> str:
    """Model serving endpoint audit: failed endpoints, missing scale-to-zero,
    missing inference tables, missing AI Gateway limits."""
    s = _settings()
    return _render(
        ml.classify_serving_endpoints(
            ml.fetch_serving_endpoints(_client()),
            _now_ms(),
            s.serving_failed_grace_hours,
        ),
        "get_serving_findings",
        "Databricks serving endpoints API",
    )


@tool
def get_model_hygiene(
    catalog: str | None = None,
    schema_name: str | None = None,
) -> str:
    """UC registered-model hygiene: stale, ownerless, unaliased, never served."""
    s = _settings()
    w = _client()
    models, truncated = ml.fetch_registered_models(
        w,
        catalog,
        schema_name,
        s.ml_max_models,
    )
    served = ml.served_entity_names(ml.fetch_serving_endpoints(w))
    text = _render(
        ml.classify_models(
            models,
            served,
            _now_ms(),
            s.model_stale_days,
            s.model_unaliased_days,
        ),
        "get_model_hygiene",
        "Unity Catalog registered models API + serving endpoints API",
    )
    if truncated:
        text += f"\n(listing truncated at {s.ml_max_models} models)"
    return text


@tool
def get_gpu_findings() -> str:
    """Interactive GPU clusters running without autotermination or past the
    GPU uptime threshold."""
    s = _settings()
    w = _client()
    return _render(
        ml.classify_gpu_clusters(
            ml.fetch_clusters_with_node_types(w),
            ml.fetch_gpu_node_types(w),
            _now_ms(),
            s.gpu_max_uptime_hours,
        ),
        "get_gpu_findings",
        "Databricks clusters API + node types API",
    )


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
    return _render(
        rows,
        "get_policy_drift",
        "packaged policies + Databricks cluster policies API",
    )


@tool
def get_stale_clusters() -> str:
    """Stale terminated clusters and long-running interactive clusters."""
    s = _settings()
    return _render(
        housekeeping.classify_clusters(
            housekeeping.fetch_clusters(_client()),
            _now_ms(),
            s.stale_cluster_days,
            s.max_uptime_hours,
        ),
        "get_stale_clusters",
        "Databricks clusters API",
    )


@tool
def get_warehouse_utilization(days: int = 30) -> str:
    """SQL warehouses mis-sized in either direction: idle spend, few queries,
    or sustained queueing at capacity."""
    s = _settings()
    rows = cost.warehouse_utilization(_client(), s.warehouse_id, days)
    return _render(
        cost.classify_warehouse_utilization(
            rows,
            s.warehouse_min_queries,
            s.warehouse_queue_warn_seconds,
        ),
        "get_warehouse_utilization",
        "system.query.history + system.billing.usage",
    )


@tool
def get_token_findings() -> str:
    """PAT token audit: never-expires, over the age threshold, expiring soon.
    Requires workspace-admin visibility."""
    s = _settings()
    return _render(
        security.classify_tokens(
            security.fetch_tokens(_client()),
            _now_ms(),
            s.token_max_age_days,
            s.token_expiry_warn_days,
        ),
        "get_token_findings",
        "Databricks token management API",
    )


@tool
def get_orphaned_jobs() -> str:
    """Jobs whose creator no longer exists or is inactive."""
    w = _client()
    return _render(
        housekeeping.find_orphaned_jobs(
            housekeeping.fetch_jobs(w),
            housekeeping.fetch_active_principals(w),
        ),
        "get_orphaned_jobs",
        "Databricks Jobs API + SCIM users/service principals",
    )


@tool
def get_tag_recommendations() -> str:
    """Suggested fixes for resources missing required tags (mistyped keys,
    values inferred from names, creators for owner-type keys)."""
    s = _settings()
    return _render(
        governance.recommend_tags(
            governance.fetch_taggable_resources(_client()),
            s.required_tag_list(),
            min_ratio=s.tag_suggestion_min_ratio_pct / 100,
            owner_keys=tuple(s.tag_owner_key_list()),
        ),
        "get_tag_recommendations",
        "Databricks workspace resource APIs",
    )


@tool
def list_platform_jobs() -> str:
    """The [dbx-platform] report jobs deployed by the bundle, with job IDs."""
    rows = [
        {"job_id": j.job_id, "name": j.settings.name if j.settings else ""}
        for j in _client().jobs.list()
        if "dbx-platform" in ((j.settings.name if j.settings else "") or "")
    ]
    return _render(
        sorted(rows, key=lambda r: r["name"]),
        "list_platform_jobs",
        "Databricks Jobs API",
    )


@tool
def get_recent_runs(job_id: int) -> str:
    """The last five runs of a job: state, result and start time."""
    rows = []
    for r in _client().jobs.list_runs(job_id=job_id, limit=5):
        state = r.state
        rows.append({
            "run_id": r.run_id,
            "state": state.life_cycle_state.value if state and state.life_cycle_state else "",
            "result": state.result_state.value if state and state.result_state else "",
            "started_ms": r.start_time or 0,
        })
    return _render(
        rows,
        "get_recent_runs",
        f"Databricks Jobs API job_id={job_id}",
    )


# --- proposals (read-only dry-runs the console turns into confirm cards) -----

_PROPOSAL_PLANNERS = {
    "stale-clusters": lambda w, s: housekeeping.classify_clusters(
        housekeeping.fetch_clusters(w), _now_ms(),
        s.stale_cluster_days, s.max_uptime_hours),
    "orphaned-jobs": lambda w, s: housekeeping.find_orphaned_jobs(
        housekeeping.fetch_jobs(w), housekeeping.fetch_active_principals(w)),
    "token-revoke": lambda w, s: [
        f for f in security.classify_tokens(
            security.fetch_tokens(w), _now_ms(),
            s.token_max_age_days, s.token_expiry_warn_days)
        if f["over_age"]
    ],
    "policy-sync": lambda w, s: _policy_drift_rows(w),
}


def _policy_drift_rows(w) -> list[dict]:
    packaged = resources.files("dbx_platform") / "policies"
    plan = governance.diff_policies(
        governance.load_local_policies(str(packaged)),
        governance.fetch_remote_policies(w),
    )
    return (
        [{"action": "create", "name": p["name"]} for p in plan["create"]]
        + [{"action": "update", "name": p["name"]} for p in plan["update"]]
    )


@tool
def propose_remediation(action: str) -> str:
    """Dry-run one of the console's guarded remediation actions and emit a
    proposal the user can confirm in the Platform Console. Valid actions:
    stale-clusters, orphaned-jobs, token-revoke, policy-sync. Changes nothing.
    Copy the ACTION_PROPOSAL line verbatim into your final answer."""
    planner = _PROPOSAL_PLANNERS.get(action)
    if planner is None:
        return f"Unknown action '{action}'. Valid: {', '.join(sorted(_PROPOSAL_PLANNERS))}."
    items = planner(_client(), _settings())
    if not items:
        return f"Dry-run of {action}: nothing to do — no proposal needed."
    marker = json.dumps({"action": action, "count": len(items)})
    return (
        f"Dry-run of {action} found {len(items)} item(s):\n"
        + _render(
            items,
            "propose_remediation",
            f"read-only planner:{action}",
        )
        + f"\nACTION_PROPOSAL:{marker}"
    )


@tool
def propose_job_run(job_name: str) -> str:
    """Propose kicking off one of the [dbx-platform] report jobs by (partial)
    name. Changes nothing — the user confirms the run in the Platform Console.
    Copy the JOB_PROPOSAL line verbatim into your final answer."""
    matches = [
        {"job_id": j.job_id, "name": (j.settings.name if j.settings else "") or ""}
        for j in _client().jobs.list()
        if "dbx-platform" in ((j.settings.name if j.settings else "") or "")
        and job_name.lower() in ((j.settings.name if j.settings else "") or "").lower()
    ]
    if not matches:
        return f"No [dbx-platform] job matches '{job_name}'. Use list_platform_jobs."
    if len(matches) > 1:
        return "Ambiguous — matches:\n" + _render(
            matches,
            "propose_job_run",
            "Databricks Jobs API",
        )
    marker = json.dumps({"job_id": matches[0]["job_id"], "name": matches[0]["name"]})
    return f"Ready to run {matches[0]['name']}.\nJOB_PROPOSAL:{marker}"


ALL_TOOLS = [
    get_cost_report,
    get_top_jobs,
    get_cluster_utilization,
    get_failed_run_waste,
    get_llm_cost_and_efficiency,
    get_warehouse_utilization,
    get_serving_findings,
    get_model_hygiene,
    get_gpu_findings,
    get_policy_drift,
    get_stale_clusters,
    get_token_findings,
    get_orphaned_jobs,
    get_tag_recommendations,
    list_platform_jobs,
    get_recent_runs,
    propose_remediation,
    propose_job_run,
]
