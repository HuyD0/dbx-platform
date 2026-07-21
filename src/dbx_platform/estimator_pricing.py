"""Versioned unit-price snapshots for the AI Solution Cost & TCO estimator.

Two pricing sources land in one Delta table
(``<catalog>.<schema>.estimator_price_snapshots``), keyed by snapshot date so
every estimate can be reproduced against the exact prices it used:

- **Azure Retail Prices API** (https://prices.azure.com/api/retail/prices) —
  public and unauthenticated; ``fetch_retail_prices`` is the only network
  call. The rate card (``estimator_data/rate_card.json``) groups meters into
  a handful of OData requests and matches individual meters by regex at parse
  time, so Azure meter renames surface as pricing-coverage findings instead
  of silently wrong estimates.
- **``system.billing.list_prices``** — the live $/DBU list prices, fetched
  through the SQL warehouse like every other Databricks-side price in this
  repo.

Fetch/parse split as everywhere else: fetching touches the network/warehouse;
parsing, unit normalization, DDL/MERGE construction and coverage
classification are pure and unit-tested offline. Token prices are normalized
to **per 1M text units**, capacity prices to **per hour**, storage to
**per GB-month** at parse time, so the engine never sees raw meter units.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import run_query

RETAIL_PRICES_URL = "https://prices.azure.com/api/retail/prices"
_MAX_RETRIES = 5

PRICE_ROW_SCHEMA = (
    "array<struct<snapshot_date:date,source:string,rate_key:string,meter_id:string,"
    "service_name:string,product_name:string,sku_name:string,meter_name:string,"
    "region:string,unit:string,unit_price:double,currency:string>>"
)

_CANONICAL_UNITS = {
    "token": "million text units",
    "hour": "hour",
    "unit_hour": "unit-hour",
    "gb_month": "GB-month",
}


# --- Azure Retail Prices API --------------------------------------------------


def build_price_filters(rate_card: dict, region: str, currency: str) -> list[tuple[str, str]]:
    """One (group, $filter) per rate-card group that a rate key references. Pure.

    Regional and Global meters are both requested because Azure prices global
    model deployments under armRegionName 'Global'. ``currency`` rides on the
    request query string, not the filter (see fetch_retail_prices).
    """
    del currency  # bound at fetch time; kept in the signature for call-site clarity
    referenced = {entry["group"] for entry in rate_card["azure_rate_keys"]}
    filters = []
    for group, base in rate_card["azure_groups"].items():
        if group not in referenced:
            continue
        filters.append(
            (
                group,
                f"({base}) and (armRegionName eq '{region}' or armRegionName eq 'Global')",
            )
        )
    return sorted(filters)


def fetch_retail_prices(
    odata_filter: str, *, currency: str = "USD", max_pages: int = 100
) -> list[dict]:
    """Fetch every item for one OData filter; the only Azure network call.

    No credential is required — the Retail Prices API is public. Follows
    NextPageLink paging and honors 429 Retry-After.
    """
    import requests  # ships with databricks-sdk; keep the core wheel lean

    items: list[dict] = []
    url: str | None = RETAIL_PRICES_URL
    params: dict | None = {"$filter": odata_filter, "currencyCode": currency}
    retries = 0
    pages = 0
    while url and pages < max_pages:
        resp = requests.get(url, params=params, timeout=60)
        if resp.status_code == 429 and retries < _MAX_RETRIES:
            retries += 1
            time.sleep(int(resp.headers.get("Retry-After", "10")))
            continue
        if not resp.ok:
            raise RuntimeError(
                f"Azure Retail Prices API returned HTTP {resp.status_code}: "
                f"{resp.text[:500]}"
            )
        payload = resp.json()
        items.extend(payload.get("Items") or [])
        url = payload.get("NextPageLink") or None
        params = None  # NextPageLink already carries the query string
        pages += 1
    return items


def _token_scale(unit_of_measure: str) -> float | None:
    """Multiplier that converts a per-<unit> token price to per 1M. Pure."""

    match = re.match(r"\s*([\d,.]+)\s*([KM]?)", str(unit_of_measure or ""), re.IGNORECASE)
    if not match or not match.group(1):
        return None
    try:
        count = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    count *= {"": 1, "K": 1_000, "M": 1_000_000}[match.group(2).upper()]
    return 1_000_000 / count if count > 0 else None


def _capacity_scale(unit_of_measure: str) -> float:
    """Per-N capacity units ('10 Hours', '100 GB/Month') normalize to per-1."""

    match = re.match(r"\s*([\d,.]+)", str(unit_of_measure or ""))
    if not match:
        return 1.0
    try:
        count = float(match.group(1).replace(",", ""))
    except ValueError:
        return 1.0
    return 1.0 / count if count > 1 else 1.0


def parse_retail_prices(
    items_by_group: dict[str, list[dict]], rate_card: dict, snapshot_date: str
) -> list[dict]:
    """Match fetched meters to rate keys and normalize units. Pure.

    Zero-priced meters are skipped: free-tier meters must never win the
    cheapest-meter selection and silently zero out a component.
    """
    rows: list[dict] = []
    for entry in rate_card["azure_rate_keys"]:
        include = re.compile(entry["meter_regex"])
        exclude = re.compile(entry["exclude_regex"]) if entry.get("exclude_regex") else None
        product_inc = (
            re.compile(entry["product_regex"]) if entry.get("product_regex") else None
        )
        product_exc = (
            re.compile(entry["product_exclude_regex"])
            if entry.get("product_exclude_regex")
            else None
        )
        seen: set[str] = set()
        for item in items_by_group.get(entry["group"], []):
            meter = str(item.get("meterName") or "")
            product = str(item.get("productName") or "")
            if not include.search(meter):
                continue
            if exclude and exclude.search(meter):
                continue
            if product_inc and not product_inc.search(product):
                continue
            if product_exc and product_exc.search(product):
                continue
            price = float(item.get("retailPrice") or 0.0)
            if price <= 0:
                continue
            if entry["kind"] == "token":
                scale = _token_scale(item.get("unitOfMeasure"))
                if scale is None:
                    continue
            else:
                scale = _capacity_scale(item.get("unitOfMeasure"))
            meter_id = str(item.get("meterId") or f"{meter}|{product}")
            if meter_id in seen:
                continue
            seen.add(meter_id)
            rows.append(
                {
                    "snapshot_date": snapshot_date,
                    "source": "azure_retail",
                    "rate_key": entry["rate_key"],
                    "meter_id": meter_id,
                    "service_name": str(item.get("serviceName") or ""),
                    "product_name": product,
                    "sku_name": str(item.get("skuName") or ""),
                    "meter_name": meter,
                    "region": str(item.get("armRegionName") or ""),
                    "unit": _CANONICAL_UNITS[entry["kind"]],
                    "unit_price": round(price * scale, 8),
                    "currency": str(item.get("currencyCode") or ""),
                }
            )
    return rows


# --- Databricks list prices ---------------------------------------------------


def databricks_prices_sql() -> str:
    """Latest $/DBU list price per SKU on Azure. Pure."""

    return (
        "SELECT sku_name, currency_code, "
        "COALESCE(pricing.effective_list.default, pricing.default) AS unit_price "
        "FROM system.billing.list_prices "
        "WHERE price_end_time IS NULL AND upper(cloud) = 'AZURE'"
    )


def fetch_databricks_prices(w: WorkspaceClient, warehouse_id: str) -> list[dict]:
    return run_query(w, databricks_prices_sql(), warehouse_id)


def parse_databricks_prices(
    rows: list[dict], rate_card: dict, snapshot_date: str
) -> list[dict]:
    """Match list_prices SKUs to Databricks rate keys. Pure."""

    out: list[dict] = []
    for entry in rate_card["databricks_rate_keys"]:
        pattern = re.compile(entry["sku_regex"])
        for row in rows:
            sku = str(row.get("sku_name") or "")
            if not pattern.search(sku):
                continue
            try:
                price = float(row.get("unit_price"))
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            out.append(
                {
                    "snapshot_date": snapshot_date,
                    "source": "databricks_list_prices",
                    "rate_key": entry["rate_key"],
                    "meter_id": sku,
                    "service_name": "Databricks",
                    "product_name": "",
                    "sku_name": sku,
                    "meter_name": sku,
                    "region": "",
                    "unit": "DBU",
                    "unit_price": price,
                    "currency": str(row.get("currency_code") or ""),
                }
            )
    return out


# --- storage (Delta via the SQL warehouse) ------------------------------------


def create_price_snapshot_table_sql(catalog: str, schema: str) -> str:
    """DDL for estimator_price_snapshots. Pure; runs only in schema_migrations."""

    return (
        f"CREATE TABLE IF NOT EXISTS {catalog}.{schema}.estimator_price_snapshots ("
        "snapshot_date DATE, source STRING, rate_key STRING, meter_id STRING, "
        "service_name STRING, product_name STRING, sku_name STRING, meter_name STRING, "
        "region STRING, unit STRING, unit_price DOUBLE, currency STRING, "
        "environment STRING, ingested_at TIMESTAMP) "
        "COMMENT 'Versioned unit prices (Azure Retail Prices API + "
        "system.billing.list_prices) for the AI solution cost estimator'"
    )


def merge_price_snapshot_sql(catalog: str, schema: str) -> str:
    """Atomically replace one snapshot_date × environment. Pure."""

    fq = f"{catalog}.{schema}.estimator_price_snapshots"
    return (
        f"MERGE INTO {fq} t USING ("
        "SELECT :environment AS environment, item.* "
        f"FROM (SELECT explode(from_json(:rows, '{PRICE_ROW_SCHEMA}')) AS item)"
        ") s "
        "ON t.environment = s.environment AND t.snapshot_date = s.snapshot_date "
        "AND t.source = s.source AND t.rate_key = s.rate_key "
        "AND t.meter_id = s.meter_id AND t.currency = s.currency "
        "WHEN MATCHED THEN UPDATE SET t.unit_price = s.unit_price, t.unit = s.unit, "
        "t.service_name = s.service_name, t.product_name = s.product_name, "
        "t.sku_name = s.sku_name, t.meter_name = s.meter_name, t.region = s.region, "
        "t.ingested_at = current_timestamp() "
        "WHEN NOT MATCHED THEN INSERT "
        "(snapshot_date, source, rate_key, meter_id, service_name, product_name, "
        "sku_name, meter_name, region, unit, unit_price, currency, environment, "
        "ingested_at) "
        "VALUES (s.snapshot_date, s.source, s.rate_key, s.meter_id, s.service_name, "
        "s.product_name, s.sku_name, s.meter_name, s.region, s.unit, s.unit_price, "
        "s.currency, s.environment, current_timestamp()) "
        "WHEN NOT MATCHED BY SOURCE AND t.environment = :environment "
        "AND t.snapshot_date = CAST(:snapshot_date AS DATE) THEN DELETE"
    )


def store_price_snapshot(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    rows: list[dict],
    *,
    snapshot_date: str,
    environment: str,
) -> int:
    """Atomically replace the snapshot for one date/deployment scope."""

    if not environment.strip():
        raise ValueError("environment is required for price snapshot reconciliation")
    day = date.fromisoformat(str(snapshot_date)[:10]).isoformat()
    stray = [r for r in rows if str(r.get("snapshot_date")) != day]
    if stray:
        raise ValueError(f"price rows fall outside snapshot {day}: {stray[:2]}")
    params = {"rows": json.dumps(rows, default=str), "environment": environment,
              "snapshot_date": day}
    try:
        run_query(w, merge_price_snapshot_sql(catalog, schema), warehouse_id, params)
    except Exception as exc:
        raise RuntimeError(
            f"Unable to reconcile required table "
            f"{catalog}.{schema}.estimator_price_snapshots; run the deployment "
            "schema_migrations job and verify writer grants."
        ) from exc
    return len(rows)


def latest_snapshot_sql(catalog: str, schema: str) -> str:
    """Rows of the newest snapshot for one deployment scope and currency. Pure."""

    fq = f"{catalog}.{schema}.estimator_price_snapshots"
    return (
        f"SELECT snapshot_date, source, rate_key, meter_id, meter_name, region, unit, "
        f"unit_price, currency FROM {fq} "
        "WHERE environment = :environment AND currency = :currency "
        f"AND snapshot_date = (SELECT MAX(snapshot_date) FROM {fq} "
        "WHERE environment = :environment AND currency = :currency)"
    )


def read_latest_snapshot(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    *,
    environment: str,
    currency: str = "USD",
) -> list[dict]:
    return run_query(
        w,
        latest_snapshot_sql(catalog, schema),
        warehouse_id,
        {"environment": environment, "currency": currency},
    )


def snapshot_status_sql(catalog: str, schema: str) -> str:
    """Per-source freshness and row counts of the newest snapshot. Pure."""

    fq = f"{catalog}.{schema}.estimator_price_snapshots"
    return (
        "SELECT source, MAX(snapshot_date) AS snapshot_date, COUNT(*) AS rows, "
        "COUNT(DISTINCT rate_key) AS rate_keys "
        f"FROM {fq} WHERE environment = :environment "
        "GROUP BY source ORDER BY source"
    )


def read_snapshot_status(
    w: WorkspaceClient, warehouse_id: str, catalog: str, schema: str, *, environment: str
) -> list[dict]:
    return run_query(
        w, snapshot_status_sql(catalog, schema), warehouse_id, {"environment": environment}
    )


# --- coverage (pure) ----------------------------------------------------------


def classify_price_coverage(
    rate_card: dict, snapshot_rows: list[dict], snapshot_date: str
) -> tuple[list[dict], list[str]]:
    """Findings for required rate keys with no matched meter; notes for optional.

    This is the meter-rot alarm: a rename in Azure's catalog or a Databricks
    SKU change empties a rate key on the next pull and lands here — weeks
    before anyone trusts a wrong estimate.
    """
    matched = {
        str(row.get("rate_key"))
        for row in snapshot_rows
        if str(row.get("snapshot_date")) == str(snapshot_date)
    }
    findings: list[dict] = []
    notes: list[str] = []
    entries = [
        *(("azure_retail", e) for e in rate_card["azure_rate_keys"]),
        *(("databricks_list_prices", e) for e in rate_card["databricks_rate_keys"]),
    ]
    for source, entry in entries:
        key = entry["rate_key"]
        if key in matched:
            continue
        if entry.get("optional"):
            notes.append(f"optional rate key {key} has no matching meter (informational)")
            continue
        findings.append(
            {
                "rate_key": key,
                "source": source,
                "reason": (
                    f"no meter matched rate key {key} in snapshot {snapshot_date}; "
                    "estimates depending on it will show 'price unavailable'"
                ),
                "action": "update-rate-card-meter-regex",
            }
        )
    return findings, notes
