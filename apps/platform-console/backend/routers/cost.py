"""Cost & right-sizing checks — read-only system-table queries."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from backend import cache, deps
from backend.models import envelope
from dbx_platform import azure_cost, cost, llm_cost, platform_cost
from dbx_platform.system_tables import run_query

router = APIRouter(prefix="/api/cost")


def _text(value: Any) -> str | None:
    return None if value in (None, "") else str(value)


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _is_stale(value: Any, days: int = 2) -> bool:
    parsed = _date_value(value)
    return parsed is not None and (datetime.now(UTC).date() - parsed).days > days


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _azure_job_snapshot() -> dict[str, Any]:
    job_id = deps.azure_cost_job_id()
    if not job_id:
        return {}
    workspace = deps.get_ws()
    job = workspace.jobs.get(job_id)
    settings = getattr(job, "settings", None)
    schedule = getattr(settings, "schedule", None)
    runs = list(workspace.jobs.list_runs(job_id=job_id, limit=1))
    latest = runs[0] if runs else None
    state = getattr(latest, "state", None)
    return {
        "job_id": job_id,
        "job_name": str(
            getattr(settings, "name", "") or "[dbx-platform] azure-cost-pull"
        ),
        "schedule_status": _enum_value(getattr(schedule, "pause_status", None))
        or "UNKNOWN",
        "last_run_id": getattr(latest, "run_id", None),
        "last_run_result": _enum_value(getattr(state, "result_state", None)),
        "last_run_started_at": (
            datetime.fromtimestamp(latest.start_time / 1000, tz=UTC).isoformat()
            if latest is not None and getattr(latest, "start_time", None)
            else None
        ),
    }


def _cached_source(
    key: str,
    loader: Callable[[], dict[str, Any]],
    refresh: bool,
) -> dict[str, Any]:
    try:
        data, as_of, hit = cache.cached(key, loader, refresh, ttl_seconds=60)
        data.update({"as_of": as_of.isoformat(), "cached": hit})
        return data
    except Exception as error:  # noqa: BLE001 - each source degrades independently
        source_id = key.rsplit("/", 1)[-1]
        title, cost_basis, summary = {
            "databricks_list": (
                "Databricks list cost",
                "DATABRICKS_LIST",
                "Databricks system billing could not be read.",
            ),
            "azure_actual": (
                "Azure billed actuals",
                "AZURE_ACTUAL",
                "The persisted Azure billing ledger could not be read.",
            ),
            "ai_ledger": (
                "AI cost coverage",
                None,
                "The governed AI cost ledger could not be read.",
            ),
        }.get(source_id, ("Cost source", None, "This cost source could not be read."))
        return {
            "id": source_id,
            "title": title,
            "status": "unavailable",
            "amount": None,
            "currency": None,
            "cost_basis": cost_basis,
            "coverage_start": None,
            "coverage_end": None,
            "freshness": None,
            "series": [],
            "notes": (
                f"{summary} Check its data access and collector health "
                f"({error.__class__.__name__})."
            ),
            "as_of": datetime.now(UTC).isoformat(),
            "cached": False,
        }


def _databricks_source(days: int, workspace_id: str) -> dict[str, Any]:
    rows = cost.usage_report(
        deps.get_ws(),
        deps.warehouse_id(),
        days,
        workspace_id=workspace_id,
    )
    dates = sorted(
        {
            str(row.get("usage_date"))
            for row in rows
            if row.get("usage_date") not in (None, "")
        }
    )
    freshest = dates[-1] if dates else None
    status = "no_data" if not rows else ("stale" if _is_stale(freshest) else "healthy")
    return {
        "id": "databricks_list",
        "title": "Databricks list cost",
        "status": status,
        "amount": (
            round(sum(float(row.get("list_cost_usd") or 0) for row in rows), 2)
            if rows
            else None
        ),
        "currency": "USD",
        "cost_basis": "DATABRICKS_LIST",
        "coverage_start": dates[0] if dates else None,
        "coverage_end": freshest,
        "freshness": freshest,
        "series": [],
        "notes": (
            "Workspace-scoped list-price usage from Databricks system billing."
            if rows
            else "No billed Databricks usage exists in this period."
        ),
    }


def _azure_source(days: int, workspace_id: str, environment: str) -> dict[str, Any]:
    job: dict[str, Any] = {}
    try:
        job = _azure_job_snapshot()
    except Exception:  # noqa: BLE001 - data remains useful without Job metadata
        job = {"job_id": deps.azure_cost_job_id()}
    settings = deps.get_settings()
    resource_groups = settings.azure_cost_resource_group_list()
    if not deps.azure_cost_configured() or not resource_groups:
        return {
            "id": "azure_actual",
            "title": "Azure billed actuals",
            "status": "not_configured",
            "amount": None,
            "currency": None,
            "cost_basis": "AZURE_ACTUAL",
            "coverage_start": None,
            "coverage_end": None,
            "freshness": None,
            "series": [],
            "notes": (
                "Azure billing is not configured. Set the subscription, service "
                "credential, and scoped resource groups in the reviewed deployment."
            ),
            "job": job,
        }
    rows = platform_cost.fetch_scoped_daily(
        deps.get_ws(),
        deps.warehouse_id(),
        settings.dashboard_catalog,
        settings.dashboard_schema,
        workspace_id=workspace_id,
        environment=environment,
        resource_groups=resource_groups,
        days=days,
    )
    dates = sorted(
        {
            str(row.get("usage_date"))
            for row in rows
            if row.get("usage_date") not in (None, "")
        }
    )
    currencies = sorted(
        {str(row.get("currency")) for row in rows if row.get("currency")}
    )
    freshness_values = [row.get("ingested_at") for row in rows if row.get("ingested_at")]
    freshness = max(freshness_values, key=str) if freshness_values else (
        dates[-1] if dates else None
    )
    last_result = str(job.get("last_run_result") or "").upper()
    if not rows:
        status = "no_data" if last_result == "SUCCESS" else "never_run"
    else:
        status = "stale" if _is_stale(freshness) else "healthy"
    one_currency = len(currencies) == 1
    refresh_action = None
    if job.get("job_id") and status in {"never_run", "stale"}:
        refresh_action = {
            "action_type": "run-job",
            "job_id": job["job_id"],
            "job_name": job.get("job_name") or "[dbx-platform] azure-cost-pull",
        }
    return {
        "id": "azure_actual",
        "title": "Azure billed actuals",
        "status": status,
        "amount": (
            round(sum(float(row.get("cost") or 0) for row in rows), 2)
            if rows and one_currency
            else None
        ),
        "currency": currencies[0] if one_currency else None,
        "cost_basis": "AZURE_ACTUAL",
        "coverage_start": dates[0] if dates else None,
        "coverage_end": dates[-1] if dates else None,
        "freshness": _text(freshness),
        "series": [],
        "notes": (
            "Azure Cost Management billed actuals."
            if rows and one_currency
            else (
                "Multiple source currencies are present and are not combined."
                if rows
                else (
                    "The collector ran successfully and found no billed rows in this period."
                    if status == "no_data"
                    else (
                        "Azure billing is configured, but the exact bundle-managed "
                        "refresh Job is not bound to the app."
                        if not job.get("job_id")
                        else "Azure collection has never produced data for this scope."
                    )
                )
            )
        ),
        "job": job,
        "refresh_action": refresh_action,
    }


def _ai_source(days: int, workspace_id: str, environment: str) -> dict[str, Any]:
    settings = deps.get_settings()
    rows = llm_cost.read_llm_cost_daily(
        deps.get_ws(),
        deps.warehouse_id(),
        settings.dashboard_catalog,
        settings.dashboard_schema,
        workspace_id,
        environment,
        min(days, 400),
    )
    dates = sorted(
        {
            str(row.get("usage_date"))
            for row in rows
            if row.get("usage_date") not in (None, "")
        }
    )
    currencies = sorted(
        {str(row.get("currency")) for row in rows if row.get("currency")}
    )
    bases = sorted(
        {str(row.get("cost_basis")) for row in rows if row.get("cost_basis")}
    )
    freshness_values = [row.get("ingested_at") for row in rows if row.get("ingested_at")]
    freshness = max(freshness_values, key=str) if freshness_values else (
        dates[-1] if dates else None
    )
    combinable = len(currencies) == 1 and len(bases) == 1
    status = "no_data" if not rows else ("stale" if _is_stale(freshness) else "healthy")
    return {
        "id": "ai_ledger",
        "title": "AI cost coverage",
        "status": status,
        "amount": (
            round(sum(float(row.get("cost") or 0) for row in rows), 2)
            if rows and combinable
            else None
        ),
        "currency": currencies[0] if len(currencies) == 1 else None,
        "cost_basis": bases[0] if len(bases) == 1 else None,
        "coverage_start": dates[0] if dates else None,
        "coverage_end": dates[-1] if dates else None,
        "freshness": _text(freshness),
        "series": [],
        "notes": (
            "Persisted AI usage and provider cost ledger."
            if combinable
            else (
                "AI rows use multiple currencies or cost bases and are not combined."
                if rows
                else "The governed AI cost rollup has not produced rows for this period."
            )
        ),
    }


def _source_cards(days: int, refresh: bool) -> list[dict[str, Any]]:
    workspace_id, environment = deps.control_plane_scope()
    prefix = f"cost/sources/{workspace_id}/{environment}/{days}"
    return [
        _cached_source(
            f"{prefix}/databricks_list",
            lambda: _databricks_source(days, workspace_id),
            refresh,
        ),
        _cached_source(
            f"{prefix}/azure_actual",
            lambda: _azure_source(days, workspace_id, environment),
            refresh,
        ),
        _cached_source(
            f"{prefix}/ai_ledger",
            lambda: _ai_source(days, workspace_id, environment),
            refresh,
        ),
    ]


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
            workspace_id=workspace_id,
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
    overview["scope"]["workspace_name"] = deps.workspace_display_name()
    return overview


def _cost_overview(days: int, refresh: bool) -> tuple[dict, object, bool]:
    workspace_id, environment = deps.control_plane_scope()
    return cache.cached(
        f"cost/control/{workspace_id}/{environment}/{days}",
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
    return envelope(
        {**data, "source_cards": _source_cards(days, refresh)},
        as_of,
        hit,
    )


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
        f"cost/usage/{deps.control_plane_scope()[0]}/{days}",
        lambda: cost.usage_report(
            deps.get_ws(),
            deps.warehouse_id(),
            days,
            workspace_id=deps.control_plane_scope()[0],
        ),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/products")
def products(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    workspace_id, _ = deps.control_plane_scope()
    data, as_of, hit = cache.cached(
        f"cost/products/{workspace_id}/{days}",
        lambda: cost.product_spend(
            deps.get_ws(), deps.warehouse_id(), days, workspace_id
        ),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/top-jobs")
def top_jobs(days: int = 30, limit: int = 20, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    limit = max(1, min(100, limit))
    workspace_id, _ = deps.control_plane_scope()
    data, as_of, hit = cache.cached(
        f"cost/top-jobs/{workspace_id}/{days}/{limit}",
        lambda: cost.top_jobs(
            deps.get_ws(), deps.warehouse_id(), days, limit, workspace_id
        ),
        refresh,
    )
    return envelope(data, as_of, hit)


@router.get("/cluster-utilization")
def cluster_utilization(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    workspace_id, _ = deps.control_plane_scope()

    def load() -> list[dict]:
        s = deps.get_settings()
        rows = cost.cluster_utilization(
            deps.get_ws(), deps.warehouse_id(), days, workspace_id
        )
        return cost.classify_cluster_utilization(
            rows, s.util_cpu_threshold_pct, s.util_mem_threshold_pct)

    data, as_of, hit = cache.cached(
        f"cost/cluster-utilization/{workspace_id}/{days}", load, refresh
    )
    return envelope(data, as_of, hit)


@router.get("/warehouse-utilization")
def warehouse_utilization(days: int = 30, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    workspace_id, _ = deps.control_plane_scope()

    def load() -> list[dict]:
        s = deps.get_settings()
        rows = cost.warehouse_utilization(
            deps.get_ws(), deps.warehouse_id(), days, workspace_id
        )
        return cost.classify_warehouse_utilization(
            rows, s.warehouse_min_queries, s.warehouse_queue_warn_seconds)

    data, as_of, hit = cache.cached(
        f"cost/warehouse-utilization/{workspace_id}/{days}", load, refresh
    )
    return envelope(data, as_of, hit)


@router.get("/failed-run-waste")
def failed_run_waste(days: int = 30, limit: int = 20, refresh: bool = False) -> dict:
    days = deps.clamp_days(days)
    limit = max(1, min(100, limit))
    workspace_id, _ = deps.control_plane_scope()
    data, as_of, hit = cache.cached(
        f"cost/failed-run-waste/{workspace_id}/{days}/{limit}",
        lambda: cost.failed_run_waste(
            deps.get_ws(), deps.warehouse_id(), days, limit, workspace_id
        ),
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
        lambda: cost.attribution(
            deps.get_ws(), deps.warehouse_id(), dimension, days, workspace_id
        ),
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
def azure(
    days: int = 30,
    by: Literal["service", "resource-group", "resource", "meter"] = "service",
    refresh: bool = False,
) -> dict:
    days = deps.clamp_days(days)
    workspace_id, environment = deps.control_plane_scope()

    def load() -> list[dict]:
        s = deps.get_settings()
        workspace_id, environment = deps.control_plane_scope()
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
