"""Feature engineering for the Azure cost forecaster.

Turns the daily per-bucket spend in ``azure_costs`` into a supervised
feature table (``cost_features``) with lag, rolling-window and calendar
features per series ("total" plus one series per service bucket).

Leakage rule: every feature for date *d* is computed **only from days
strictly before d** — the label is the spend on d itself. The pure
functions here are unit-tested for that property; only the store/fetch
wrappers touch the warehouse.

Bump ``FEATURE_SET_VERSION`` whenever the feature list changes so training
runs, forecasts and drift checks can tell feature generations apart.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from databricks.sdk import WorkspaceClient

from dbx_platform.system_tables import run_query

FEATURE_SET_VERSION = 1

TOTAL_SERIES = "total"

LAGS = (1, 7, 14, 28)
ROLL_WINDOWS = (7, 28)

# Numeric model inputs, in stable order (the model signature depends on it).
FEATURE_COLUMNS = [
    *[f"lag_{n}" for n in LAGS],
    *[f"roll_mean_{n}" for n in ROLL_WINDOWS],
    "roll_std_7",
    "dow",
    "dom",
    "month",
    "is_weekend",
    "is_month_start",
    "is_month_end",
]

_HISTORY_MIN_DAYS = max(LAGS)  # need a full lag window before the first row

FEATURE_ROW_SCHEMA = (
    "array<struct<series:string,feature_date:date,cost:double,"
    + ",".join(f"{c}:double" for c in FEATURE_COLUMNS)
    + ",feature_set_version:int>>"
)


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def daily_series(rows: list[dict]) -> dict[str, dict[date, float]]:
    """Group (usage_date, service_bucket, cost) rows into per-series daily
    totals, adding the cross-bucket "total" series. Pure.

    Missing days inside each series' observed range are filled with 0 —
    a day with no billed usage is a real zero, and lags need a dense index.
    """
    series: dict[str, dict[date, float]] = {}
    for r in rows:
        try:
            d = date.fromisoformat(str(r.get("usage_date", ""))[:10])
        except ValueError:
            continue
        bucket = str(r.get("service_bucket", "") or "other")
        cost = _num(r.get("cost"))
        series.setdefault(bucket, {})[d] = series.get(bucket, {}).get(d, 0.0) + cost
        series.setdefault(TOTAL_SERIES, {})[d] = (
            series.get(TOTAL_SERIES, {}).get(d, 0.0) + cost
        )
    dense: dict[str, dict[date, float]] = {}
    for name, daily in series.items():
        if not daily:
            continue
        lo, hi = min(daily), max(daily)
        dense[name] = {
            lo + timedelta(days=i): daily.get(lo + timedelta(days=i), 0.0)
            for i in range((hi - lo).days + 1)
        }
    return dense


def features_for_date(daily: dict[date, float], d: date) -> dict | None:
    """Features for one series/date from days strictly before ``d``. Pure.

    Returns None when the history window is too short. Shared by training
    (label = daily[d]) and inference (d in the future, no label).
    """
    history = [daily.get(d - timedelta(days=i), None) for i in range(1, max(LAGS) + 1)]
    if any(v is None for v in history[: _HISTORY_MIN_DAYS]):
        return None
    feats: dict[str, float] = {}
    for n in LAGS:
        feats[f"lag_{n}"] = float(history[n - 1])
    for n in ROLL_WINDOWS:
        window = [float(v) for v in history[:n]]
        feats[f"roll_mean_{n}"] = sum(window) / n
    w7 = [float(v) for v in history[:7]]
    m7 = feats["roll_mean_7"]
    feats["roll_std_7"] = (sum((v - m7) ** 2 for v in w7) / 7) ** 0.5
    feats["dow"] = float(d.weekday())
    feats["dom"] = float(d.day)
    feats["month"] = float(d.month)
    feats["is_weekend"] = 1.0 if d.weekday() >= 5 else 0.0
    feats["is_month_start"] = 1.0 if d.day == 1 else 0.0
    next_day = d + timedelta(days=1)
    feats["is_month_end"] = 1.0 if next_day.day == 1 else 0.0
    return feats


def build_features(rows: list[dict]) -> list[dict]:
    """Feature rows for every series/date with enough history. Pure."""
    out: list[dict] = []
    for name, daily in sorted(daily_series(rows).items()):
        for d in sorted(daily):
            feats = features_for_date(daily, d)
            if feats is None:
                continue
            out.append(
                {
                    "series": name,
                    "feature_date": d.isoformat(),
                    "cost": daily[d],
                    **feats,
                    "feature_set_version": FEATURE_SET_VERSION,
                }
            )
    return out


# --- storage ------------------------------------------------------------------

def create_features_table_sql(catalog: str, schema: str) -> str:
    cols = ", ".join(f"{c} DOUBLE" for c in FEATURE_COLUMNS)
    return (
        f"CREATE TABLE IF NOT EXISTS {catalog}.{schema}.cost_features ("
        f"series STRING, feature_date DATE, cost DOUBLE, {cols}, "
        "feature_set_version INT, computed_at TIMESTAMP) "
        "COMMENT 'Engineered features for the Azure cost forecaster'"
    )


def merge_features_sql(catalog: str, schema: str) -> str:
    fq = f"{catalog}.{schema}.cost_features"
    cols = ["series", "feature_date", "cost", *FEATURE_COLUMNS, "feature_set_version"]
    select = ", ".join(f"item.{c}" for c in cols)
    updates = ", ".join(f"t.{c} = s.{c}" for c in cols if c not in ("series", "feature_date"))
    inserts = ", ".join(cols)
    values = ", ".join(f"s.{c}" for c in cols)
    return (
        f"MERGE INTO {fq} t USING ("
        f"SELECT {select} FROM (SELECT explode(from_json(:rows, "
        f"'{FEATURE_ROW_SCHEMA}')) AS item)) s "
        "ON t.series = s.series AND t.feature_date = s.feature_date "
        f"WHEN MATCHED THEN UPDATE SET {updates}, t.computed_at = current_timestamp() "
        f"WHEN NOT MATCHED THEN INSERT ({inserts}, computed_at) "
        f"VALUES ({values}, current_timestamp())"
    )


_MERGE_BATCH_ROWS = 1000


def store_features(
    w: WorkspaceClient, warehouse_id: str, catalog: str, schema: str, rows: list[dict]
) -> int:
    """MERGE into the feature table created by deployment migrations."""

    sql = merge_features_sql(catalog, schema)
    try:
        for i in range(0, len(rows), _MERGE_BATCH_ROWS):
            run_query(
                w, sql, warehouse_id,
                {"rows": json.dumps(rows[i:i + _MERGE_BATCH_ROWS], default=str)},
            )
    except Exception as exc:
        raise RuntimeError(
            f"Unable to write required table {catalog}.{schema}.cost_features; "
            "run the deployment schema_migrations job and verify writer grants."
        ) from exc
    return len(rows)


def fetch_features(
    w: WorkspaceClient, warehouse_id: str, catalog: str, schema: str, days: int = 3650
) -> list[dict]:
    cols = ", ".join(["series", "feature_date", "cost", *FEATURE_COLUMNS,
                      "feature_set_version"])
    return run_query(
        w,
        f"SELECT {cols} FROM {catalog}.{schema}.cost_features "
        "WHERE feature_date >= DATE_SUB(CURRENT_DATE(), :days) "
        "ORDER BY series, feature_date",
        warehouse_id,
        {"days": days},
        row_limit=50000,
    )
