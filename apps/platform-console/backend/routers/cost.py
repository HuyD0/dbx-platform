"""Cost & right-sizing checks — read-only system-table queries."""

from __future__ import annotations

from fastapi import APIRouter

from backend import cache, deps
from backend.models import envelope
from dbx_platform import azure_cost, cost
from dbx_platform.system_tables import run_query

router = APIRouter(prefix="/api/cost")


@router.get("/usage")
def usage(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = cache.cached(
        f"cost/usage/{days}",
        lambda: cost.usage_report(deps.get_ws(), deps.warehouse_id(), days),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/products")
def products(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    workspace_id, _ = deps.control_plane_scope()
    data, as_of, hit = cache.cached(
        f"cost/products/{workspace_id}/{days}",
        lambda: cost.product_spend(deps.get_ws(), deps.warehouse_id(), days),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/top-jobs")
def top_jobs(days: int = 30, limit: int = 20, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    limit = max(1, min(100, limit))
    data, as_of, hit = cache.cached(
        f"cost/top-jobs/{days}/{limit}",
        lambda: cost.top_jobs(deps.get_ws(), deps.warehouse_id(), days, limit),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/cluster-utilization")
def cluster_utilization(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)

    def load() -> list[dict]:
        s = deps.get_settings()
        rows = cost.cluster_utilization(deps.get_ws(), deps.warehouse_id(), days)
        return cost.classify_cluster_utilization(
            rows, s.util_cpu_threshold_pct, s.util_mem_threshold_pct)

    data, as_of, hit = cache.cached(f"cost/cluster-utilization/{days}", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/warehouse-utilization")
def warehouse_utilization(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)

    def load() -> list[dict]:
        s = deps.get_settings()
        rows = cost.warehouse_utilization(deps.get_ws(), deps.warehouse_id(), days)
        return cost.classify_warehouse_utilization(
            rows, s.warehouse_min_queries, s.warehouse_queue_warn_seconds)

    data, as_of, hit = cache.cached(f"cost/warehouse-utilization/{days}", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/failed-run-waste")
def failed_run_waste(days: int = 30, limit: int = 20, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    limit = max(1, min(100, limit))
    data, as_of, hit = cache.cached(
        f"cost/failed-run-waste/{days}/{limit}",
        lambda: cost.failed_run_waste(deps.get_ws(), deps.warehouse_id(), days, limit),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/attribution")
def attribution(dimension: str = "team", days: int = 30, refresh: bool = False) -> dict:
    """Spend by enforced tag (team/project) or whole workspace.

    The dimension allowlist lives in cost.ATTRIBUTION_DIMENSIONS; an unknown
    value raises ValueError inside the loader and maps to a 400.
    """
    days = deps.clamp_days(days)
    workspace_id, _ = deps.control_plane_scope()
    data, as_of, hit = cache.cached(
        f"cost/attribution/{workspace_id}/{dimension}/{days}",
        lambda: cost.attribution(deps.get_ws(), deps.warehouse_id(), dimension, days),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/azure-detail")
def azure_detail(
    by: str = "meter",
    days: int = 30,
    bucket: str | None = None,
    refresh: bool = False,
) -> dict:
    """Detail-grain Azure spend (resource/meter) — per-Foundry-deployment drill."""
    days = deps.clamp_days(days)
    workspace_id, environment = deps.control_plane_scope()

    def load() -> list[dict]:
        s = deps.get_settings()
        return azure_cost.report_detail(
            deps.get_ws(),
            deps.warehouse_id(),
            s.dashboard_catalog,
            s.dashboard_schema,
            by,
            days,
            bucket,
            workspace_id=workspace_id,
            environment=environment,
        )

    data, as_of, hit = cache.cached(
        f"cost/azure-detail/{workspace_id}/{environment}/{by}/{bucket or 'all'}/{days}",
        load,
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/azure")
def azure(days: int = 30, by: str = "service", refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    workspace_id, environment = deps.control_plane_scope()

    def load() -> list[dict]:
        s = deps.get_settings()
        return azure_cost.report(
            deps.get_ws(),
            deps.warehouse_id(),
            s.dashboard_catalog,
            s.dashboard_schema,
            by,
            days,
            workspace_id=workspace_id,
            environment=environment,
        )

    data, as_of, hit = cache.cached(
        f"cost/azure/{workspace_id}/{environment}/{by}/{days}", load, refresh
    )
    return envelope(data, as_of, hit)


@router.get("/reconciliation")
def reconciliation(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    workspace_id, environment = deps.control_plane_scope()

    def load() -> list[dict]:
        s = deps.get_settings()
        return azure_cost.reconciliation(
            deps.get_ws(),
            deps.warehouse_id(),
            s.dashboard_catalog,
            s.dashboard_schema,
            days,
            workspace_id=workspace_id,
            environment=environment,
        )

    data, as_of, hit = cache.cached(
        f"cost/reconciliation/{workspace_id}/{environment}/{days}",
        load,
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/azure-anomalies")
def azure_anomalies(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    workspace_id, environment = deps.control_plane_scope()

    def load() -> list[dict]:
        s = deps.get_settings()
        rows = azure_cost.fetch_daily_buckets(
            deps.get_ws(),
            deps.warehouse_id(),
            s.dashboard_catalog,
            s.dashboard_schema,
            days,
            workspace_id=workspace_id,
            environment=environment,
        )
        return azure_cost.classify_azure_spend(
            rows,
            s.azure_spike_pct,
            s.azure_spike_min_cost,
        )

    data, as_of, hit = cache.cached(
        f"cost/azure-anomalies/{workspace_id}/{environment}/{days}",
        load,
        refresh,
    )
    return envelope(data, as_of, hit)


def _forecast_rows() -> list[dict]:
    s = deps.get_settings()
    workspace_id, environment = deps.control_plane_scope()
    fq = f"{s.dashboard_catalog}.{s.dashboard_schema}"
    return run_query(
        deps.get_ws(),
        f"""
        WITH latest AS (
          SELECT MAX(run_date) AS run_date
          FROM {fq}.cost_forecasts
        ),
        current_scope AS (
          SELECT subscription_id, scope_filter
          FROM {fq}.azure_costs
          WHERE workspace_id = :workspace_id
            AND environment = :environment
            AND COALESCE(scope_filter, '') <> ''
          ORDER BY ingested_at DESC
          LIMIT 1
        ),
        series_currency AS (
          SELECT c.service_bucket AS series,
                 CASE WHEN COUNT(DISTINCT c.currency) = 1 THEN MAX(c.currency)
                      ELSE 'UNRESOLVED' END AS currency,
                 COUNT(DISTINCT c.currency) AS currency_count
          FROM {fq}.azure_costs c
          INNER JOIN current_scope s
            ON c.subscription_id = s.subscription_id
            AND c.scope_filter = s.scope_filter
          WHERE c.usage_date >= DATE_SUB(CURRENT_DATE(), 90)
            AND c.workspace_id = :workspace_id
            AND c.environment = :environment
          GROUP BY c.service_bucket
        )
        SELECT f.run_date, f.target_date, f.series, f.p10, f.p50, f.p90,
               f.model_version, f.feature_set_version,
               COALESCE(c.currency, 'UNRESOLVED') AS currency,
               COALESCE(c.currency_count, 0) AS currency_count,
               'AZURE_ACTUAL_FORECAST' AS cost_basis
        FROM {fq}.cost_forecasts f
        INNER JOIN latest l ON f.run_date = l.run_date
        LEFT JOIN series_currency c ON f.series = c.series
        ORDER BY f.target_date, f.series
        """,
        deps.warehouse_id(),
        {"workspace_id": workspace_id, "environment": environment},
    )


@router.get("/azure-forecast")
def azure_forecast(refresh: bool = False) -> dict:
    workspace_id, environment = deps.control_plane_scope()
    data, as_of, hit = cache.cached(
        f"cost/azure-forecast/{workspace_id}/{environment}",
        _forecast_rows,
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/forecast")
def consolidated_forecast(refresh: bool = False) -> dict:
    """Forecast rows stay separate by series and resolved source currency."""
    workspace_id, environment = deps.control_plane_scope()
    data, as_of, hit = cache.cached(
        f"cost/consolidated-forecast/{workspace_id}/{environment}",
        _forecast_rows,
        refresh,
    )
    return envelope(data, as_of, hit)
