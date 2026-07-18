"""Azure bill ingestion and analysis via the Cost Management Query API.

Pulls the subscription's actual cost (daily grain, by service and resource
group) into ``<catalog>.<schema>.azure_costs`` so the Azure bill sits next to
the Databricks-side cost checks in the same dashboards/app.

Auth is keyless: ``secrets.get_credential()`` resolves a Unity Catalog service
credential inside a Databricks runtime (or DefaultAzureCredential locally),
and the identity behind it needs the **Cost Management Reader** role on the
subscription — see docs/cloud-setup.md. Reader can call the Query API but
cannot create exports; that is why this module pulls instead of exporting.

Fetch/classify split as everywhere else: ``fetch_cost_query`` is the only
network call; parsing, bucketing, spike classification and SQL construction
are pure and unit-tested offline.
"""

from __future__ import annotations

import json
import time

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import run_query

_ARM_SCOPE = "https://management.azure.com/.default"
_API_VERSION = "2023-11-01"
_MAX_RETRIES = 5

# Columns of the azure_costs table; parse_query_result emits dicts with
# exactly these keys (plus ingestion adds the timestamp server-side).
COST_ROW_SCHEMA = (
    "array<struct<usage_date:date,service_name:string,resource_group:string,"
    "service_bucket:string,cost:double,currency:string>>"
)

_MERGE_BATCH_ROWS = 2000


# --- service buckets (pure) ---------------------------------------------------

def service_bucket(service_name: str) -> str:
    """Map an Azure ServiceName onto the platform buckets the dashboards use.

    Buckets: databricks / foundry_ai (Azure OpenAI, Cognitive Services, AI
    Foundry, Azure ML) / search (Azure AI Search) / storage / other. Matching
    is keyword-based because Azure renames these products regularly.
    """
    name = (service_name or "").lower()
    if "databricks" in name:
        return "databricks"
    if "search" in name:  # "Azure AI Search" / "Azure Cognitive Search"
        return "search"
    if any(k in name for k in
           ("cognitive services", "openai", "ai foundry", "foundry",
            "azure ai services", "machine learning")):
        return "foundry_ai"
    if "storage" in name:
        return "storage"
    return "other"


# --- Cost Management Query API ------------------------------------------------

def build_query_body(start: str, end: str) -> dict:
    """Request body for the Query API: daily ActualCost by service + RG. Pure."""
    return {
        "type": "ActualCost",
        "timeframe": "Custom",
        "timePeriod": {"from": f"{start}T00:00:00+00:00", "to": f"{end}T23:59:59+00:00"},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ServiceName"},
                {"type": "Dimension", "name": "ResourceGroupName"},
            ],
        },
    }


def fetch_cost_query(credential, subscription_id: str, start: str, end: str) -> list[dict]:
    """Call the Cost Management Query API; returns the raw page payloads.

    ``credential`` is azure-identity-compatible (see secrets.get_credential).
    Follows nextLink paging and honors 429 Retry-After — the API is
    aggressively rate-limited.
    """
    import requests  # ships with databricks-sdk; keep the core wheel lean

    if not subscription_id:
        raise ValueError(
            "An Azure subscription ID is required. Pass --subscription-id or set "
            "DBX_PLATFORM_AZURE_SUBSCRIPTION_ID (BUNDLE_VAR_azure_subscription_id "
            "for the scheduled job)."
        )
    token = credential.get_token(_ARM_SCOPE).token
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/providers/Microsoft.CostManagement/query?api-version={_API_VERSION}"
    )
    body = build_query_body(start, end)
    pages: list[dict] = []
    retries = 0
    while url:
        resp = requests.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        if resp.status_code == 429 and retries < _MAX_RETRIES:
            retries += 1
            time.sleep(int(resp.headers.get("Retry-After", "15")))
            continue
        if resp.status_code == 403:
            raise RuntimeError(
                "Azure Cost Management returned 403. The identity needs the "
                "'Cost Management Reader' role on the subscription — see "
                "docs/cloud-setup.md (Azure Cost Management access)."
            )
        resp.raise_for_status()
        payload = resp.json()
        pages.append(payload)
        url = (payload.get("properties") or {}).get("nextLink")
    return pages


def parse_query_result(pages: list[dict]) -> list[dict]:
    """Flatten Query API pages into azure_costs rows. Pure.

    The API returns ``properties.columns`` + ``properties.rows`` (positional);
    UsageDate arrives as an int like 20260701.
    """
    rows: list[dict] = []
    for page in pages:
        props = page.get("properties") or {}
        cols = [c.get("name", "") for c in props.get("columns") or []]
        idx = {c.lower(): i for i, c in enumerate(cols)}
        for raw in props.get("rows") or []:
            def col(name: str, default=None, raw=raw, idx=idx):
                i = idx.get(name)
                return raw[i] if i is not None and i < len(raw) else default

            usage = str(col("usagedate", ""))
            if len(usage) == 8 and usage.isdigit():
                usage = f"{usage[0:4]}-{usage[4:6]}-{usage[6:8]}"
            service = str(col("servicename", "") or "")
            rows.append(
                {
                    "usage_date": usage,
                    "service_name": service,
                    "resource_group": str(col("resourcegroupname", "") or ""),
                    "service_bucket": service_bucket(service),
                    "cost": float(col("cost", 0) or 0),
                    "currency": str(col("currency", "") or ""),
                }
            )
    return rows


# --- storage (Delta via the SQL warehouse) ------------------------------------

def create_table_sql(catalog: str, schema: str) -> str:
    """DDL for the azure_costs table. Pure."""
    return (
        f"CREATE TABLE IF NOT EXISTS {catalog}.{schema}.azure_costs ("
        "usage_date DATE, service_name STRING, resource_group STRING, "
        "service_bucket STRING, cost DOUBLE, currency STRING, "
        "ingested_at TIMESTAMP) "
        "COMMENT 'Azure bill (Cost Management Query API), daily by service/RG'"
    )


def merge_costs_sql(catalog: str, schema: str) -> str:
    """MERGE statement upserting rows passed as a :rows JSON parameter. Pure.

    Re-pulling a window re-MERGEs it, which absorbs Azure's late cost
    restatements (the bill for a day keeps moving for ~72h).
    """
    fq = f"{catalog}.{schema}.azure_costs"
    return (
        f"MERGE INTO {fq} t USING ("
        "SELECT item.usage_date, item.service_name, item.resource_group, "
        "item.service_bucket, item.cost, item.currency "
        f"FROM (SELECT explode(from_json(:rows, '{COST_ROW_SCHEMA}')) AS item)"
        ") s "
        "ON t.usage_date = s.usage_date AND t.service_name = s.service_name "
        "AND t.resource_group = s.resource_group "
        "WHEN MATCHED THEN UPDATE SET t.cost = s.cost, t.currency = s.currency, "
        "t.service_bucket = s.service_bucket, t.ingested_at = current_timestamp() "
        "WHEN NOT MATCHED THEN INSERT "
        "(usage_date, service_name, resource_group, service_bucket, cost, "
        "currency, ingested_at) "
        "VALUES (s.usage_date, s.service_name, s.resource_group, "
        "s.service_bucket, s.cost, s.currency, current_timestamp())"
    )


def store_costs(
    w: WorkspaceClient, warehouse_id: str, catalog: str, schema: str, rows: list[dict]
) -> int:
    """Create the table if needed and MERGE rows in batches. Returns row count."""
    run_query(w, create_table_sql(catalog, schema), warehouse_id)
    sql = merge_costs_sql(catalog, schema)
    for i in range(0, len(rows), _MERGE_BATCH_ROWS):
        batch = rows[i:i + _MERGE_BATCH_ROWS]
        run_query(w, sql, warehouse_id, {"rows": json.dumps(batch, default=str)})
    return len(rows)


# --- reporting ----------------------------------------------------------------

_REPORT_DIMENSIONS = {
    "bucket": "service_bucket",
    "service": "service_name",
    "resource-group": "resource_group",
}


def report_sql(catalog: str, schema: str, by: str) -> str:
    """Aggregated spend by dimension over the :days window. Pure.

    ``by`` is validated against a whitelist because identifiers cannot be
    bound as statement parameters.
    """
    dim = _REPORT_DIMENSIONS.get(by)
    if not dim:
        raise ValueError(f"--by must be one of {sorted(_REPORT_DIMENSIONS)}")
    fq = f"{catalog}.{schema}.azure_costs"
    return (
        f"SELECT {dim}, ROUND(SUM(cost), 2) AS cost, MAX(currency) AS currency, "
        "MIN(usage_date) AS first_day, MAX(usage_date) AS last_day "
        f"FROM {fq} WHERE usage_date >= DATE_SUB(CURRENT_DATE(), :days) "
        f"GROUP BY {dim} ORDER BY cost DESC"
    )


def report(
    w: WorkspaceClient, warehouse_id: str, catalog: str, schema: str,
    by: str, days: int,
) -> list[dict]:
    return run_query(w, report_sql(catalog, schema, by), warehouse_id, {"days": days})


def daily_bucket_sql(catalog: str, schema: str) -> str:
    """Daily spend per service bucket over the :days window. Pure."""
    fq = f"{catalog}.{schema}.azure_costs"
    return (
        "SELECT usage_date, service_bucket, ROUND(SUM(cost), 2) AS cost "
        f"FROM {fq} WHERE usage_date >= DATE_SUB(CURRENT_DATE(), :days) "
        "GROUP BY usage_date, service_bucket ORDER BY usage_date, service_bucket"
    )


def fetch_daily_buckets(
    w: WorkspaceClient, warehouse_id: str, catalog: str, schema: str, days: int
) -> list[dict]:
    return run_query(w, daily_bucket_sql(catalog, schema), warehouse_id, {"days": days})


# --- spike classification (pure) ----------------------------------------------

def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def classify_azure_spend(
    rows: list[dict], spike_pct: int, min_cost: float
) -> list[dict]:
    """Pure decision logic: per-bucket day-over-trailing-week spend spikes.

    ``rows`` are daily (usage_date, service_bucket, cost) rows. For each
    bucket, the latest day is compared to the mean of the preceding seven
    days; a jump above ``spike_pct``%% on at least ``min_cost`` currency
    units is a finding. The most recent day in the data is often partial —
    the comparison uses the latest *closed* day (second-newest date).
    """
    by_bucket: dict[str, dict[str, float]] = {}
    dates: set[str] = set()
    for r in rows:
        d, b = str(r.get("usage_date", "")), str(r.get("service_bucket", ""))
        if not d or not b:
            continue
        dates.add(d)
        by_bucket.setdefault(b, {})[d] = by_bucket.get(b, {}).get(d, 0.0) + _num(r.get("cost"))
    ordered = sorted(dates)
    if len(ordered) < 3:
        return []
    latest_closed = ordered[-2] if len(ordered) >= 2 else ordered[-1]
    window = [d for d in ordered if d < latest_closed][-7:]
    findings = []
    for bucket, daily in sorted(by_bucket.items()):
        latest = daily.get(latest_closed, 0.0)
        base = [daily.get(d, 0.0) for d in window]
        baseline = sum(base) / len(base) if base else 0.0
        if latest < min_cost or baseline <= 0:
            continue
        change_pct = (latest - baseline) / baseline * 100
        if change_pct >= spike_pct:
            findings.append(
                {
                    "service_bucket": bucket,
                    "day": latest_closed,
                    "cost": round(latest, 2),
                    "trailing_7d_avg": round(baseline, 2),
                    "reason": f"spend {latest:.2f} is {change_pct:.0f}% above the "
                              f"trailing-7d average {baseline:.2f} "
                              f"(threshold {spike_pct}%)",
                    "action": "investigate-spend-spike",
                }
            )
    findings.sort(key=lambda f: f["cost"], reverse=True)
    return findings
