"""Unified, scope-safe Azure and Databricks cost presentation.

Azure Cost Management ActualCost is the authoritative platform total.
Databricks system billing is deliberately exposed as a separate LIST-cost
workload driver and is never added to the Azure total.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import run_query


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def service_category(service_name: str, service_bucket: str = "") -> str:
    """Normalize changing Azure service names into stable FinOps categories."""

    value = f"{service_name} {service_bucket}".lower()
    if "databricks" in value:
        return "Databricks"
    if any(word in value for word in ("virtual machine", "compute", "container", "kubernetes")):
        return "Compute"
    if "storage" in value:
        return "Storage"
    if any(word in value for word in ("network", "bandwidth", "nat gateway", "load balancer")):
        return "Network"
    if any(word in value for word in ("openai", "cognitive", "foundry", "machine learning")):
        return "AI / Foundry"
    if "search" in value:
        return "Search"
    if any(word in value for word in ("monitor", "log analytics", "application insights")):
        return "Monitoring"
    return "Other"


def scoped_daily_sql(catalog: str, schema: str, resource_groups: list[str]) -> tuple[str, dict]:
    """Build a bound, deployment-scoped read over the Azure actual-cost ledger."""

    if not resource_groups:
        raise ValueError("At least one Azure cost resource group must be configured.")
    placeholders = ", ".join(f":resource_group_{index}" for index in range(len(resource_groups)))
    params = {
        f"resource_group_{index}": resource_group
        for index, resource_group in enumerate(resource_groups)
    }
    sql = (
        "SELECT usage_date, service_name, resource_group, service_bucket, "
        "cost, currency, ingested_at "
        f"FROM {catalog}.{schema}.azure_costs "
        "WHERE workspace_id = :workspace_id AND environment = :environment "
        f"AND LOWER(resource_group) IN ({placeholders}) "
        "AND usage_date >= DATE_SUB(CURRENT_DATE(), :history_days) "
        "ORDER BY usage_date, service_name, resource_group"
    )
    return sql, params


def fetch_scoped_daily(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    *,
    workspace_id: str,
    environment: str,
    resource_groups: list[str],
    days: int,
) -> list[dict]:
    sql, params = scoped_daily_sql(
        catalog,
        schema,
        [group.lower() for group in resource_groups],
    )
    params.update(
        {
            "workspace_id": workspace_id,
            "environment": environment,
            "history_days": days * 2 + 8,
        }
    )
    return run_query(w, sql, warehouse_id, params)


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _pct(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100, 1)


def _severity(change_pct: float, cost: float, minimum: float) -> str:
    if change_pct >= 150 and cost >= minimum * 3:
        return "critical"
    if change_pct >= 80 and cost >= minimum * 2:
        return "serious"
    return "warning"


def _daily_anomalies(
    points: list[dict],
    *,
    spike_pct: int,
    spike_min_cost: float,
    acceleration_pct: int,
    acceleration_min_cost: float,
) -> list[dict]:
    by_series: dict[tuple[str, str], dict[date, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    all_dates: set[date] = set()
    for point in points:
        usage_date = _date(point.get("usage_date"))
        if usage_date is None:
            continue
        all_dates.add(usage_date)
        by_series[
            (str(point.get("category") or "Other"), str(point.get("currency") or "UNKNOWN"))
        ][usage_date] += _num(point.get("cost"))
    ordered_dates = sorted(all_dates)
    if len(ordered_dates) < 3:
        return []
    closed_dates = ordered_dates[:-1]
    findings: list[dict] = []
    for (category, currency), daily in sorted(by_series.items()):
        for index, usage_date in enumerate(closed_dates):
            baseline_dates = closed_dates[max(0, index - 7):index]
            if len(baseline_dates) < 3:
                continue
            actual = daily.get(usage_date, 0.0)
            baseline = sum(daily.get(day, 0.0) for day in baseline_dates) / len(baseline_dates)
            change_pct = _pct(actual, baseline)
            if (
                actual >= spike_min_cost
                and change_pct is not None
                and change_pct >= spike_pct
            ):
                findings.append(
                    {
                        "id": f"{usage_date.isoformat()}:{category}:{currency}:daily-spike",
                        "signal": "Daily spike",
                        "day": usage_date.isoformat(),
                        "category": category,
                        "currency": currency,
                        "cost_basis": "AZURE_ACTUAL",
                        "cost": round(actual, 2),
                        "baseline": round(baseline, 2),
                        "change_pct": change_pct,
                        "severity": _severity(change_pct, actual, spike_min_cost),
                        "reason": (
                            f"{category} was {change_pct:.0f}% above its trailing "
                            "7-day daily average."
                        ),
                    }
                )

        if len(closed_dates) >= 14:
            recent_dates = closed_dates[-7:]
            prior_dates = closed_dates[-14:-7]
            recent = sum(daily.get(day, 0.0) for day in recent_dates)
            prior = sum(daily.get(day, 0.0) for day in prior_dates)
            change_pct = _pct(recent, prior)
            if (
                recent >= acceleration_min_cost
                and change_pct is not None
                and change_pct >= acceleration_pct
            ):
                usage_date = recent_dates[-1]
                findings.append(
                    {
                        "id": f"{usage_date.isoformat()}:{category}:{currency}:acceleration",
                        "signal": "7-day acceleration",
                        "day": usage_date.isoformat(),
                        "category": category,
                        "currency": currency,
                        "cost_basis": "AZURE_ACTUAL",
                        "cost": round(recent, 2),
                        "baseline": round(prior, 2),
                        "change_pct": change_pct,
                        "severity": _severity(
                            change_pct,
                            recent,
                            acceleration_min_cost,
                        ),
                        "reason": (
                            f"{category}'s latest 7 days accelerated {change_pct:.0f}% "
                            "versus the prior 7 days."
                        ),
                    }
                )
    findings.sort(
        key=lambda finding: (
            {"critical": 3, "serious": 2, "warning": 1}.get(
                str(finding["severity"]), 0
            ),
            _num(finding["cost"]),
        ),
        reverse=True,
    )
    return findings


def build_overview(
    azure_rows: list[dict],
    databricks_rows: list[dict],
    *,
    days: int,
    workspace_id: str,
    environment: str,
    resource_groups: list[str],
    spike_pct: int = 50,
    spike_min_cost: float = 10,
    acceleration_pct: int = 30,
    acceleration_min_cost: float = 25,
    databricks_error: str | None = None,
) -> dict:
    """Compose the cost-control contract without mixing currencies or bases."""

    normalized: list[dict] = []
    for row in azure_rows:
        usage_date = _date(row.get("usage_date"))
        if usage_date is None:
            continue
        service_name = str(row.get("service_name") or "Unknown service")
        normalized.append(
            {
                "usage_date": usage_date.isoformat(),
                "service_name": service_name,
                "resource_group": str(row.get("resource_group") or "Unallocated"),
                "category": service_category(
                    service_name,
                    str(row.get("service_bucket") or ""),
                ),
                "cost": _num(row.get("cost")),
                "currency": str(row.get("currency") or "UNKNOWN"),
                "cost_basis": "AZURE_ACTUAL",
                "ingested_at": row.get("ingested_at"),
            }
        )

    latest = max((_date(row["usage_date"]) for row in normalized), default=None)
    current_start = latest - timedelta(days=days - 1) if latest else None
    previous_start = current_start - timedelta(days=days) if current_start else None
    previous_end = current_start - timedelta(days=1) if current_start else None
    current = [
        row
        for row in normalized
        if current_start and _date(row["usage_date"]) and _date(row["usage_date"]) >= current_start
    ]
    previous = [
        row
        for row in normalized
        if previous_start
        and previous_end
        and _date(row["usage_date"])
        and previous_start <= _date(row["usage_date"]) <= previous_end
    ]

    totals_by_currency: dict[str, float] = defaultdict(float)
    previous_by_currency: dict[str, float] = defaultdict(float)
    for row in current:
        totals_by_currency[row["currency"]] += row["cost"]
    for row in previous:
        previous_by_currency[row["currency"]] += row["cost"]
    totals = [
        {
            "currency": currency,
            "cost": round(cost, 2),
            "previous_period_cost": round(previous_by_currency.get(currency, 0.0), 2),
            "period_delta_pct": _pct(cost, previous_by_currency.get(currency, 0.0)),
            "cost_basis": "AZURE_ACTUAL",
        }
        for currency, cost in sorted(
            totals_by_currency.items(), key=lambda item: item[1], reverse=True
        )
    ]

    component_totals: dict[tuple[str, str], float] = defaultdict(float)
    category_totals: dict[tuple[str, str], float] = defaultdict(float)
    ownership_totals: dict[tuple[str, str], float] = defaultdict(float)
    previous_categories: dict[tuple[str, str], float] = defaultdict(float)
    for row in current:
        component = (
            "Azure Databricks"
            if row["category"] == "Databricks"
            else "Other Azure infrastructure"
        )
        component_totals[(component, row["currency"])] += row["cost"]
        category_totals[(row["category"], row["currency"])] += row["cost"]
        ownership_totals[(row["resource_group"], row["currency"])] += row["cost"]
    for row in previous:
        previous_categories[(row["category"], row["currency"])] += row["cost"]

    def shares(source: dict[tuple[str, str], float], key_name: str) -> list[dict]:
        result = []
        for (key, currency), cost in source.items():
            total = totals_by_currency.get(currency, 0.0)
            result.append(
                {
                    key_name: key,
                    "cost": round(cost, 2),
                    "currency": currency,
                    "cost_basis": "AZURE_ACTUAL",
                    "share_pct": round(cost / total * 100, 1) if total else 0,
                }
            )
        return sorted(result, key=lambda row: row["cost"], reverse=True)

    categories = shares(category_totals, "category")
    movers = []
    for row in categories:
        key = (row["category"], row["currency"])
        previous_cost = previous_categories.get(key, 0.0)
        movers.append(
            {
                **row,
                "previous_cost": round(previous_cost, 2),
                "change": round(row["cost"] - previous_cost, 2),
                "change_pct": _pct(row["cost"], previous_cost),
            }
        )
    movers.sort(key=lambda row: abs(_num(row["change"])), reverse=True)

    daily_totals: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in current:
        daily_totals[(row["usage_date"], row["category"], row["currency"])] += row["cost"]
    series = [
        {
            "usage_date": usage_date,
            "category": category,
            "currency": currency,
            "cost": round(cost, 4),
            "cost_basis": "AZURE_ACTUAL",
        }
        for (usage_date, category, currency), cost in sorted(daily_totals.items())
    ]

    scoped_databricks_rows = [
        row
        for row in databricks_rows
        if not row.get("workspace_id")
        or str(row.get("workspace_id")) == str(workspace_id)
    ]
    databricks_total = sum(
        _num(row.get("list_cost_usd")) for row in scoped_databricks_rows
    )
    databricks_driver = {
        "cost": round(databricks_total, 2),
        "currency": "USD",
        "cost_basis": "DATABRICKS_LIST",
        "additive_to_total": False,
        "rows": scoped_databricks_rows,
        "status": "degraded" if databricks_error else "healthy",
        "notes": databricks_error
        or "Workload attribution signal only; already represented in Azure actuals.",
    }

    anomalies = _daily_anomalies(
        normalized,
        spike_pct=spike_pct,
        spike_min_cost=spike_min_cost,
        acceleration_pct=acceleration_pct,
        acceleration_min_cost=acceleration_min_cost,
    )
    freshest = max(
        (str(row.get("ingested_at")) for row in normalized if row.get("ingested_at")),
        default=None,
    )
    observed_days = len({row["usage_date"] for row in current})
    ownership = shares(ownership_totals, "owner")
    return {
        "scope": {
            "label": "Configured platform scope",
            "workspace_id": workspace_id,
            "environment": environment,
            "resource_groups": resource_groups,
        },
        "period": {
            "days": days,
            "from": current_start.isoformat() if current_start else None,
            "to": latest.isoformat() if latest else None,
        },
        "totals": totals,
        "components": shares(component_totals, "component"),
        "series": series,
        "categories": categories,
        "ownership": ownership,
        "movers": movers,
        "anomalies": anomalies,
        "databricks_list": databricks_driver,
        "data_health": [
            {
                "source": "Azure Cost Management",
                "status": "healthy" if normalized else "unavailable",
                "freshness": freshest or (latest.isoformat() if latest else None),
                "retention_days": observed_days,
                "notes": (
                    f"{observed_days} observed days in the selected window; "
                    f"filtered to {len(resource_groups)} configured resource groups."
                ),
            },
            {
                "source": "Databricks system billing",
                "status": databricks_driver["status"],
                "notes": databricks_driver["notes"],
            },
            {
                "source": "Ownership allocation",
                "status": "healthy" if ownership else "degraded",
                "notes": (
                    "Resource group is the current ownership fallback. Tag-level "
                    "allocation can be added without changing the total."
                ),
            },
        ],
    }
