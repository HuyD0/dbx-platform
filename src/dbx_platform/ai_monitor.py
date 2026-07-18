"""Centralized production AI app monitoring from serving system tables.

Rolls per-request telemetry up to one daily grain — per endpoint, per calling
app — so every production AI workload is visible in one table and dashboard:

* ``system.serving.endpoint_usage`` (primary; rows exist only for endpoints
  with usage tracking enabled, 90-day retention) joined to
  ``system.serving.served_entities``. App attribution comes from the
  caller-supplied ``usage_context`` map.
* ``system.ai_gateway.usage`` (Beta; feature-detected) adds latency and
  request-tag attribution. Kept as a separate ``source`` so request counts
  are never double-counted across the two tables.

Findings are evidence-based and deliberately distinct from the config-based
``ml/endpoint-audit`` checks: an error-rate spike against the trailing
average, endpoints that bill serving cost while emitting zero usage telemetry
(monitoring blind spots), and idle endpoints (delegated to
``ml.find_stale_endpoints``).

Fetch/classify split as everywhere else: the two query wrappers and the
store/read helpers are the only I/O; classification and SQL construction are
pure and unit-tested offline.
"""

from __future__ import annotations

import json
from datetime import date

from databricks.sdk import WorkspaceClient

from dbx_platform import ml
from dbx_platform.system_tables import load_query, run_query

ENDPOINT_USAGE_SOURCE = "system.serving.endpoint_usage"
GATEWAY_USAGE_SOURCE = "system.ai_gateway.usage"
SOURCES = (ENDPOINT_USAGE_SOURCE, GATEWAY_USAGE_SOURCE)

RETENTION_DAYS = 400

MONITOR_ROW_SCHEMA = (
    "array<struct<usage_date:date,endpoint_name:string,app:string,"
    "entity_name:string,entity_type:string,requests:bigint,errors:bigint,"
    "server_errors:bigint,input_tokens:bigint,output_tokens:bigint,"
    "distinct_requesters:bigint,p95_latency_ms:double,source:string>>"
)


def endpoint_usage_daily(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    """Daily per-endpoint/app usage from the serving usage system table."""
    return run_query(w, load_query("ai_endpoint_usage_daily"), warehouse_id, {"days": days})


def gateway_usage_daily(w: WorkspaceClient, warehouse_id: str, days: int) -> list[dict]:
    """Daily per-endpoint/app usage from Unity AI Gateway (Beta)."""
    return run_query(w, load_query("ai_gateway_usage_daily"), warehouse_id, {"days": days})


# --- storage (Delta via the SQL warehouse) --------------------------------------

def create_monitoring_table_sql(catalog: str, schema: str) -> str:
    """DDL for the ai_app_monitoring table. Pure."""
    return (
        f"CREATE TABLE IF NOT EXISTS {catalog}.{schema}.ai_app_monitoring ("
        "usage_date DATE, workspace_id STRING, environment STRING, "
        "endpoint_name STRING, app STRING, entity_name STRING, "
        "entity_type STRING, requests BIGINT, errors BIGINT, "
        "server_errors BIGINT, input_tokens BIGINT, output_tokens BIGINT, "
        "distinct_requesters BIGINT, p95_latency_ms DOUBLE, source STRING, "
        "ingested_at TIMESTAMP) "
        "COMMENT 'Daily per-endpoint/app AI serving usage; sources kept "
        f"separate; retain {RETENTION_DAYS} days'"
    )


_MONITOR_DIMENSIONS = (
    "workspace_id", "environment", "endpoint_name", "app", "entity_name",
    "entity_type", "source",
)
_MONITOR_METRICS = (
    "requests", "errors", "server_errors", "input_tokens", "output_tokens",
    "distinct_requesters", "p95_latency_ms",
)


def merge_monitoring_sql(catalog: str, schema: str) -> str:
    """Atomically reconcile one exact workspace/environment/source window."""
    fq = f"{catalog}.{schema}.ai_app_monitoring"
    match = " AND ".join(f"t.{c} = s.{c}" for c in _MONITOR_DIMENSIONS)
    updates = ", ".join(f"t.{c} = s.{c}" for c in _MONITOR_METRICS)
    columns = ("usage_date", *_MONITOR_DIMENSIONS, *_MONITOR_METRICS)
    return (
        f"MERGE INTO {fq} t USING ("
        "SELECT :workspace_id AS workspace_id, :environment AS environment, item.* "
        f"FROM (SELECT explode(from_json(:rows, '{MONITOR_ROW_SCHEMA}')) AS item)"
        f") s ON t.usage_date = s.usage_date AND {match} "
        f"WHEN MATCHED THEN UPDATE SET {updates}, t.ingested_at = current_timestamp() "
        f"WHEN NOT MATCHED THEN INSERT ({', '.join(columns)}, ingested_at) "
        f"VALUES ({', '.join(f's.{c}' for c in columns)}, current_timestamp()) "
        "WHEN NOT MATCHED BY SOURCE AND t.workspace_id = :workspace_id "
        "AND t.environment = :environment AND t.source = :source "
        "AND t.usage_date BETWEEN CAST(:window_start AS DATE) "
        "AND CAST(:window_end AS DATE) THEN DELETE"
    )


def retention_delete_sql(catalog: str, schema: str) -> str:
    """Age out rows beyond the retention window. Pure."""
    return (
        f"DELETE FROM {catalog}.{schema}.ai_app_monitoring "
        f"WHERE usage_date < DATE_SUB(CURRENT_DATE(), {RETENTION_DAYS})"
    )


def _window_params(
    rows: list[dict],
    workspace_id: str,
    environment: str,
    window_start: str,
    window_end: str,
    sources: list[str],
) -> tuple[dict[str, list[dict]], str, str]:
    """Validate the inclusive window + declared sources; split rows. Pure."""
    if not workspace_id.strip() or not environment.strip():
        raise ValueError(
            "workspace_id and environment are required for monitoring reconciliation"
        )
    if not sources:
        raise ValueError("at least one refreshed source is required")
    unknown = sorted(set(sources) - set(SOURCES))
    if unknown:
        raise ValueError(f"unknown monitoring sources: {unknown}")
    try:
        start = date.fromisoformat(str(window_start)[:10])
        end = date.fromisoformat(str(window_end)[:10])
    except ValueError as exc:
        raise ValueError("window_start and window_end must be ISO dates") from exc
    if start > end:
        raise ValueError("window_start must be on or before window_end")
    outside = [
        row.get("usage_date")
        for row in rows
        if not start <= date.fromisoformat(str(row.get("usage_date"))[:10]) <= end
    ]
    if outside:
        raise ValueError(
            f"usage rows fall outside the reconciliation window: {outside[:3]}"
        )
    undeclared = sorted({str(row.get("source")) for row in rows} - set(sources))
    if undeclared:
        raise ValueError(
            f"rows belong to sources that were not declared as refreshed: {undeclared}"
        )
    by_source = {
        source: [r for r in rows if r.get("source") == source] for source in sources
    }
    return by_source, start.isoformat(), end.isoformat()


def store_monitoring(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    rows: list[dict],
    *,
    workspace_id: str,
    environment: str,
    window_start: str,
    window_end: str,
    sources: list[str],
) -> int:
    """Atomically replace the exact usage window, one MERGE per source.

    Only sources that actually refreshed may be declared — an unavailable
    Beta table must never erase its previous rows.
    """
    by_source, start, end = _window_params(
        rows, workspace_id, environment, window_start, window_end, sources
    )
    try:
        run_query(w, retention_delete_sql(catalog, schema), warehouse_id)
        for source, scoped in by_source.items():
            run_query(
                w,
                merge_monitoring_sql(catalog, schema),
                warehouse_id,
                {
                    "rows": json.dumps(scoped, default=str),
                    "workspace_id": workspace_id,
                    "environment": environment,
                    "source": source,
                    "window_start": start,
                    "window_end": end,
                },
            )
    except Exception as exc:
        raise RuntimeError(
            f"Unable to reconcile required table "
            f"{catalog}.{schema}.ai_app_monitoring; run the deployment "
            "schema_migrations job and verify writer grants."
        ) from exc
    return len(rows)


def read_monitoring(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    workspace_id: str,
    environment: str,
    days: int,
) -> list[dict]:
    fq = f"{catalog}.{schema}.ai_app_monitoring"
    return run_query(
        w,
        f"SELECT * FROM {fq} WHERE workspace_id = :workspace_id "
        "AND environment = :environment "
        "AND usage_date >= DATE_SUB(CURRENT_DATE(), :days) "
        "ORDER BY usage_date, endpoint_name, app",
        warehouse_id,
        {"workspace_id": workspace_id, "environment": environment, "days": days},
    )


def report_sql(catalog: str, schema: str) -> str:
    """Per-app usage/error aggregates over the :days window. Pure."""
    fq = f"{catalog}.{schema}.ai_app_monitoring"
    return (
        "SELECT app, endpoint_name, source, SUM(requests) AS requests, "
        "SUM(errors) AS errors, "
        "ROUND(SUM(errors) / GREATEST(SUM(requests), 1), 4) AS error_rate, "
        "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
        "MAX(distinct_requesters) AS peak_daily_requesters, "
        "MAX(p95_latency_ms) AS worst_p95_latency_ms, "
        "MIN(usage_date) AS first_day, MAX(usage_date) AS last_day "
        f"FROM {fq} WHERE workspace_id = :workspace_id "
        "AND environment = :environment "
        "AND usage_date >= DATE_SUB(CURRENT_DATE(), :days) "
        "GROUP BY app, endpoint_name, source ORDER BY requests DESC"
    )


def report(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    workspace_id: str,
    environment: str,
    days: int,
) -> list[dict]:
    return run_query(
        w,
        report_sql(catalog, schema),
        warehouse_id,
        {"workspace_id": workspace_id, "environment": environment, "days": days},
    )


# --- findings (pure) ------------------------------------------------------------

def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def classify_ai_monitoring(
    daily_rows: list[dict],
    endpoints: list[dict],
    cost_rows: list[dict],
    now_ms: int,
    *,
    spike_pct: int,
    min_requests: int,
    min_error_rate_pct: int,
    stale_days: int,
) -> dict[str, list[dict]]:
    """Pure decision logic over the daily usage rollup.

    - ``error-rate-spike``: per (endpoint, app), the latest *closed* day's
      error rate against the trailing up-to-7-closed-day average (the newest
      day in the data is treated as partial, like classify_azure_spend).
    - ``usage-tracking-gap``: endpoint bills serving cost but emitted zero
      usage rows — traffic is billed while monitoring is blind. Evidence-
      based, distinct from ml/endpoint-audit's config check.
    - ``idle-endpoint``: delegated to ml.find_stale_endpoints.
    """
    serving_rows = [r for r in daily_rows if r.get("source") == ENDPOINT_USAGE_SOURCE]

    spikes: list[dict] = []
    by_group: dict[tuple[str, str], dict[str, dict[str, float]]] = {}
    dates: set[str] = set()
    for r in serving_rows:
        day = str(r.get("usage_date", ""))
        endpoint = str(r.get("endpoint_name", ""))
        if not day or not endpoint:
            continue
        dates.add(day)
        group = by_group.setdefault((endpoint, str(r.get("app", ""))), {})
        daily = group.setdefault(day, {"requests": 0.0, "errors": 0.0})
        daily["requests"] += _num(r.get("requests"))
        daily["errors"] += _num(r.get("errors"))
    ordered = sorted(dates)
    if len(ordered) >= 3:
        latest_closed = ordered[-2]
        window = [d for d in ordered if d < latest_closed][-7:]
        for (endpoint, app), daily in sorted(by_group.items()):
            latest = daily.get(latest_closed, {"requests": 0.0, "errors": 0.0})
            if latest["requests"] < min_requests:
                continue
            rate = latest["errors"] / latest["requests"]
            if rate * 100 < min_error_rate_pct:
                continue
            base_rates = [
                daily[d]["errors"] / daily[d]["requests"]
                for d in window
                if d in daily and daily[d]["requests"] > 0
            ]
            baseline = sum(base_rates) / len(base_rates) if base_rates else 0.0
            if baseline > 0 and rate < baseline * (1 + spike_pct / 100):
                continue
            spikes.append(
                {
                    "name": f"{endpoint}/{app}",
                    "resource_id": endpoint,
                    "endpoint_name": endpoint,
                    "app": app,
                    "resource_type": "SERVING_ENDPOINT",
                    "day": latest_closed,
                    "requests": int(latest["requests"]),
                    "errors": int(latest["errors"]),
                    "error_rate": round(rate, 4),
                    "trailing_avg_rate": round(baseline, 4),
                    "reason": f"error rate {rate:.1%} on {latest_closed} "
                              f"({int(latest['errors'])}/{int(latest['requests'])} "
                              f"requests) vs trailing avg {baseline:.1%} "
                              f"(threshold {min_error_rate_pct}% and "
                              f"+{spike_pct}%)",
                    "action": "investigate-error-spike",
                    "severity": "HIGH",
                    "freshness_at": latest_closed,
                }
            )
        spikes.sort(key=lambda f: f["error_rate"], reverse=True)

    tracked = {str(r.get("endpoint_name")) for r in serving_rows}
    billed: dict[str, float] = {}
    for r in cost_rows:
        endpoint = str(r.get("endpoint_name") or "")
        cost = _num(r.get("list_cost_usd"))
        if endpoint and cost > 0:
            billed[endpoint] = billed.get(endpoint, 0.0) + cost
    gaps: list[dict] = []
    by_name = {e.get("name"): e for e in endpoints}
    for endpoint, cost in sorted(billed.items(), key=lambda kv: kv[1], reverse=True):
        meta = by_name.get(endpoint)
        if meta is None or meta.get("is_system_endpoint"):
            continue
        if endpoint in tracked:
            continue
        gaps.append(
            {
                "name": endpoint,
                "resource_id": endpoint,
                "resource_type": "SERVING_ENDPOINT",
                "creator": meta.get("creator", ""),
                "list_cost_usd": round(cost, 2),
                "reason": f"billed {cost:.2f} USD serving cost in the window but "
                          "emitted zero usage-tracking rows — production traffic "
                          "is not observable",
                "action": "enable-usage-tracking (manual)",
                "severity": "MEDIUM",
            }
        )

    # With zero usage telemetry, "idle" is indistinguishable from "untracked":
    # only assess idleness when the usage source produced rows, and never
    # double-flag an endpoint already reported as a tracking gap.
    gap_names = {f["name"] for f in gaps}
    idle = (
        [
            {**f, "severity": "LOW", "resource_type": "SERVING_ENDPOINT"}
            for f in ml.find_stale_endpoints(endpoints, serving_rows, now_ms, stale_days)
            if f["name"] not in gap_names
        ]
        if serving_rows
        else []
    )

    return {
        "ai-monitor/error-rate-spike": spikes,
        "ai-monitor/usage-tracking-gap": gaps,
        "ai-monitor/idle-endpoint": idle,
    }
