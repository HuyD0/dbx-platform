"""Provider-aware LLM cost and usage reporting.

The module deliberately keeps three financial bases separate:

* ``DATABRICKS_LIST`` is usage multiplied by the public Databricks list price.
* ``AZURE_ACTUAL`` is the Cost Management actual-cost ledger.
* ``PROVIDER_ESTIMATE`` is Unity AI Gateway's estimate for external models.

Callers must not add rows across bases and present the result as a single
invoice total.  The helpers below therefore group totals and forecasts by
currency *and* cost basis.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import load_query, run_query

COST_BASES = {"DATABRICKS_LIST", "AZURE_ACTUAL", "PROVIDER_ESTIMATE"}
_SOURCE_STATUSES = {"available", "partial", "unavailable"}
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BREAKDOWN_DIMENSIONS = {
    "provider",
    "model",
    "endpoint",
    "principal",
    "team",
    "use_case",
    "workspace_id",
}
COST_ROW_SCHEMA = (
    "array<struct<usage_date:date,workspace_id:string,environment:string,provider:string,"
    "model:string,endpoint:string,principal:string,team:string,use_case:string,"
    "cost:double,currency:string,cost_basis:string,source:string>>"
)
USAGE_ROW_SCHEMA = (
    "array<struct<usage_hour:timestamp,workspace_id:string,environment:string,provider:string,"
    "model:string,endpoint:string,principal:string,team:string,use_case:string,"
    "requests:bigint,successful_requests:bigint,invocations:bigint,"
    "input_tokens:bigint,output_tokens:bigint,"
    "cached_tokens:bigint,reasoning_tokens:bigint,errors:bigint,retries:bigint,"
    "p95_latency_ms:double,source:string>>"
)


@dataclass(frozen=True)
class AzureActualCostResult:
    rows: list[dict]
    status: str
    notes: str


SOURCE_HEALTH_ROW_SCHEMA = (
    "array<struct<workspace_id:string,environment:string,source_key:string,"
    "source:string,source_type:string,status:string,cost_basis:string,"
    "freshness:string,retention_days:int,coverage_start:string,"
    "coverage_end:string,row_count:bigint,available_metrics_json:string,"
    "notes:string,checked_at:string>>"
)


def databricks_cost(
    w: WorkspaceClient, warehouse_id: str, days: int, *, gateway_enriched: bool = True
) -> list[dict]:
    """Daily Databricks-hosted model cost at list price.

    The enriched query uses Unity AI Gateway struct fields introduced in 2026.
    ``gateway_enriched=False`` selects the compatibility query for workspaces
    where those fields are not yet present.
    """

    name = "llm_databricks_gateway_cost" if gateway_enriched else "llm_databricks_cost"
    return run_query(w, load_query(name), warehouse_id, {"days": days})


def gateway_usage(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    """Request, token, retry and latency metrics from Unity AI Gateway."""

    return run_query(w, load_query("llm_gateway_usage"), warehouse_id, {"days": days})


def endpoint_usage(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    """Compatibility usage query for the legacy serving usage table."""

    return run_query(w, load_query("llm_endpoint_usage_daily"), warehouse_id, {"days": days})


def external_model_spend(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    """Hourly external-provider estimates aggregated to daily cost."""

    return run_query(w, load_query("llm_external_model_spend"), warehouse_id, {"days": days})


def azure_actual_cost(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    days: int,
    *,
    workspace_id: str,
    environment: str,
) -> AzureActualCostResult:
    """Actual Azure AI cost from the ingested Cost Management ledger."""

    params = {
        "days": days,
        "workspace_id": workspace_id,
        "environment": environment,
    }
    detail_table = f"{catalog}.{schema}.azure_cost_details"
    detail_sql = load_query("llm_azure_actual_cost_detail").replace(
        "__AZURE_COST_DETAIL_TABLE__", detail_table
    )
    try:
        rows = run_query(w, detail_sql, warehouse_id, params)
        return AzureActualCostResult(
            rows=rows,
            status="available",
            notes="Actual billing with resource and meter attribution",
        )
    except Exception as detail_error:  # noqa: BLE001 - compatibility source
        table = f"{catalog}.{schema}.azure_costs"
        sql = load_query("llm_azure_actual_cost").replace("__AZURE_COST_TABLE__", table)
        rows = run_query(w, sql, warehouse_id, params)
        return AzureActualCostResult(
            rows=rows,
            status="partial",
            notes=(
                "Actual billing is available, but resource/meter/use-case "
                "attribution is unavailable "
                f"({detail_error.__class__.__name__})"
            ),
        )


def create_ledger_table_statements(catalog: str, schema: str) -> list[tuple[str, str]]:
    """Idempotent DDL for normalized LLM cost, usage and budget tables."""

    fq = f"{catalog}.{schema}"
    return [
        (
            f"table {fq}.llm_cost_daily",
            f"CREATE TABLE IF NOT EXISTS {fq}.llm_cost_daily ("
            "usage_date DATE, workspace_id STRING, environment STRING, "
            "provider STRING, model STRING, "
            "endpoint STRING, principal STRING, team STRING, use_case STRING, "
            "cost DOUBLE, currency STRING, cost_basis STRING, source STRING, "
            "ingested_at TIMESTAMP) "
            "COMMENT 'Normalized LLM cost; financial bases are never mixed'",
        ),
        (
            f"table {fq}.llm_usage_hourly",
            f"CREATE TABLE IF NOT EXISTS {fq}.llm_usage_hourly ("
            "usage_hour TIMESTAMP, workspace_id STRING, environment STRING, "
            "provider STRING, model STRING, "
            "endpoint STRING, principal STRING, team STRING, use_case STRING, "
            "requests BIGINT, successful_requests BIGINT, invocations BIGINT, "
            "input_tokens BIGINT, "
            "output_tokens BIGINT, cached_tokens BIGINT, reasoning_tokens BIGINT, "
            "errors BIGINT, retries BIGINT, p95_latency_ms DOUBLE, source STRING, "
            "ingested_at TIMESTAMP) "
            "COMMENT 'Normalized hourly LLM usage and performance; retain 90 days'",
        ),
        (
            f"table {fq}.llm_budgets",
            f"CREATE TABLE IF NOT EXISTS {fq}.llm_budgets ("
            "budget_id STRING, workspace_id STRING, environment STRING, scope_type STRING, "
            "scope_value STRING, cost_basis STRING, month DATE, currency STRING, "
            "amount DOUBLE, "
            "warning_pct INT DEFAULT 80, critical_pct INT DEFAULT 100, "
            "status STRING, plan_hash STRING, "
            "updated_by STRING, updated_at TIMESTAMP) "
            "COMMENT 'Human-approved monthly LLM budgets'",
        ),
        (
            f"table {fq}.llm_source_health",
            f"CREATE TABLE IF NOT EXISTS {fq}.llm_source_health ("
            "workspace_id STRING NOT NULL, environment STRING NOT NULL, "
            "source_key STRING NOT NULL, source STRING NOT NULL, "
            "source_type STRING NOT NULL, status STRING NOT NULL, "
            "cost_basis STRING, freshness STRING NOT NULL, retention_days INT, "
            "coverage_start DATE, coverage_end DATE, row_count BIGINT NOT NULL, "
            "available_metrics_json STRING, notes STRING, "
            "checked_at TIMESTAMP NOT NULL, last_success_at TIMESTAMP) "
            "COMMENT 'Feature detection, freshness and coverage for persisted LLM ledgers'",
        ),
    ]


def budget_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    workspace_id: str,
    environment: str,
) -> list[dict]:
    """Read active monthly budgets; missing tables are handled by the caller."""

    return run_query(
        w,
        f"SELECT budget_id, workspace_id, environment, scope_type, scope_value, "
        f"cost_basis, month, currency, amount, warning_pct, critical_pct, "
        f"status, updated_by, updated_at "
        f"FROM {catalog}.{schema}.llm_budgets "
        "WHERE workspace_id IN (:workspace_id, 'all') "
        "AND environment IN (:environment, 'all') "
        "AND month = DATE_TRUNC('MONTH', CURRENT_DATE()) "
        "ORDER BY scope_type, scope_value, cost_basis, currency",
        warehouse_id,
        {"workspace_id": workspace_id, "environment": environment},
    )


def evaluate_budgets(
    budgets: list[dict],
    cost_rows: list[dict],
    *,
    today: date | None = None,
) -> list[dict]:
    """Calculate threshold state without combining cost bases or currencies."""

    today = today or date.today()
    results = []
    for budget in budgets:
        scope_type = str(budget.get("scope_type") or "workspace")
        scope_value = str(budget.get("scope_value") or "all")
        basis = str(budget.get("cost_basis") or "")
        currency = str(budget.get("currency") or "").upper()
        budget_month = _coerce_date(budget.get("month")) or today.replace(day=1)
        month_start = budget_month.replace(day=1)
        matching = [
            row
            for row in cost_rows
            if row.get("cost_basis") == basis
            and str(row.get("currency") or "").upper() == currency
            and (_coerce_date(row.get("usage_date")) or date.min).replace(day=1) == month_start
            and (
                scope_type == "workspace"
                or str(row.get(scope_type) or "unallocated") == scope_value
            )
        ]
        spend = sum(_number(row.get("cost")) for row in matching)
        amount = _number(budget.get("amount"))
        consumed_pct = spend / amount * 100 if amount > 0 else None
        warning = _integer(budget.get("warning_pct") or 80)
        critical = _integer(budget.get("critical_pct") or 100)
        if consumed_pct is None:
            state = "INVALID"
        elif consumed_pct >= critical:
            state = "CRITICAL"
        elif consumed_pct >= warning:
            state = "WARNING"
        else:
            state = "OK"
        results.append(
            {
                **budget,
                "spend": round(spend, 2),
                "remaining": round(max(amount - spend, 0), 2),
                "consumed_pct": round(consumed_pct, 1) if consumed_pct is not None else None,
                "threshold_state": state,
            }
        )
    return results


def setup_ledger_tables(
    w: WorkspaceClient, warehouse_id: str, catalog: str, schema: str
) -> list[str]:
    """Disabled compatibility entrypoint; deployment migrations own DDL."""

    del w, warehouse_id, catalog, schema
    raise RuntimeError(
        "Direct LLM ledger setup is disabled; run the deployment schema_migrations Job."
    )


def merge_cost_rows_sql(catalog: str, schema: str) -> str:
    fq = f"{catalog}.{schema}.llm_cost_daily"
    dimensions = (
        "workspace_id",
        "environment",
        "provider",
        "model",
        "endpoint",
        "principal",
        "team",
        "use_case",
        "currency",
        "cost_basis",
        "source",
    )
    match = " AND ".join(f"t.{column} = s.{column}" for column in dimensions)
    columns = (
        "usage_date",
        *dimensions,
        "cost",
    )
    return (
        f"MERGE INTO {fq} t USING ("
        f"SELECT item.* FROM (SELECT explode(from_json(:rows, '{COST_ROW_SCHEMA}')) item)"
        f") s ON t.usage_date = s.usage_date AND {match} "
        "WHEN MATCHED THEN UPDATE SET t.cost = s.cost, "
        "t.ingested_at = current_timestamp() "
        f"WHEN NOT MATCHED THEN INSERT ({', '.join(columns)}, ingested_at) "
        f"VALUES ({', '.join(f's.{column}' for column in columns)}, current_timestamp()) "
        "WHEN NOT MATCHED BY SOURCE AND t.workspace_id = :workspace_id "
        "AND t.environment = :environment AND t.source = :source "
        "AND t.cost_basis = :cost_basis "
        "AND t.usage_date BETWEEN CAST(:window_start AS DATE) "
        "AND CAST(:window_end AS DATE) THEN DELETE"
    )


def merge_usage_rows_sql(catalog: str, schema: str) -> str:
    fq = f"{catalog}.{schema}.llm_usage_hourly"
    dimensions = (
        "workspace_id",
        "environment",
        "provider",
        "model",
        "endpoint",
        "principal",
        "team",
        "use_case",
        "source",
    )
    metrics = (
        "requests",
        "successful_requests",
        "invocations",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "errors",
        "retries",
        "p95_latency_ms",
    )
    match = " AND ".join(f"t.{column} = s.{column}" for column in dimensions)
    columns = ("usage_hour", *dimensions, *metrics)
    updates = ", ".join(f"t.{column} = s.{column}" for column in metrics)
    return (
        f"MERGE INTO {fq} t USING ("
        f"SELECT item.* FROM (SELECT explode(from_json(:rows, '{USAGE_ROW_SCHEMA}')) item)"
        f") s ON t.usage_hour = s.usage_hour AND {match} "
        f"WHEN MATCHED THEN UPDATE SET {updates}, t.ingested_at = current_timestamp() "
        f"WHEN NOT MATCHED THEN INSERT ({', '.join(columns)}, ingested_at) "
        f"VALUES ({', '.join(f's.{column}' for column in columns)}, current_timestamp()) "
        "WHEN NOT MATCHED BY SOURCE AND t.workspace_id = :workspace_id "
        "AND t.environment = :environment AND t.source = :source "
        "AND t.usage_hour >= CAST(:window_start AS DATE) "
        "AND t.usage_hour < DATE_ADD(CAST(:window_end AS DATE), 1) THEN DELETE"
    )


def store_ledger(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    cost_rows: list[dict],
    usage_rows: list[dict],
    *,
    window_start: str,
    window_end: str,
    cost_scopes: list[dict[str, str]],
    usage_scopes: list[dict[str, str]],
) -> dict[str, int]:
    """Atomically reconcile each successfully reprocessed ledger scope.

    A scope identifies one workspace, environment and source (plus financial
    basis for cost).  Sources that were unavailable must be omitted so a
    transient preview-table failure cannot erase their previous ledger rows.
    Empty rows for a declared scope are intentional and remove withdrawn
    records inside the exact inclusive date window.
    """

    start, end = _date_window(window_start, window_end)
    cost_scope_keys = _validated_scopes(
        cost_scopes,
        ("workspace_id", "environment", "source", "cost_basis"),
        "cost",
    )
    usage_scope_keys = _validated_scopes(
        usage_scopes,
        ("workspace_id", "environment", "source"),
        "usage",
    )
    _validate_rows_in_window(cost_rows, "usage_date", start, end, "cost")
    _validate_rows_in_window(usage_rows, "usage_hour", start, end, "usage")
    _validate_row_scopes(
        cost_rows,
        ("workspace_id", "environment", "source", "cost_basis"),
        cost_scope_keys,
        "cost",
    )
    _validate_row_scopes(
        usage_rows,
        ("workspace_id", "environment", "source"),
        usage_scope_keys,
        "usage",
    )

    try:
        run_query(
            w,
            f"DELETE FROM {catalog}.{schema}.llm_usage_hourly "
            "WHERE usage_hour < CURRENT_TIMESTAMP() - INTERVAL 90 DAYS",
            warehouse_id,
        )
        run_query(
            w,
            f"DELETE FROM {catalog}.{schema}.llm_cost_daily "
            "WHERE usage_date < DATE_SUB(CURRENT_DATE(), 400)",
            warehouse_id,
        )
        for scope in cost_scope_keys:
            scoped = [
                row
                for row in cost_rows
                if _row_scope(row, ("workspace_id", "environment", "source", "cost_basis")) == scope
            ]
            run_query(
                w,
                merge_cost_rows_sql(catalog, schema),
                warehouse_id,
                {
                    "rows": json.dumps(scoped, default=str),
                    "workspace_id": scope[0],
                    "environment": scope[1],
                    "source": scope[2],
                    "cost_basis": scope[3],
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                },
            )
        for scope in usage_scope_keys:
            scoped = [
                row
                for row in usage_rows
                if _row_scope(row, ("workspace_id", "environment", "source")) == scope
            ]
            run_query(
                w,
                merge_usage_rows_sql(catalog, schema),
                warehouse_id,
                {
                    "rows": json.dumps(scoped, default=str),
                    "workspace_id": scope[0],
                    "environment": scope[1],
                    "source": scope[2],
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                },
            )
    except Exception as exc:
        raise RuntimeError(
            f"Unable to reconcile required LLM ledger tables in {catalog}.{schema}; "
            "run the deployment schema_migrations job and verify writer grants."
        ) from exc
    return {"cost_rows": len(cost_rows), "usage_rows": len(usage_rows)}


def _date_window(window_start: str, window_end: str) -> tuple[date, date]:
    try:
        start = date.fromisoformat(str(window_start)[:10])
        end = date.fromisoformat(str(window_end)[:10])
    except ValueError as exc:
        raise ValueError("window_start and window_end must be ISO dates") from exc
    if start > end:
        raise ValueError("window_start must be on or before window_end")
    return start, end


def _row_scope(row: dict, fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(row.get(field) or "") for field in fields)


def _validated_scopes(
    scopes: list[dict[str, str]],
    fields: tuple[str, ...],
    label: str,
) -> list[tuple[str, ...]]:
    result: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for scope in scopes:
        key = _row_scope(scope, fields)
        if not all(key):
            raise ValueError(f"{label} reconciliation scope requires {', '.join(fields)}")
        if key not in seen:
            result.append(key)
            seen.add(key)
    return result


def _validate_rows_in_window(
    rows: list[dict],
    date_field: str,
    start: date,
    end: date,
    label: str,
) -> None:
    outside = []
    for row in rows:
        row_date = _coerce_date(row.get(date_field))
        if row_date is None or not start <= row_date <= end:
            outside.append(row.get(date_field))
    if outside:
        raise ValueError(f"{label} rows fall outside the reconciliation window: {outside[:3]}")


def _validate_row_scopes(
    rows: list[dict],
    fields: tuple[str, ...],
    scopes: list[tuple[str, ...]],
    label: str,
) -> None:
    allowed = set(scopes)
    undeclared = sorted({_row_scope(row, fields) for row in rows} - allowed)
    if undeclared:
        raise ValueError(f"{label} rows have undeclared reconciliation scopes: {undeclared[:3]}")


def normalize_cost_rows(
    rows: list[dict],
    source: str,
    basis: str,
    *,
    environment: str = "prod",
    workspace_id: str = "current",
) -> list[dict]:
    """Coerce query results into the canonical cost row shape."""

    if basis not in COST_BASES:
        raise ValueError(f"unsupported cost basis: {basis}")
    out: list[dict] = []
    for row in rows:
        cost = _number(row.get("cost"))
        # Never invent USD for a provider row whose billing currency is absent.
        # UNKNOWN remains a separate, visibly uncovered ledger bucket.
        currency = str(row.get("currency") or "UNKNOWN").upper()
        out.append(
            {
                "usage_date": _date_text(row.get("usage_date")),
                "workspace_id": workspace_id,
                "environment": environment,
                "provider": str(row.get("provider") or "unallocated"),
                "model": str(row.get("model") or "unallocated"),
                "endpoint": str(row.get("endpoint") or "unallocated"),
                "principal": str(row.get("principal") or "unallocated"),
                "team": str(row.get("team") or "unallocated"),
                "use_case": str(row.get("use_case") or "unallocated"),
                "cost": round(cost, 8),
                "currency": currency,
                "cost_basis": basis,
                "source": source,
            }
        )
    return out


def normalize_usage_rows(
    rows: list[dict],
    source: str,
    *,
    environment: str = "prod",
    workspace_id: str = "current",
) -> list[dict]:
    """Coerce usage results without manufacturing unavailable metrics."""

    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "usage_date": _date_text(row.get("usage_date")),
                "usage_hour": _hour_text(row.get("usage_hour") or row.get("usage_date")),
                "workspace_id": workspace_id,
                "environment": environment,
                "provider": str(row.get("provider") or "unallocated"),
                "model": str(row.get("model") or "unallocated"),
                "endpoint": str(row.get("endpoint") or "unallocated"),
                "principal": str(row.get("principal") or "unallocated"),
                "team": str(row.get("team") or "unallocated"),
                "use_case": str(row.get("use_case") or "unallocated"),
                "requests": _integer(row.get("requests")),
                "successful_requests": (
                    _integer(row.get("successful_requests"))
                    if row.get("successful_requests") is not None
                    else (
                        _integer(row.get("requests")) - _integer(row.get("errors"))
                        if row.get("errors") is not None
                        else None
                    )
                ),
                "invocations": _integer(row.get("invocations") or row.get("requests")),
                "input_tokens": _integer(row.get("input_tokens")),
                "output_tokens": _integer(row.get("output_tokens")),
                "cached_tokens": _integer_or_none(row.get("cached_tokens")),
                "reasoning_tokens": _integer_or_none(row.get("reasoning_tokens")),
                "errors": _integer_or_none(row.get("errors")),
                "retries": _integer_or_none(row.get("retries")),
                "p95_latency_ms": _number_or_none(row.get("p95_latency_ms")),
                "source": source,
            }
        )
    return out


def summarize(
    cost_rows: list[dict],
    usage_rows: list[dict],
    days: int,
    *,
    today: date | None = None,
) -> dict:
    """Build KPI totals without mixing cost bases or currencies."""

    today = today or date.today()
    month_start = today.replace(day=1)
    mtd_cost_rows = [
        row
        for row in cost_rows
        if month_start <= (_coerce_date(row.get("usage_date")) or date.min) <= today
    ]
    mtd_usage_rows = [
        row
        for row in usage_rows
        if month_start <= (_coerce_date(row.get("usage_date")) or date.min) <= today
    ]
    grouped_cost: dict[tuple[str, str], float] = defaultdict(float)
    for row in mtd_cost_rows:
        grouped_cost[(row["currency"], row["cost_basis"])] += _number(row.get("cost"))

    previous_month_end = month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    previous_through = previous_month_start + timedelta(
        days=min(today.day, previous_month_end.day) - 1
    )
    previous_grouped: dict[tuple[str, str], float] = defaultdict(float)
    for row in cost_rows:
        usage_date = _coerce_date(row.get("usage_date")) or date.min
        if previous_month_start <= usage_date <= previous_through:
            previous_grouped[(row["currency"], row["cost_basis"])] += _number(row.get("cost"))

    totals = []
    for (currency, basis), value in sorted(grouped_cost.items()):
        previous = previous_grouped.get((currency, basis), 0.0)
        delta_pct = ((value - previous) / previous * 100) if previous else None
        totals.append(
            {
                "currency": currency,
                "basis": basis,
                "cost_basis": basis,
                "cost": round(value, 2),
                "previous_period_cost": round(previous, 2),
                "period_delta_pct": round(delta_pct, 1) if delta_pct is not None else None,
                "comparison_from": previous_month_start.isoformat(),
                "comparison_to": previous_through.isoformat(),
            }
        )
    metrics = _usage_totals(mtd_usage_rows)
    forecasts = []
    month_days = calendar.monthrange(today.year, today.month)[1]
    elapsed = max(today.day, 1)
    for total in totals:
        month_end = total["cost"] / elapsed * month_days
        forecasts.append(
            {
                "currency": total["currency"],
                "basis": total["cost_basis"],
                "cost_basis": total["cost_basis"],
                "month_end": round(month_end, 2),
                # A transparent range rather than false model precision.
                "lower": round(month_end * 0.85, 2),
                "upper": round(month_end * 1.15, 2),
                "method": "month-to-date run rate",
            }
        )

    cost_per_request = None
    cost_per_successful_task = None
    cost_per_million_tokens = None
    if len(totals) == 1:
        amount = totals[0]["cost"]
        if metrics["requests"]:
            cost_per_request = round(amount / metrics["requests"], 6)
        if metrics["successful_requests"]:
            cost_per_successful_task = round(amount / metrics["successful_requests"], 6)
        tokens = _optional_sum(metrics["input_tokens"], metrics["output_tokens"])
        if tokens:
            cost_per_million_tokens = round(amount / tokens * 1_000_000, 2)

    return {
        "period": {
            "kind": "month-to-date",
            "requested_days": days,
            "days": today.day,
            "from": month_start.isoformat(),
            "to": today.isoformat(),
        },
        "totals": totals,
        **metrics,
        "cost_per_request": cost_per_request,
        "cost_per_successful_task": cost_per_successful_task,
        "cost_per_million_tokens": cost_per_million_tokens,
        "forecasts": forecasts,
        "forecast": forecasts[0] if len(forecasts) == 1 else None,
    }


def time_series(cost_rows: list[dict], usage_rows: list[dict]) -> list[dict]:
    """Return daily rows; finances remain separated by basis and currency."""

    groups: dict[tuple, dict[str, Any]] = {}
    for row in cost_rows:
        key = (
            row["usage_date"],
            row["provider"],
            row["model"],
            row["endpoint"],
            row["currency"],
            row["cost_basis"],
        )
        item = groups.setdefault(
            key,
            {
                "usage_date": row["usage_date"],
                "provider": row["provider"],
                "model": row["model"],
                "endpoint": row["endpoint"],
                "currency": row["currency"],
                "cost_basis": row["cost_basis"],
                "cost": 0.0,
                "requests": 0,
                "successful_requests": None,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": None,
                "reasoning_tokens": None,
            },
        )
        item["cost"] += _number(row.get("cost"))

    usage_by_day_provider: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in usage_rows:
        usage_by_day_provider[(row["usage_date"], row["provider"])].append(row)

    usage_metrics: dict[tuple[str, str], dict[str, int | None]] = {
        key: _aggregate_metrics(
            rows,
            (
                "requests",
                "successful_requests",
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "reasoning_tokens",
            ),
        )
        for key, rows in usage_by_day_provider.items()
    }

    # Usage cannot be allocated safely across several financial rows. Attach it
    # only when a day/provider has exactly one cost row; otherwise expose a
    # separate usage-only row.
    cost_keys_by_usage: dict[tuple[str, str], list[tuple]] = defaultdict(list)
    for key in groups:
        cost_keys_by_usage[(key[0], key[1])].append(key)
    for usage_key, metrics in usage_metrics.items():
        keys = cost_keys_by_usage.get(usage_key, [])
        if len(keys) == 1:
            groups[keys[0]].update(metrics)
            continue
        key = (*usage_key, "all", "all", "", "USAGE_ONLY")
        groups[key] = {
            "usage_date": usage_key[0],
            "provider": usage_key[1],
            "model": "all",
            "endpoint": "all",
            "currency": None,
            "cost_basis": "USAGE_ONLY",
            "cost": None,
            **metrics,
        }

    rows = list(groups.values())
    for row in rows:
        if row["cost"] is not None:
            row["cost"] = round(row["cost"], 8)
    return sorted(
        rows,
        key=lambda r: (
            r["usage_date"],
            r["provider"],
            str(r.get("model")),
            str(r.get("cost_basis")),
        ),
    )


def breakdown(cost_rows: list[dict], usage_rows: list[dict], dimension: str) -> list[dict]:
    """Aggregate by an allowlisted attribution dimension."""

    if dimension not in BREAKDOWN_DIMENSIONS:
        raise ValueError(f"dimension must be one of {sorted(BREAKDOWN_DIMENSIONS)}")

    cost_groups: dict[tuple, float] = defaultdict(float)
    for row in cost_rows:
        key = (
            str(row.get(dimension) or "unallocated"),
            row["currency"],
            row["cost_basis"],
        )
        cost_groups[key] += _number(row.get("cost"))

    usage_rows_by_key: dict[str, list[dict]] = defaultdict(list)
    for row in usage_rows:
        usage_rows_by_key[str(row.get(dimension) or "unallocated")].append(row)
    usage_groups = {
        key: _aggregate_metrics(
            rows,
            (
                "requests",
                "successful_requests",
                "input_tokens",
                "output_tokens",
                "cached_tokens",
                "reasoning_tokens",
                "errors",
                "retries",
            ),
        )
        for key, rows in usage_rows_by_key.items()
    }

    out = []
    keys = set(usage_groups) | {key[0] for key in cost_groups}
    for value in keys:
        financial = [(key, amount) for key, amount in cost_groups.items() if key[0] == value]
        if not financial:
            financial = [((value, None, "USAGE_ONLY"), 0.0)]
        for (_, currency, basis), amount in financial:
            item = {
                "dimension": dimension,
                "key": mask_identity(value) if dimension == "principal" else value,
                "cost": round(amount, 8) if basis != "USAGE_ONLY" else None,
                "currency": currency,
                "cost_basis": basis,
            }
            item.update(usage_groups.get(value, {}))
            out.append(item)
    return sorted(
        out,
        key=lambda r: (
            -(_number(r.get("cost"))),
            -_integer(r.get("input_tokens")) - _integer(r.get("output_tokens")),
            str(r["key"]),
        ),
    )


def efficiency(cost_rows: list[dict], usage_rows: list[dict]) -> dict:
    """Cross-pillar efficiency metrics and deterministic recommendations."""

    metrics = _usage_totals(usage_rows)
    total_invocations = _sum_metric(usage_rows, "invocations")
    latency_values = [
        _number(r.get("p95_latency_ms"))
        for r in usage_rows
        if r.get("p95_latency_ms") is not None and _number(r.get("p95_latency_ms")) > 0
    ]
    total_tokens = _optional_sum(metrics["input_tokens"], metrics["output_tokens"])
    cache_ratio = (
        metrics["cached_tokens"] / (metrics["cached_tokens"] + metrics["input_tokens"])
        if metrics["cached_tokens"] is not None
        and metrics["input_tokens"] is not None
        and metrics["cached_tokens"] + metrics["input_tokens"]
        else None
    )
    retry_rate = (
        metrics["retries"] / total_invocations
        if metrics["retries"] is not None and total_invocations
        else None
    )
    error_rate = (
        metrics["errors"] / total_invocations
        if metrics["errors"] is not None and total_invocations
        else None
    )

    recommendations: list[dict] = []
    if retry_rate is not None and retry_rate >= 0.05:
        recommendations.append(
            {
                "type": "retry-storm",
                "severity": "high" if retry_rate >= 0.15 else "medium",
                "evidence": f"{retry_rate:.1%} of invocations are retries",
                "action_type": "review-routing-and-retry-policy",
                "requires_approval": True,
            }
        )
    if (
        total_tokens is not None
        and total_tokens >= 100_000
        and cache_ratio is not None
        and cache_ratio < 0.10
    ):
        recommendations.append(
            {
                "type": "low-cache-use",
                "severity": "medium",
                "evidence": f"cache-read share is {cache_ratio:.1%}",
                "action_type": "evaluate-prompt-cache",
                "requires_approval": True,
            }
        )
    unallocated = sum(
        _number(r.get("cost"))
        for r in cost_rows
        if "unallocated"
        in {
            str(r.get("endpoint")),
            str(r.get("team")),
            str(r.get("use_case")),
        }
    )
    if unallocated:
        recommendations.append(
            {
                "type": "unallocated-spend",
                "severity": "medium",
                "evidence": "some spend is missing endpoint, team, or use-case attribution",
                "action_type": "add-ai-gateway-request-tags",
                "requires_approval": True,
            }
        )

    result = {
        **metrics,
        "total_tokens": total_tokens,
        "retry_rate": round(retry_rate, 4) if retry_rate is not None else None,
        "error_rate": round(error_rate, 4) if error_rate is not None else None,
        "cache_ratio": round(cache_ratio, 4) if cache_ratio is not None else None,
        "p95_latency_ms": max(latency_values) if latency_values else None,
        "recommendations": recommendations,
    }
    result["metrics"] = {key: value for key, value in result.items() if key != "recommendations"}
    return result


def coverage_record(
    source: str,
    status: str,
    *,
    freshness: str,
    retention_days: int | None,
    cost_basis: str | None = None,
    notes: str | None = None,
    source_key: str | None = None,
    source_type: str | None = None,
    coverage_start: Any | None = None,
    coverage_end: Any | None = None,
    row_count: int | None = None,
    available_metrics: list[str] | None = None,
    checked_at: Any | None = None,
    last_success_at: Any | None = None,
) -> dict:
    """Canonical source-health record returned to the frontend."""

    result = {
        "source": source,
        "status": status,
        "freshness": freshness,
        "retention_days": retention_days,
        "cost_basis": cost_basis,
    }
    optional = {
        "source_key": source_key,
        "source_type": source_type,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "row_count": row_count,
        "available_metrics": available_metrics,
        "checked_at": checked_at,
        "last_success_at": last_success_at,
        "notes": notes,
    }
    result.update({key: value for key, value in optional.items() if value is not None})
    return result


def mask_identity(value: str) -> str:
    """Stable pseudonym for requester identity in non-privileged views."""

    if not value or value == "unallocated":
        return "unallocated"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    kind = "user" if "@" in value else "principal"
    return f"{kind}-{digest}"


def _usage_totals(rows: list[dict]) -> dict[str, int | None]:
    fields = (
        "requests",
        "successful_requests",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "errors",
        "retries",
    )
    return _aggregate_metrics(rows, fields)


def _aggregate_metrics(rows: list[dict], fields: tuple[str, ...]) -> dict[str, int | None]:
    return {field: _sum_metric(rows, field) for field in fields}


def _sum_metric(rows: list[dict], field: str) -> int | None:
    values = [row.get(field) for row in rows if row.get(field) is not None]
    return sum(_integer(value) for value in values) if values else None


def _optional_sum(*values: int | None) -> int | None:
    if any(value is None for value in values):
        return None
    return sum(_integer(value) for value in values)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _number_or_none(value: Any) -> float | None:
    return None if value is None else _number(value)


def _integer_or_none(value: Any) -> int | None:
    return None if value is None else _integer(value)


def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return str(value or "")


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "")
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _hour_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return f"{value.isoformat()}T00:00:00+00:00"
    text = str(value or "")
    if len(text) == 10:
        return f"{text}T00:00:00+00:00"
    return text


def _platform_table(catalog: str, schema: str, table: str) -> str:
    """Return a quoted platform table name after strict identifier checks."""

    if not all(_IDENTIFIER_RE.fullmatch(value) for value in (catalog, schema, table)):
        raise ValueError("Unsafe Unity Catalog ledger identifier.")
    return f"`{catalog}`.`{schema}`.`{table}`"


def read_llm_cost_daily(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    workspace_id: str,
    environment: str,
    days: int,
) -> list[dict]:
    """Read only the persisted, exact-scope daily financial ledger."""

    lookback_days = max(0, min(int(days), 400) - 1)
    return run_query(
        w,
        "SELECT usage_date, workspace_id, environment, provider, model, "
        "endpoint, principal, team, use_case, cost, currency, cost_basis, "
        "source, ingested_at "
        f"FROM {_platform_table(catalog, schema, 'llm_cost_daily')} "
        "WHERE workspace_id = :workspace_id "
        "AND environment = :environment "
        "AND usage_date >= DATE_SUB(CURRENT_DATE(), :lookback_days) "
        "ORDER BY usage_date, provider, model, endpoint, currency, cost_basis",
        warehouse_id,
        {
            "workspace_id": workspace_id,
            "environment": environment,
            "lookback_days": lookback_days,
        },
        row_limit=100_000,
    )


def read_llm_usage_hourly(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    workspace_id: str,
    environment: str,
    days: int,
) -> list[dict]:
    """Read persisted hourly usage without manufacturing missing metrics."""

    lookback_days = max(0, min(int(days), 90) - 1)
    return run_query(
        w,
        "SELECT usage_hour, CAST(usage_hour AS DATE) AS usage_date, "
        "workspace_id, environment, provider, model, endpoint, principal, "
        "team, use_case, requests, successful_requests, invocations, "
        "input_tokens, output_tokens, cached_tokens, reasoning_tokens, "
        "errors, retries, p95_latency_ms, source, ingested_at "
        f"FROM {_platform_table(catalog, schema, 'llm_usage_hourly')} "
        "WHERE workspace_id = :workspace_id "
        "AND environment = :environment "
        "AND usage_hour >= CAST("
        "DATE_SUB(CURRENT_DATE(), :lookback_days) AS TIMESTAMP) "
        "ORDER BY usage_hour, provider, model, endpoint",
        warehouse_id,
        {
            "workspace_id": workspace_id,
            "environment": environment,
            "lookback_days": lookback_days,
        },
        row_limit=100_000,
    )


def read_llm_source_health(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    workspace_id: str,
    environment: str,
) -> list[dict]:
    """Read the last feature-detection result for each scheduled source."""

    rows = run_query(
        w,
        "SELECT source_key, source, source_type, status, cost_basis, freshness, "
        "retention_days, coverage_start, coverage_end, row_count, "
        "available_metrics_json, notes, checked_at, last_success_at "
        f"FROM {_platform_table(catalog, schema, 'llm_source_health')} "
        "WHERE workspace_id = :workspace_id "
        "AND environment = :environment "
        "ORDER BY source_type, source, cost_basis",
        warehouse_id,
        {"workspace_id": workspace_id, "environment": environment},
        row_limit=100,
    )
    result: list[dict] = []
    for row in rows:
        item = dict(row)
        raw_metrics = item.pop("available_metrics_json", None)
        try:
            item["available_metrics"] = json.loads(str(raw_metrics)) if raw_metrics else []
        except (TypeError, json.JSONDecodeError):
            item["available_metrics"] = None
        result.append(item)
    return result


def merge_source_health_sql(catalog: str, schema: str) -> str:
    """Atomic upsert for one rollup's source-health snapshot."""

    table = _platform_table(catalog, schema, "llm_source_health")
    return (
        f"MERGE INTO {table} t USING ("
        "SELECT item.workspace_id, item.environment, item.source_key, "
        "item.source, item.source_type, item.status, "
        "NULLIF(item.cost_basis, '') AS cost_basis, item.freshness, "
        "item.retention_days, "
        "CAST(NULLIF(item.coverage_start, '') AS DATE) AS coverage_start, "
        "CAST(NULLIF(item.coverage_end, '') AS DATE) AS coverage_end, "
        "item.row_count, NULLIF(item.available_metrics_json, '') "
        "AS available_metrics_json, NULLIF(item.notes, '') AS notes, "
        "CAST(item.checked_at AS TIMESTAMP) AS checked_at "
        f"FROM (SELECT explode(from_json(:rows, '{SOURCE_HEALTH_ROW_SCHEMA}')) item)"
        ") s ON t.workspace_id = s.workspace_id "
        "AND t.environment = s.environment AND t.source_key = s.source_key "
        "WHEN MATCHED THEN UPDATE SET t.source = s.source, "
        "t.source_type = s.source_type, t.status = s.status, "
        "t.cost_basis = s.cost_basis, t.freshness = s.freshness, "
        "t.retention_days = s.retention_days, "
        "t.coverage_start = s.coverage_start, t.coverage_end = s.coverage_end, "
        "t.row_count = s.row_count, "
        "t.available_metrics_json = s.available_metrics_json, "
        "t.notes = s.notes, t.checked_at = s.checked_at, "
        "t.last_success_at = CASE WHEN s.status IN ('available', 'partial') "
        "THEN s.checked_at ELSE t.last_success_at END "
        "WHEN NOT MATCHED THEN INSERT (workspace_id, environment, source_key, "
        "source, source_type, status, cost_basis, freshness, retention_days, "
        "coverage_start, coverage_end, row_count, available_metrics_json, "
        "notes, checked_at, last_success_at) VALUES (s.workspace_id, "
        "s.environment, s.source_key, s.source, s.source_type, s.status, "
        "s.cost_basis, s.freshness, s.retention_days, s.coverage_start, "
        "s.coverage_end, s.row_count, s.available_metrics_json, s.notes, "
        "s.checked_at, CASE WHEN s.status IN ('available', 'partial') "
        "THEN s.checked_at ELSE NULL END)"
    )


def store_source_health(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    records: list[dict],
    *,
    workspace_id: str,
    environment: str,
) -> int:
    """Persist feature detection, freshness and coverage even for zero rows."""

    if not workspace_id or not environment:
        raise ValueError("Source health requires workspace_id and environment.")
    now = datetime.now(UTC).isoformat()
    normalized: list[dict] = []
    seen: set[str] = set()
    for record in records:
        source = str(record.get("source") or "").strip()
        source_key = str(record.get("source_key") or source).strip()
        status = str(record.get("status") or "").strip().lower()
        cost_basis = str(record.get("cost_basis") or "").strip()
        if not source or not source_key:
            raise ValueError("Source health requires source and source_key.")
        if source_key in seen:
            raise ValueError(f"Duplicate source health key: {source_key}")
        if status not in _SOURCE_STATUSES:
            raise ValueError(f"Unsupported source health status: {status}")
        if cost_basis and cost_basis not in COST_BASES:
            raise ValueError(f"unsupported cost basis: {cost_basis}")
        row_count = _integer(record.get("row_count"))
        if row_count < 0:
            raise ValueError("Source health row_count cannot be negative.")
        metrics = record.get("available_metrics")
        normalized.append(
            {
                "workspace_id": workspace_id,
                "environment": environment,
                "source_key": source_key,
                "source": source,
                "source_type": str(record.get("source_type") or "usage"),
                "status": status,
                "cost_basis": cost_basis,
                "freshness": str(record.get("freshness") or "unknown"),
                "retention_days": (
                    _integer(record["retention_days"])
                    if record.get("retention_days") is not None
                    else None
                ),
                "coverage_start": _date_text(record.get("coverage_start")),
                "coverage_end": _date_text(record.get("coverage_end")),
                "row_count": row_count,
                "available_metrics_json": (
                    json.dumps(metrics, sort_keys=True) if metrics is not None else ""
                ),
                "notes": str(record.get("notes") or ""),
                "checked_at": str(record.get("checked_at") or now),
            }
        )
        seen.add(source_key)
    if not normalized:
        return 0
    run_query(
        w,
        merge_source_health_sql(catalog, schema),
        warehouse_id,
        {"rows": json.dumps(normalized, sort_keys=True)},
    )
    return len(normalized)
