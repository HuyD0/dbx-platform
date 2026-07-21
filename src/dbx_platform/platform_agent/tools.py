"""Small allowlist of read-only tools for the App-hosted platform agent."""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.tools import tool

from dbx_platform import cost, llm_cost
from dbx_platform.config import Settings
from dbx_platform.platform_agent.formatting import rows_to_text

_client_factory: Callable[[], object] | None = None
_settings_factory: Callable[[], Settings] | None = None


def configure_runtime(*, client_factory, settings_factory) -> None:
    """Bind tools to the App's resource-scoped client and settings."""
    global _client_factory, _settings_factory
    _client_factory = client_factory
    _settings_factory = settings_factory


def _client():
    if _client_factory is None:
        raise RuntimeError("Platform agent workspace client is not configured.")
    return _client_factory()


def _settings() -> Settings:
    if _settings_factory is None:
        raise RuntimeError("Platform agent settings are not configured.")
    return _settings_factory()


def _render(rows: list[dict], tool_name: str, source: str) -> str:
    return rows_to_text(rows, tool_name=tool_name, source=source)


@tool
def get_cost_report(days: int = 30) -> str:
    """DBU and list-price cost by SKU and workspace over the last N days."""
    settings = _settings()
    return _render(
        cost.usage_report(_client(), settings.warehouse_id, days),
        "get_cost_report",
        "system.billing.usage + system.billing.list_prices",
    )


@tool
def get_top_jobs(days: int = 30, limit: int = 20) -> str:
    """The most expensive jobs by list cost over the last N days."""
    settings = _settings()
    return _render(
        cost.top_jobs(_client(), settings.warehouse_id, days, limit),
        "get_top_jobs",
        "system.billing.usage + system.lakeflow jobs",
    )


@tool
def get_cluster_utilization(days: int = 30) -> str:
    """Under-utilized clusters ranked by cost."""
    settings = _settings()
    rows = cost.cluster_utilization(_client(), settings.warehouse_id, days)
    return _render(
        cost.classify_cluster_utilization(
            rows,
            settings.util_cpu_threshold_pct,
            settings.util_mem_threshold_pct,
        ),
        "get_cluster_utilization",
        "system.compute.node_timeline + system.billing.usage",
    )


@tool
def get_failed_run_waste(days: int = 30) -> str:
    """List cost burned on failed or timed-out job runs."""
    settings = _settings()
    return _render(
        cost.failed_run_waste(_client(), settings.warehouse_id, days, 20),
        "get_failed_run_waste",
        "system.lakeflow.job_run_timeline + system.billing.usage",
    )


@tool
def get_warehouse_utilization(days: int = 30) -> str:
    """SQL warehouses with idle spend, low use, or sustained queueing."""
    settings = _settings()
    rows = cost.warehouse_utilization(_client(), settings.warehouse_id, days)
    return _render(
        cost.classify_warehouse_utilization(
            rows,
            settings.warehouse_min_queries,
            settings.warehouse_queue_warn_seconds,
        ),
        "get_warehouse_utilization",
        "system.query.history + system.billing.usage",
    )


@tool
def get_llm_cost_and_efficiency(days: int = 30) -> str:
    """LLM list cost, requests, tokens, and efficiency recommendations."""
    settings = _settings()
    client = _client()
    try:
        cost_rows = llm_cost.databricks_cost(
            client, settings.warehouse_id, days, gateway_enriched=True
        )
    except Exception:  # noqa: BLE001 - older workspaces may lack Gateway columns
        cost_rows = llm_cost.databricks_cost(
            client, settings.warehouse_id, days, gateway_enriched=False
        )
    try:
        usage_rows = llm_cost.gateway_usage(client, settings.warehouse_id, min(days, 90))
    except Exception:  # noqa: BLE001 - fall back to serving usage when unavailable
        usage_rows = llm_cost.endpoint_usage(client, settings.warehouse_id, min(days, 90))
    costs = llm_cost.normalize_cost_rows(
        cost_rows,
        "system.billing.usage",
        "DATABRICKS_LIST",
        environment=settings.environment,
    )
    usage = llm_cost.normalize_usage_rows(
        usage_rows, "model usage", environment=settings.environment
    )
    summary = llm_cost.summarize(costs, usage, days)
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
        *llm_cost.efficiency(costs, usage)["recommendations"],
    ]
    return _render(
        rows,
        "get_llm_cost_and_efficiency",
        "system.billing.usage + system.ai_gateway + system.serving.endpoint_usage",
    )


ALL_TOOLS = [
    get_cost_report,
    get_top_jobs,
    get_cluster_utilization,
    get_failed_run_waste,
    get_warehouse_utilization,
    get_llm_cost_and_efficiency,
]

