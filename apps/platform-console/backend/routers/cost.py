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


@router.get("/azure")
def azure(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)

    def load() -> list[dict]:
        s = deps.get_settings()
        return azure_cost.report(
            deps.get_ws(),
            deps.warehouse_id(),
            s.dashboard_catalog,
            s.dashboard_schema,
            "service",
            days,
        )

    data, as_of, hit = cache.cached(f"cost/azure/{days}", load, refresh)
    return envelope(data, as_of, hit)


@router.get("/azure-anomalies")
def azure_anomalies(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)

    def load() -> list[dict]:
        s = deps.get_settings()
        rows = azure_cost.fetch_daily_buckets(
            deps.get_ws(),
            deps.warehouse_id(),
            s.dashboard_catalog,
            s.dashboard_schema,
            days,
        )
        return azure_cost.classify_azure_spend(
            rows,
            s.azure_spike_pct,
            s.azure_spike_min_cost,
        )

    data, as_of, hit = cache.cached(
        f"cost/azure-anomalies/{days}",
        load,
        refresh,
    )
    return envelope(data, as_of, hit)


def _forecast_rows() -> list[dict]:
    s = deps.get_settings()
    fq = f"{s.dashboard_catalog}.{s.dashboard_schema}"
    return run_query(
        deps.get_ws(),
        f"""
        WITH latest AS (
          SELECT MAX(run_date) AS run_date
          FROM {fq}.cost_forecasts
        ),
        series_currency AS (
          SELECT service_bucket AS series,
                 CASE WHEN COUNT(DISTINCT currency) = 1 THEN MAX(currency)
                      ELSE 'UNRESOLVED' END AS currency,
                 COUNT(DISTINCT currency) AS currency_count
          FROM {fq}.azure_costs
          WHERE usage_date >= DATE_SUB(CURRENT_DATE(), 90)
          GROUP BY service_bucket
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
    )


@router.get("/azure-forecast")
def azure_forecast(refresh: bool = False) -> dict:
    data, as_of, hit = cache.cached(
        "cost/azure-forecast",
        _forecast_rows,
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/forecast")
def consolidated_forecast(refresh: bool = False) -> dict:
    """Forecast rows stay separate by series and resolved source currency."""
    data, as_of, hit = cache.cached(
        "cost/consolidated-forecast",
        _forecast_rows,
        refresh,
    )
    return envelope(data, as_of, hit)
