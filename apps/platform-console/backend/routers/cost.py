"""Cost & right-sizing checks — read-only system-table queries."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend import cache, deps
from backend.models import envelope
from dbx_platform import azure_cost, cost, platform_cost
from dbx_platform.system_tables import run_query

router = APIRouter(prefix="/api/cost")


def _window_days(window: str | None, days: int) -> int:
    if window:
        value = window.lower().removesuffix("d")
        if value.isdigit():
            days = int(value)
    return deps.clamp_days(days)


def _load_cost_overview(days: int) -> dict:
    settings = deps.get_settings()
    workspace_id, environment = deps.control_plane_scope()
    resource_groups = settings.azure_cost_resource_group_list()
    azure_rows = platform_cost.fetch_scoped_daily(
        deps.get_ws(),
        deps.warehouse_id(),
        settings.dashboard_catalog,
        settings.dashboard_schema,
        workspace_id=workspace_id,
        environment=environment,
        resource_groups=resource_groups,
        days=days,
    )
    databricks_rows: list[dict] = []
    databricks_error: str | None = None
    try:
        databricks_rows = cost.usage_report(
            deps.get_ws(),
            deps.warehouse_id(),
            days,
        )
    except Exception:
        # Azure ActualCost remains authoritative even when the non-additive
        # Databricks workload attribution source is temporarily unavailable.
        databricks_error = "Databricks list-cost attribution is temporarily unavailable."
    overview = platform_cost.build_overview(
        azure_rows,
        databricks_rows,
        days=days,
        workspace_id=workspace_id,
        environment=environment,
        resource_groups=resource_groups,
        spike_pct=settings.azure_spike_pct,
        spike_min_cost=settings.azure_spike_min_cost,
        acceleration_pct=settings.azure_acceleration_pct,
        acceleration_min_cost=settings.azure_acceleration_min_cost,
        databricks_error=databricks_error,
    )
    try:
        alignment_rows = azure_cost.reconciliation(
            deps.get_ws(),
            deps.warehouse_id(),
            settings.dashboard_catalog,
            settings.dashboard_schema,
            days,
            workspace_id=workspace_id,
            environment=environment,
        )
        overview["billing_alignment"] = azure_cost.summarize_reconciliation(
            alignment_rows
        )
    except Exception:
        overview["billing_alignment"] = {
            "status": "unavailable",
            "variance_count": 0,
            "unmatched_count": 0,
            "latest_azure_date": None,
            "latest_databricks_date": None,
            "azure_lag_days": None,
            "databricks_lag_days": None,
            "azure_totals": [],
            "databricks_totals": [],
            "largest_pattern_variance": None,
            "money_comparable": False,
            "notes": "Daily Azure and Databricks alignment is temporarily unavailable.",
        }
    return overview


def _cost_overview(days: int, refresh: bool) -> tuple[dict, object, bool]:
    return cache.cached(
        f"cost/control/{days}",
        lambda: _load_cost_overview(days),
        refresh,
    )


@router.get("/overview")
def cost_overview(
    days: int = 30,
    window: str | None = Query(default=None, pattern=r"^\d{1,3}d?$"),
    refresh: bool = False,
) -> dict:
    days = _window_days(window, days)
    data, as_of, hit = _cost_overview(days, refresh)
    return envelope(data, as_of, hit)


@router.get("/timeseries")
def cost_timeseries(
    days: int = 30,
    window: str | None = Query(default=None, pattern=r"^\d{1,3}d?$"),
    refresh: bool = False,
) -> dict:
    days = _window_days(window, days)
    data, as_of, hit = _cost_overview(days, refresh)
    return envelope(data["series"], as_of, hit)


@router.get("/breakdown")
def cost_breakdown(
    by: str = Query(default="category", pattern=r"^(category|ownership|component)$"),
    days: int = 30,
    refresh: bool = False,
) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = _cost_overview(days, refresh)
    key = {
        "category": "categories",
        "ownership": "ownership",
        "component": "components",
    }[by]
    return envelope(data[key], as_of, hit)


@router.get("/movers")
def cost_movers(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = _cost_overview(days, refresh)
    return envelope(data["movers"], as_of, hit)


@router.get("/anomalies")
def cost_anomalies(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = _cost_overview(days, refresh)
    return envelope(data["anomalies"], as_of, hit)


@router.get("/anomalies/{anomaly_id}")
def cost_anomaly(anomaly_id: str, days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = _cost_overview(days, refresh)
    anomaly = next(
        (row for row in data["anomalies"] if row["id"] == anomaly_id),
        None,
    )
    if anomaly is None:
        raise HTTPException(status_code=404, detail="Cost anomaly not found.")
    related_series = [
        row
        for row in data["series"]
        if row["category"] == anomaly["category"]
        and row["currency"] == anomaly["currency"]
    ]
    related_mover = next(
        (
            row
            for row in data["movers"]
            if row["category"] == anomaly["category"]
            and row["currency"] == anomaly["currency"]
        ),
        None,
    )
    return envelope(
        {
            "anomaly": anomaly,
            "series": related_series,
            "mover": related_mover,
            "scope": data["scope"],
            "databricks_list": data["databricks_list"],
            "investigation": {
                "checks": [
                    "Confirm whether the increase aligns with a planned workload.",
                    "Review Databricks SKU attribution for the same period.",
                    "Compare the affected resource group and service category.",
                ],
                "safe_actions": [
                    "Open filtered Cost Explorer",
                    "Export the evidence",
                    "Draft a governed cost-remediation proposal",
                ],
            },
        },
        as_of,
        hit,
    )


@router.get("/data-health")
def cost_data_health(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    data, as_of, hit = _cost_overview(days, refresh)
    return envelope(data["data_health"], as_of, hit)


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


@router.get("/foundry-attribution")
def foundry_attribution(days: int = 30, refresh: bool = False) -> dict:
    """Current-scope Foundry actuals from persisted resource/meter detail."""

    days = deps.clamp_days(days)
    workspace_id, environment = deps.control_plane_scope()

    def load() -> dict:
        settings = deps.get_settings()
        return azure_cost.foundry_attribution(
            deps.get_ws(),
            deps.warehouse_id(),
            settings.dashboard_catalog,
            settings.dashboard_schema,
            days,
            workspace_id=workspace_id,
            environment=environment,
        )

    result, as_of, hit = cache.cached(
        f"cost/foundry-attribution/{workspace_id}/{environment}/{days}",
        load,
        refresh,
    )
    response = envelope(result["rows"], as_of, hit)
    response["source_status"] = result["source_status"]
    return response


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
