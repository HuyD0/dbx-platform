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
from collections.abc import Sequence
from datetime import date, timedelta

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import load_query, run_query

_ARM_SCOPE = "https://management.azure.com/.default"
_API_VERSION = "2023-11-01"
_MAX_RETRIES = 5
_MAX_QUERY_DAYS = 31
_MAX_SQL_PARAMETER_BYTES = 900_000
_CLIENT_TYPE = "GitHubCopilotForAzure"

# Columns of the azure_costs table; parse_query_result emits dicts with
# exactly these keys (plus ingestion adds the timestamp server-side).
COST_ROW_SCHEMA = (
    "array<struct<usage_date:date,service_name:string,resource_group:string,"
    "service_bucket:string,cost:double,currency:string>>"
)


def inclusive_date_window(end: date, days: int) -> tuple[date, date]:
    """Return exactly ``days`` calendar dates, including both endpoints."""

    if days < 1:
        raise ValueError("Azure cost collection days must be at least 1.")
    return end - timedelta(days=days - 1), end


DETAIL_ROW_SCHEMA = (
    "array<struct<usage_date:date,resource_id:string,resource_group:string,"
    "resource_type:string,meter_name:string,service_bucket:string,cost:double,"
    "currency:string>>"
)

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
           ("cognitive services", "cognitiveservices", "openai", "ai foundry", "foundry",
            "azure ai services", "machine learning")):
        return "foundry_ai"
    if "storage" in name:
        return "storage"
    return "other"


# --- Cost Management Query API ------------------------------------------------

def parse_resource_groups(value: str | Sequence[str]) -> tuple[str, ...]:
    """Normalize the resource-group allowlist used for workspace attribution."""

    raw = value.split(",") if isinstance(value, str) else value
    groups = tuple(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))
    if not groups:
        raise ValueError(
            "Azure cost ingestion requires at least one resource group. Pass "
            "--resource-groups or set BUNDLE_VAR_azure_cost_resource_groups."
        )
    return groups


def resource_group_scope_filter(value: str | Sequence[str]) -> str:
    """Return a stable persisted identity for an allowlist."""

    return ",".join(sorted(group.casefold() for group in parse_resource_groups(value)))


def split_date_windows(
    start: str,
    end: str,
    *,
    max_days: int = _MAX_QUERY_DAYS,
) -> list[tuple[str, str]]:
    """Split an inclusive range into Cost Management Query-safe windows."""

    first = date.fromisoformat(str(start)[:10])
    last = date.fromisoformat(str(end)[:10])
    if first > last:
        raise ValueError("start must be on or before end")
    if max_days < 1:
        raise ValueError("max_days must be positive")
    windows: list[tuple[str, str]] = []
    current = first
    while current <= last:
        window_end = min(current + timedelta(days=max_days - 1), last)
        windows.append((current.isoformat(), window_end.isoformat()))
        current = window_end + timedelta(days=1)
    return windows


def _validate_query_window(start: str, end: str) -> None:
    if len(split_date_windows(start, end)) != 1:
        raise ValueError(
            f"Azure daily cost queries may cover at most {_MAX_QUERY_DAYS} days; "
            "split the requested range first."
        )


def _resource_group_filter(resource_groups: str | Sequence[str]) -> dict:
    return {
        "dimensions": {
            "name": "ResourceGroup",
            "operator": "In",
            "values": list(parse_resource_groups(resource_groups)),
        }
    }


def _inclusive_lookback(days: int) -> int:
    value = int(days)
    if value < 1:
        raise ValueError("days must be positive")
    return value - 1


def build_query_body(
    start: str,
    end: str,
    *,
    resource_groups: str | Sequence[str],
) -> dict:
    """Daily billed usage by service/RG within an explicit workspace scope."""

    _validate_query_window(start, end)
    return {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": {"from": f"{start}T00:00:00+00:00", "to": f"{end}T23:59:59+00:00"},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ServiceName"},
                {"type": "Dimension", "name": "ResourceGroup"},
            ],
            "filter": _resource_group_filter(resource_groups),
        },
    }


def build_detail_query_body(
    start: str,
    end: str,
    *,
    resource_groups: str | Sequence[str],
) -> dict:
    """Daily billed usage by resource/meter within a workspace scope."""

    _validate_query_window(start, end)
    return {
        "type": "Usage",
        "timeframe": "Custom",
        "timePeriod": {"from": f"{start}T00:00:00+00:00", "to": f"{end}T23:59:59+00:00"},
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}},
            "grouping": [
                {"type": "Dimension", "name": "ResourceId"},
                {"type": "Dimension", "name": "Meter"},
            ],
            "filter": _resource_group_filter(resource_groups),
        },
    }


def fetch_cost_query(
    credential,
    subscription_id: str,
    start: str,
    end: str,
    *,
    body: dict | None = None,
) -> list[dict]:
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
    if body is None:
        raise ValueError("A resource-scoped Azure Cost Management query body is required.")
    pages: list[dict] = []
    retries = 0
    while url:
        resp = requests.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "ClientType": _CLIENT_TYPE,
            },
            timeout=60,
        )
        if resp.status_code == 429 and retries < _MAX_RETRIES:
            retries += 1
            retry_headers = (
                "Retry-After",
                "x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after",
                "x-ms-ratelimit-microsoft.costmanagement-entity-retry-after",
                "x-ms-ratelimit-microsoft.costmanagement-tenant-retry-after",
            )
            waits = []
            for header in retry_headers:
                try:
                    waits.append(int(resp.headers.get(header, "0")))
                except (TypeError, ValueError):
                    continue
            time.sleep(max(waits, default=15))
            continue
        if resp.status_code == 403:
            raise RuntimeError(
                "Azure Cost Management returned 403. The identity needs the "
                "'Cost Management Reader' role on the subscription — see "
                "docs/cloud-setup.md (Azure Cost Management access)."
            )
        if not resp.ok:
            try:
                error = resp.json()
            except ValueError:
                error = {"message": resp.text[:1000]}
            raise RuntimeError(
                f"Azure Cost Management returned HTTP {resp.status_code}: "
                f"{json.dumps(error, sort_keys=True)[:2000]}"
            )
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
                    "resource_group": str(
                        col("resourcegroupname", col("resourcegroup", "")) or ""
                    ),
                    "service_bucket": service_bucket(service),
                    "cost": float(col("cost", col("pretaxcost", 0)) or 0),
                    "currency": str(col("currency", "") or ""),
                }
            )
    return rows


def parse_detail_query_result(pages: list[dict]) -> list[dict]:
    """Flatten resource/meter query pages into allocation rows."""

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
            resource_id = str(col("resourceid", "") or "")
            resource_type = _resource_type(resource_id)
            meter = str(col("meter", "") or "")
            rows.append(
                {
                    "usage_date": usage,
                    "resource_id": resource_id,
                    "resource_group": _resource_group(resource_id),
                    "resource_type": resource_type,
                    "meter_name": meter,
                    "service_bucket": service_bucket(f"{resource_type} {meter}"),
                    "cost": float(col("cost", col("pretaxcost", 0)) or 0),
                    "currency": str(col("currency", "") or ""),
                }
            )
    return rows


# --- storage (Delta via the SQL warehouse) ------------------------------------

def create_table_sql(catalog: str, schema: str) -> str:
    """DDL for the azure_costs table. Pure."""
    return (
        f"CREATE TABLE IF NOT EXISTS {catalog}.{schema}.azure_costs ("
        "workspace_id STRING, environment STRING, subscription_id STRING, "
        "scope_filter STRING, usage_date DATE, "
        "service_name STRING, resource_group STRING, "
        "service_bucket STRING, cost DOUBLE, currency STRING, "
        "ingested_at TIMESTAMP) "
        "COMMENT 'Resource-scoped Azure bill, daily by service/RG'"
    )


def create_detail_table_sql(catalog: str, schema: str) -> str:
    """DDL for resource/meter-grain Azure cost allocation."""

    return (
        f"CREATE TABLE IF NOT EXISTS {catalog}.{schema}.azure_cost_details ("
        "workspace_id STRING, environment STRING, subscription_id STRING, "
        "scope_filter STRING, usage_date DATE, "
        "resource_id STRING, resource_group STRING, "
        "resource_type STRING, meter_name STRING, service_bucket STRING, "
        "cost DOUBLE, currency STRING, ingested_at TIMESTAMP) "
        "COMMENT 'Resource-scoped Azure billed cost, daily by resource and meter'"
    )


def merge_costs_sql(catalog: str, schema: str) -> str:
    """Atomically reconcile one exact workspace/environment/date window.

    ``WHEN NOT MATCHED BY SOURCE`` removes rows that Azure withdrew from a
    late-adjusted response.  Its predicate deliberately limits deletion to
    the requested window and deployment scope.
    """
    fq = f"{catalog}.{schema}.azure_costs"
    return (
        f"MERGE INTO {fq} t USING ("
        "SELECT :workspace_id AS workspace_id, :environment AS environment, "
        ":subscription_id AS subscription_id, :scope_filter AS scope_filter, "
        "item.usage_date, item.service_name, item.resource_group, "
        "item.service_bucket, item.cost, item.currency "
        f"FROM (SELECT explode(from_json(:rows, '{COST_ROW_SCHEMA}')) AS item)"
        ") s "
        "ON t.workspace_id = s.workspace_id AND t.environment = s.environment "
        "AND t.subscription_id = s.subscription_id "
        "AND t.usage_date = s.usage_date AND t.service_name = s.service_name "
        "AND t.resource_group = s.resource_group "
        "AND t.currency = s.currency "
        "WHEN MATCHED THEN UPDATE SET t.cost = s.cost, "
        "t.service_bucket = s.service_bucket, t.scope_filter = s.scope_filter, "
        "t.ingested_at = current_timestamp() "
        "WHEN NOT MATCHED THEN INSERT "
        "(workspace_id, environment, subscription_id, scope_filter, usage_date, "
        "service_name, resource_group, service_bucket, cost, currency, ingested_at) "
        "VALUES (s.workspace_id, s.environment, s.subscription_id, s.scope_filter, "
        "s.usage_date, s.service_name, s.resource_group, s.service_bucket, s.cost, "
        "s.currency, current_timestamp()) "
        "WHEN NOT MATCHED BY SOURCE AND t.workspace_id = :workspace_id "
        "AND t.environment = :environment "
        "AND t.subscription_id = :subscription_id "
        "AND t.usage_date BETWEEN CAST(:window_start AS DATE) "
        "AND CAST(:window_end AS DATE) THEN DELETE"
    )


def merge_detail_costs_sql(catalog: str, schema: str) -> str:
    """Atomically reconcile resource/meter actuals for one exact window."""

    fq = f"{catalog}.{schema}.azure_cost_details"
    return (
        f"MERGE INTO {fq} t USING ("
        "SELECT :workspace_id AS workspace_id, :environment AS environment, "
        ":subscription_id AS subscription_id, :scope_filter AS scope_filter, "
        "item.usage_date, item.resource_id, item.resource_group, "
        "item.resource_type, item.meter_name, item.service_bucket, item.cost, "
        "item.currency "
        f"FROM (SELECT explode(from_json(:rows, '{DETAIL_ROW_SCHEMA}')) AS item)"
        ") s "
        "ON t.workspace_id = s.workspace_id AND t.environment = s.environment "
        "AND t.subscription_id = s.subscription_id "
        "AND t.usage_date = s.usage_date AND t.resource_id = s.resource_id "
        "AND t.meter_name = s.meter_name AND t.currency = s.currency "
        "WHEN MATCHED THEN UPDATE SET t.resource_group = s.resource_group, "
        "t.resource_type = s.resource_type, t.service_bucket = s.service_bucket, "
        "t.cost = s.cost, t.scope_filter = s.scope_filter, "
        "t.ingested_at = current_timestamp() "
        "WHEN NOT MATCHED THEN INSERT "
        "(workspace_id, environment, subscription_id, scope_filter, usage_date, "
        "resource_id, resource_group, resource_type, meter_name, service_bucket, "
        "cost, currency, ingested_at) "
        "VALUES (s.workspace_id, s.environment, s.subscription_id, s.scope_filter, "
        "s.usage_date, s.resource_id, s.resource_group, s.resource_type, s.meter_name, "
        "s.service_bucket, s.cost, s.currency, current_timestamp()) "
        "WHEN NOT MATCHED BY SOURCE AND t.workspace_id = :workspace_id "
        "AND t.environment = :environment "
        "AND t.subscription_id = :subscription_id "
        "AND t.usage_date BETWEEN CAST(:window_start AS DATE) "
        "AND CAST(:window_end AS DATE) THEN DELETE"
    )


def count_costs_sql(catalog: str, schema: str) -> str:
    """Daily target counts for one exact coarse-cost reconciliation window."""

    return _count_reconciled_rows_sql(f"{catalog}.{schema}.azure_costs")


def count_detail_costs_sql(catalog: str, schema: str) -> str:
    """Daily target counts for one exact detail-cost reconciliation window."""

    return _count_reconciled_rows_sql(f"{catalog}.{schema}.azure_cost_details")


def _count_reconciled_rows_sql(table: str) -> str:
    return (
        "SELECT usage_date, COUNT(*) AS row_count, "
        "SUM(CASE WHEN scope_filter = :scope_filter THEN 1 ELSE 0 END) "
        f"AS scope_row_count FROM {table} "
        "WHERE workspace_id = :workspace_id AND environment = :environment "
        "AND subscription_id = :subscription_id "
        "AND usage_date BETWEEN CAST(:window_start AS DATE) "
        "AND CAST(:window_end AS DATE) GROUP BY usage_date ORDER BY usage_date"
    )


def store_costs(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    rows: list[dict],
    *,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    scope_filter: str,
    window_start: str,
    window_end: str,
) -> int:
    """Replace the exact coarse-cost window in parameter-safe atomic units."""

    try:
        _store_reconciled_rows(
            w,
            warehouse_id,
            merge_costs_sql(catalog, schema),
            rows,
            workspace_id=workspace_id,
            environment=environment,
            subscription_id=subscription_id,
            scope_filter=scope_filter,
            window_start=window_start,
            window_end=window_end,
        )
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Unable to reconcile required table {catalog}.{schema}.azure_costs; "
            "run the deployment schema_migrations job and verify writer grants."
        ) from exc
    return len(rows)


def store_detail_costs(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    rows: list[dict],
    *,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    scope_filter: str,
    window_start: str,
    window_end: str,
) -> int:
    """Replace resource/meter cost in parameter-safe atomic units."""

    try:
        _store_reconciled_rows(
            w,
            warehouse_id,
            merge_detail_costs_sql(catalog, schema),
            rows,
            workspace_id=workspace_id,
            environment=environment,
            subscription_id=subscription_id,
            scope_filter=scope_filter,
            window_start=window_start,
            window_end=window_end,
        )
    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Unable to reconcile required table "
            f"{catalog}.{schema}.azure_cost_details; run the deployment "
            "schema_migrations job and verify writer grants."
        ) from exc
    return len(rows)


def _store_reconciled_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    sql: str,
    rows: list[dict],
    *,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    scope_filter: str,
    window_start: str,
    window_end: str,
) -> None:
    """Use one MERGE when possible; split oversized payloads by exact day."""

    params = _reconciliation_params(
        rows,
        workspace_id,
        environment,
        subscription_id,
        scope_filter,
        window_start,
        window_end,
    )
    if len(params["rows"].encode("utf-8")) <= _MAX_SQL_PARAMETER_BYTES:
        run_query(w, sql, warehouse_id, params)
        return

    first = date.fromisoformat(params["window_start"])
    last = date.fromisoformat(params["window_end"])
    current = first
    while current <= last:
        day = current.isoformat()
        day_rows = [row for row in rows if str(row.get("usage_date"))[:10] == day]
        day_params = _reconciliation_params(
            day_rows,
            workspace_id,
            environment,
            subscription_id,
            scope_filter,
            day,
            day,
        )
        size = len(day_params["rows"].encode("utf-8"))
        if size > _MAX_SQL_PARAMETER_BYTES:
            raise ValueError(
                f"Azure cost payload for {day} is {size} bytes; it exceeds the "
                "safe Databricks statement parameter limit after daily splitting."
            )
        run_query(w, sql, warehouse_id, day_params)
        current += timedelta(days=1)


def _reconciliation_params(
    rows: list[dict],
    workspace_id: str,
    environment: str,
    subscription_id: str,
    scope_filter: str,
    window_start: str,
    window_end: str,
) -> dict[str, str]:
    """Validate an exact inclusive window and serialize its replacement rows."""

    required = (workspace_id, environment, subscription_id, scope_filter)
    if any(not str(value).strip() for value in required):
        raise ValueError(
            "workspace_id, environment, subscription_id and scope_filter are "
            "required for cost reconciliation"
        )
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
        raise ValueError(f"cost rows fall outside the reconciliation window: {outside[:3]}")
    return {
        "rows": json.dumps(rows, default=str),
        "workspace_id": workspace_id,
        "environment": environment,
        "subscription_id": subscription_id,
        "scope_filter": scope_filter,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
    }


def expected_daily_counts(
    rows: list[dict], window_start: str, window_end: str
) -> dict[str, int]:
    """Count source rows for every day in an exact inclusive window."""

    try:
        start = date.fromisoformat(str(window_start)[:10])
        end = date.fromisoformat(str(window_end)[:10])
    except ValueError as exc:
        raise ValueError("window_start and window_end must be ISO dates") from exc
    if start > end:
        raise ValueError("window_start must be on or before window_end")
    counts = {
        (start + timedelta(days=offset)).isoformat(): 0
        for offset in range((end - start).days + 1)
    }
    outside = []
    for row in rows:
        raw_day = str(row.get("usage_date", ""))[:10]
        try:
            day = date.fromisoformat(raw_day)
        except ValueError as exc:
            raise ValueError(f"cost row has invalid usage_date: {raw_day!r}") from exc
        if day < start or day > end:
            outside.append(raw_day)
            continue
        counts[day.isoformat()] += 1
    if outside:
        raise ValueError(f"cost rows fall outside the reconciliation window: {outside[:3]}")
    return counts


def validate_cost_reconciliation(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    rows: list[dict],
    *,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    scope_filter: str,
    window_start: str,
    window_end: str,
) -> list[dict]:
    """Fail unless coarse target counts match the exact source window by day."""

    return _validate_reconciled_rows(
        w,
        warehouse_id,
        count_costs_sql(catalog, schema),
        f"{catalog}.{schema}.azure_costs",
        rows,
        workspace_id=workspace_id,
        environment=environment,
        subscription_id=subscription_id,
        scope_filter=scope_filter,
        window_start=window_start,
        window_end=window_end,
    )


def validate_detail_reconciliation(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    rows: list[dict],
    *,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    scope_filter: str,
    window_start: str,
    window_end: str,
) -> list[dict]:
    """Fail unless detail target counts match the exact source window by day."""

    return _validate_reconciled_rows(
        w,
        warehouse_id,
        count_detail_costs_sql(catalog, schema),
        f"{catalog}.{schema}.azure_cost_details",
        rows,
        workspace_id=workspace_id,
        environment=environment,
        subscription_id=subscription_id,
        scope_filter=scope_filter,
        window_start=window_start,
        window_end=window_end,
    )


def _validate_reconciled_rows(
    w: WorkspaceClient,
    warehouse_id: str,
    sql: str,
    table: str,
    rows: list[dict],
    *,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    scope_filter: str,
    window_start: str,
    window_end: str,
) -> list[dict]:
    summaries = []
    for unit_start, unit_end, unit_rows in _validation_windows(
        rows,
        workspace_id=workspace_id,
        environment=environment,
        subscription_id=subscription_id,
        scope_filter=scope_filter,
        window_start=window_start,
        window_end=window_end,
    ):
        expected = expected_daily_counts(unit_rows, unit_start, unit_end)
        params = {
            "workspace_id": workspace_id,
            "environment": environment,
            "subscription_id": subscription_id,
            "scope_filter": scope_filter,
            "window_start": unit_start,
            "window_end": unit_end,
        }
        try:
            target_rows = run_query(w, sql, warehouse_id, params, row_limit=len(expected))
        except Exception as exc:
            raise RuntimeError(
                f"Unable to validate required table {table} after reconciliation."
            ) from exc
        observed = {day: 0 for day in expected}
        observed_scope = {day: 0 for day in expected}
        for target_row in target_rows:
            day = str(target_row.get("usage_date", ""))[:10]
            if day not in expected:
                raise RuntimeError(
                    f"Load integrity mismatch for {table}: target returned unexpected day {day}."
                )
            observed[day] += int(target_row.get("row_count") or 0)
            observed_scope[day] += int(target_row.get("scope_row_count") or 0)
        mismatches = [
            (day, expected[day], observed[day], observed_scope[day])
            for day in expected
            if expected[day] != observed[day] or expected[day] != observed_scope[day]
        ]
        if mismatches:
            details = ", ".join(
                f"{day} expected={source} target={target} current_scope={scoped}"
                for day, source, target, scoped in mismatches[:5]
            )
            raise RuntimeError(f"Load integrity mismatch for {table}: {details}")
        summaries.append(
            {
                "table": table,
                "window_start": unit_start,
                "window_end": unit_end,
                "source_rows": sum(expected.values()),
                "target_rows": sum(observed.values()),
                "validated_days": len(expected),
                "status": "validated",
            }
        )
    return summaries


def _validation_windows(
    rows: list[dict],
    *,
    workspace_id: str,
    environment: str,
    subscription_id: str,
    scope_filter: str,
    window_start: str,
    window_end: str,
) -> list[tuple[str, str, list[dict]]]:
    """Mirror the atomic windows selected by ``_store_reconciled_rows``."""

    params = _reconciliation_params(
        rows,
        workspace_id,
        environment,
        subscription_id,
        scope_filter,
        window_start,
        window_end,
    )
    if len(params["rows"].encode("utf-8")) <= _MAX_SQL_PARAMETER_BYTES:
        return [(params["window_start"], params["window_end"], rows)]
    start = date.fromisoformat(params["window_start"])
    end = date.fromisoformat(params["window_end"])
    windows = []
    current = start
    while current <= end:
        day = current.isoformat()
        windows.append(
            (day, day, [row for row in rows if str(row.get("usage_date"))[:10] == day])
        )
        current += timedelta(days=1)
    return windows


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
        "WITH current_scope AS ("
        "SELECT subscription_id, scope_filter "
        f"FROM {fq} WHERE workspace_id = :workspace_id "
        "AND environment = :environment AND COALESCE(scope_filter, '') <> '' "
        "ORDER BY ingested_at DESC LIMIT 1"
        ") "
        f"SELECT c.{dim}, ROUND(SUM(c.cost), 2) AS cost, "
        "MAX(c.currency) AS currency, MIN(c.usage_date) AS first_day, "
        "MAX(c.usage_date) AS last_day "
        f"FROM {fq} c INNER JOIN current_scope s "
        "ON c.subscription_id = s.subscription_id AND c.scope_filter = s.scope_filter "
        "WHERE c.usage_date >= DATE_SUB(CURRENT_DATE(), :days) "
        "AND c.workspace_id = :workspace_id AND c.environment = :environment "
        f"GROUP BY c.{dim} ORDER BY cost DESC"
    )


def report(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    by: str,
    days: int,
    *,
    workspace_id: str,
    environment: str,
) -> list[dict]:
    return run_query(
        w,
        report_sql(catalog, schema, by),
        warehouse_id,
        {
            "days": _inclusive_lookback(days),
            "workspace_id": workspace_id,
            "environment": environment,
        },
    )


_DETAIL_DIMENSIONS = {
    "resource": "resource_id",
    "meter": "meter_name",
    "resource-group": "resource_group",
}

_BUCKETS = ("databricks", "foundry_ai", "search", "storage", "other")


def report_detail_sql(catalog: str, schema: str, by: str, bucket: str | None = None) -> str:
    """Aggregated detail-grain spend (resource/meter) over :days. Pure.

    Reads azure_cost_details — the per-deployment/meter grain — so Foundry
    spend can be attributed to individual model deployments. ``by`` and
    ``bucket`` are validated against whitelists because identifiers cannot be
    bound as statement parameters (the bucket value itself is bound).
    """
    dim = _DETAIL_DIMENSIONS.get(by)
    if not dim:
        raise ValueError(f"--by must be one of {sorted(_DETAIL_DIMENSIONS)}")
    if bucket is not None and bucket not in _BUCKETS:
        raise ValueError(f"--bucket must be one of {sorted(_BUCKETS)}")
    extra = {
        "resource": ", c.resource_group, c.resource_type",
        "meter": "",
        "resource-group": "",
    }[by]
    fq = f"{catalog}.{schema}.azure_cost_details"
    coarse_fq = f"{catalog}.{schema}.azure_costs"
    bucket_clause = "AND c.service_bucket = :bucket " if bucket else ""
    return (
        "WITH current_scope AS ("
        "SELECT subscription_id, scope_filter "
        f"FROM {coarse_fq} WHERE workspace_id = :workspace_id "
        "AND environment = :environment AND COALESCE(scope_filter, '') <> '' "
        "ORDER BY ingested_at DESC LIMIT 1"
        ") "
        f"SELECT c.{dim}{extra}, c.service_bucket, "
        "ROUND(SUM(c.cost), 2) AS cost, MAX(c.currency) AS currency, "
        "MIN(c.usage_date) AS first_day, MAX(c.usage_date) AS last_day "
        f"FROM {fq} c INNER JOIN current_scope s "
        "ON c.subscription_id = s.subscription_id AND c.scope_filter = s.scope_filter "
        "WHERE c.usage_date >= DATE_SUB(CURRENT_DATE(), :days) "
        "AND c.workspace_id = :workspace_id AND c.environment = :environment "
        f"{bucket_clause}"
        f"GROUP BY c.{dim}{extra}, c.service_bucket ORDER BY cost DESC"
    )


def report_detail(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    by: str,
    days: int,
    bucket: str | None = None,
    *,
    workspace_id: str,
    environment: str,
) -> list[dict]:
    params: dict[str, int | str] = {
        "days": _inclusive_lookback(days),
        "workspace_id": workspace_id,
        "environment": environment,
    }
    if bucket:
        params["bucket"] = bucket
    return run_query(
        w, report_detail_sql(catalog, schema, by, bucket), warehouse_id, params
    )


def daily_bucket_sql(catalog: str, schema: str) -> str:
    """Daily spend per service bucket over the :days window. Pure."""
    fq = f"{catalog}.{schema}.azure_costs"
    return (
        "WITH current_scope AS ("
        "SELECT subscription_id, scope_filter "
        f"FROM {fq} WHERE workspace_id = :workspace_id "
        "AND environment = :environment AND COALESCE(scope_filter, '') <> '' "
        "ORDER BY ingested_at DESC LIMIT 1"
        ") "
        "SELECT c.usage_date, c.service_bucket, ROUND(SUM(c.cost), 2) AS cost "
        f"FROM {fq} c INNER JOIN current_scope s "
        "ON c.subscription_id = s.subscription_id AND c.scope_filter = s.scope_filter "
        "WHERE c.usage_date >= DATE_SUB(CURRENT_DATE(), :days) "
        "AND c.workspace_id = :workspace_id AND c.environment = :environment "
        "GROUP BY c.usage_date, c.service_bucket "
        "ORDER BY c.usage_date, c.service_bucket"
    )


def fetch_daily_buckets(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    days: int,
    *,
    workspace_id: str,
    environment: str,
) -> list[dict]:
    return run_query(
        w,
        daily_bucket_sql(catalog, schema),
        warehouse_id,
        {
            "days": _inclusive_lookback(days),
            "workspace_id": workspace_id,
            "environment": environment,
        },
    )


def reconciliation_sql(catalog: str, schema: str) -> str:
    """Daily SKU-family comparison without invoice-line claims."""

    return (
        load_query("azure_databricks_reconciliation")
        .replace(
            "__AZURE_COST_DETAIL_TABLE__",
            f"{catalog}.{schema}.azure_cost_details",
        )
        .replace(
            "__AZURE_COST_TABLE__",
            f"{catalog}.{schema}.azure_costs",
        )
    )


def reconciliation(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    days: int,
    *,
    workspace_id: str,
    environment: str,
) -> list[dict]:
    """Compare list and billed cost only where currency permits variance."""

    return run_query(
        w,
        reconciliation_sql(catalog, schema),
        warehouse_id,
        {
            "days": _inclusive_lookback(days),
            "workspace_id": workspace_id,
            "environment": environment,
        },
        row_limit=50_000,
    )


# --- spike classification (pure) ----------------------------------------------

def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resource_group(resource_id: str) -> str:
    parts = [part for part in resource_id.split("/") if part]
    lowered = [part.lower() for part in parts]
    try:
        return parts[lowered.index("resourcegroups") + 1]
    except (ValueError, IndexError):
        return ""


def _resource_type(resource_id: str) -> str:
    parts = [part for part in resource_id.split("/") if part]
    lowered = [part.lower() for part in parts]
    try:
        provider_index = lowered.index("providers")
    except ValueError:
        return ""
    provider_parts = parts[provider_index + 1:]
    if not provider_parts:
        return ""
    # Namespace plus alternating type segments, excluding resource names.
    return "/".join([provider_parts[0], *provider_parts[1::2]])


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
