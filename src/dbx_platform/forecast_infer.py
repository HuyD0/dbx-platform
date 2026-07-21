"""Batch inference for the Azure cost forecaster.

Serving mode is a daily batch job (the consumers — dashboard, Console app,
CLI — read tables), so "serving" here means: load the Unity Catalog model by
its ``@champion`` alias, roll the forecast forward ``horizon`` days, and
MERGE the quantile forecasts into ``cost_forecasts``.

Multi-step forecasting is recursive: features for day d+2 need the (still
unknown) spend on d+1, so each step's P50 prediction extends the history the
next step's lags are computed from. ``recursive_forecast`` is pure — the
model is injected as a plain callable — and unit-tested offline.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, timedelta

from databricks.sdk import WorkspaceClient

from dbx_platform import azure_cost, forecast_features
from dbx_platform.forecast_features import FEATURE_COLUMNS, FEATURE_SET_VERSION
from dbx_platform.system_tables import run_query

FORECAST_ROW_SCHEMA = (
    "array<struct<run_date:date,target_date:date,series:string,"
    "p10:double,p50:double,p90:double,model_version:string,"
    "feature_set_version:int>>"
)


def recursive_forecast(
    dense: dict[str, dict[date, float]],
    horizon: int,
    predict_fn: Callable[[list[dict]], list[tuple[float, float, float]]],
) -> list[dict]:
    """Roll every series forward ``horizon`` days. Pure given ``predict_fn``.

    ``predict_fn`` takes [{"series": ..., <FEATURE_COLUMNS>...}, ...] and
    returns one (p10, p50, p90) per input row. Each step's p50 is appended to
    the series history so later steps' lag features see it. Series without
    enough history for the feature window are skipped.
    """
    histories = {name: dict(daily) for name, daily in dense.items() if daily}
    if not histories:
        return []
    start = max(max(daily) for daily in histories.values())
    out: list[dict] = []
    for step in range(1, horizon + 1):
        target = start + timedelta(days=step)
        batch: list[tuple[str, dict]] = []
        for name in sorted(histories):
            feats = forecast_features.features_for_date(histories[name], target)
            if feats is not None:
                batch.append((name, feats))
        if not batch:
            break
        preds = predict_fn([{"series": n, **f} for n, f in batch])
        for (name, _), (p10, p50, p90) in zip(batch, preds, strict=True):
            histories[name][target] = p50
            out.append(
                {
                    "target_date": target.isoformat(),
                    "series": name,
                    "p10": round(float(p10), 4),
                    "p50": round(float(p50), 4),
                    "p90": round(float(p90), 4),
                }
            )
    return out


# --- storage ------------------------------------------------------------------

def create_forecasts_table_sql(catalog: str, schema: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {catalog}.{schema}.cost_forecasts ("
        "run_date DATE, target_date DATE, series STRING, "
        "p10 DOUBLE, p50 DOUBLE, p90 DOUBLE, model_version STRING, "
        "feature_set_version INT, created_at TIMESTAMP) "
        "COMMENT 'Azure cost forecasts (P10/P50/P90) from the @champion model'"
    )


def merge_forecasts_sql(catalog: str, schema: str) -> str:
    fq = f"{catalog}.{schema}.cost_forecasts"
    return (
        f"MERGE INTO {fq} t USING ("
        "SELECT item.run_date, item.target_date, item.series, item.p10, "
        "item.p50, item.p90, item.model_version, item.feature_set_version "
        f"FROM (SELECT explode(from_json(:rows, '{FORECAST_ROW_SCHEMA}')) AS item)"
        ") s "
        "ON t.run_date = s.run_date AND t.target_date = s.target_date "
        "AND t.series = s.series "
        "WHEN MATCHED THEN UPDATE SET t.p10 = s.p10, t.p50 = s.p50, "
        "t.p90 = s.p90, t.model_version = s.model_version, "
        "t.feature_set_version = s.feature_set_version, "
        "t.created_at = current_timestamp() "
        "WHEN NOT MATCHED THEN INSERT "
        "(run_date, target_date, series, p10, p50, p90, model_version, "
        "feature_set_version, created_at) "
        "VALUES (s.run_date, s.target_date, s.series, s.p10, s.p50, s.p90, "
        "s.model_version, s.feature_set_version, current_timestamp())"
    )


def store_forecasts(
    w: WorkspaceClient, warehouse_id: str, catalog: str, schema: str, rows: list[dict]
) -> int:
    """MERGE into the forecast table created by deployment migrations."""

    try:
        run_query(
            w,
            merge_forecasts_sql(catalog, schema),
            warehouse_id,
            {"rows": json.dumps(rows, default=str)},
        )
    except Exception as exc:
        raise RuntimeError(
            f"Unable to write required table {catalog}.{schema}.cost_forecasts; "
            "run the deployment schema_migrations job and verify writer grants."
        ) from exc
    return len(rows)


# --- runner -------------------------------------------------------------------

def run_inference(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    model_name: str,
    horizon: int,
    lookback_days: int = 120,
    *,
    workspace_id: str,
    environment: str,
) -> list[dict]:
    """Load @champion, forecast, persist. Returns summary rows for emit."""
    try:
        import mlflow
        from mlflow.tracking import MlflowClient
    except ImportError as e:
        raise ImportError(
            "Forecasting libraries not installed. "
            "Run: pip install 'dbx-platform[forecast]'"
        ) from e
    import pandas as pd

    mlflow.set_registry_uri("databricks-uc")
    uc_name = f"{catalog}.{schema}.{model_name}"
    version = MlflowClient().get_model_version_by_alias(uc_name, "champion").version
    model = mlflow.pyfunc.load_model(f"models:/{uc_name}@champion")

    rows = azure_cost.fetch_daily_buckets(
        w,
        warehouse_id,
        catalog,
        schema,
        lookback_days,
        workspace_id=workspace_id,
        environment=environment,
    )
    dense = forecast_features.daily_series(rows)
    if not dense:
        raise ValueError(
            f"no rows in {catalog}.{schema}.azure_costs — run "
            "'dbx-platform azure-cost pull' first."
        )

    def predict_fn(feature_rows: list[dict]) -> list[tuple[float, float, float]]:
        frame = pd.DataFrame(feature_rows)[["series", *FEATURE_COLUMNS]]
        preds = model.predict(frame)
        return list(zip(preds["p10"], preds["p50"], preds["p90"], strict=True))

    forecasts = recursive_forecast(dense, horizon, predict_fn)
    run_day = date.today().isoformat()
    for f in forecasts:
        f.update(run_date=run_day, model_version=str(version),
                 feature_set_version=FEATURE_SET_VERSION)
    stored = store_forecasts(w, warehouse_id, catalog, schema, forecasts)
    series_count = len({f["series"] for f in forecasts})
    return [
        {"model": f"{uc_name}@champion (v{version})", "series": series_count,
         "horizon_days": horizon, "rows_written": stored}
    ]
