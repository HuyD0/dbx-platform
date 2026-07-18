"""Train, track and (conditionally) promote the Azure cost forecaster.

MLOps loop, kept deliberately explicit:

1. Rolling-origin backtest of every candidate — a seasonal-naive baseline
   (the floor any model must beat) and LightGBM quantile models — on the
   ``cost_features`` table.
2. Every candidate is an MLflow run in the shared experiment (params,
   per-fold metrics, git commit tag, feature-set version).
3. The LightGBM candidate is registered to Unity Catalog and aliased
   ``@challenger``. ``@champion`` moves only when ``should_promote`` says the
   challenger's backtest WAPE beats the incumbent's by a margin — batch
   inference resolves the model *only* by alias, never by version number.

The decision logic (metrics, fold splitting, promotion) is pure and tested
offline; everything importing mlflow/lightgbm/pandas is lazy so the core
wheel stays lean (``pip install 'dbx-platform[forecast]'``).
"""

from __future__ import annotations

from datetime import date, timedelta

from databricks.sdk import WorkspaceClient

from dbx_platform import forecast_features
from dbx_platform.forecast_features import FEATURE_COLUMNS, FEATURE_SET_VERSION

QUANTILES = (0.1, 0.5, 0.9)

CHAMPION_ALIAS = "champion"
CHALLENGER_ALIAS = "challenger"
WAPE_TAG = "backtest_wape"


# --- metrics (pure) -----------------------------------------------------------

def wape(actuals: list[float], preds: list[float]) -> float:
    """Weighted absolute percentage error: sum|a-p| / sum|a|."""
    denom = sum(abs(a) for a in actuals)
    if denom == 0:
        return 0.0 if all(p == 0 for p in preds) else float("inf")
    return sum(abs(a - p) for a, p in zip(actuals, preds, strict=True)) / denom


def smape(actuals: list[float], preds: list[float]) -> float:
    """Symmetric MAPE in [0, 2]; zero-cost days are skipped, not divided by."""
    terms = [
        2 * abs(a - p) / (abs(a) + abs(p))
        for a, p in zip(actuals, preds, strict=True)
        if (abs(a) + abs(p)) > 0
    ]
    return sum(terms) / len(terms) if terms else 0.0


def pinball(actuals: list[float], preds: list[float], q: float) -> float:
    """Quantile (pinball) loss — what the P10/P90 heads are judged on."""
    terms = [
        max(q * (a - p), (q - 1) * (a - p))
        for a, p in zip(actuals, preds, strict=True)
    ]
    return sum(terms) / len(terms) if terms else 0.0


# --- backtesting (pure) -------------------------------------------------------

def backtest_folds(
    dates: list[date], n_folds: int, horizon: int
) -> list[tuple[date, list[date]]]:
    """Rolling-origin folds: the last ``n_folds`` blocks of ``horizon`` days
    each become a test window, training on everything strictly before it.

    Returns [(train_end, test_dates), ...] oldest fold first. Raises if there
    is not at least one horizon of training data before the first fold.
    """
    if not dates:
        raise ValueError("no feature dates to backtest on")
    ordered = sorted(set(dates))
    needed = (n_folds + 1) * horizon
    if len(ordered) < needed:
        raise ValueError(
            f"need >= {needed} days of features for {n_folds} folds x {horizon}d "
            f"horizon; have {len(ordered)}. Backfill with 'azure-cost pull' first."
        )
    folds = []
    for k in range(n_folds, 0, -1):
        test = ordered[len(ordered) - k * horizon: len(ordered) - (k - 1) * horizon]
        folds.append((test[0] - timedelta(days=1), test))
    return folds


def seasonal_naive(daily: dict[date, float], targets: list[date]) -> list[float]:
    """Same-weekday-last-week forecast, walking back in 7d steps until a
    known day is found. The baseline every learned model must beat."""
    preds = []
    for d in targets:
        back = d - timedelta(days=7)
        for _ in range(60):
            if back in daily:
                preds.append(daily[back])
                break
            back -= timedelta(days=7)
        else:
            preds.append(0.0)
    return preds


def should_promote(
    champion_wape: float | None, challenger_wape: float, min_improvement: float = 0.01
) -> bool:
    """Champion/challenger gate. Promote when there is no champion yet, or the
    challenger's backtest WAPE is at least ``min_improvement`` (relative)
    better. Ties keep the incumbent — stability beats churn."""
    if challenger_wape != challenger_wape or challenger_wape == float("inf"):
        return False
    if champion_wape is None:
        return True
    return challenger_wape < champion_wape * (1 - min_improvement)


# --- feature-row plumbing (pure) ----------------------------------------------

def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def coerce_feature_rows(rows: list[dict]) -> list[dict]:
    """Statement Execution returns strings; make numerics numeric. Pure."""
    out = []
    for r in rows:
        c = {"series": str(r.get("series", "")),
             "feature_date": str(r.get("feature_date", ""))[:10],
             "cost": _num(r.get("cost"))}
        for col in FEATURE_COLUMNS:
            c[col] = _num(r.get(col))
        out.append(c)
    return out


# --- training (lazy heavy deps) -----------------------------------------------

def _require_forecast_deps():
    try:
        import lightgbm  # noqa: F401
        import mlflow  # noqa: F401
        import pandas  # noqa: F401
    except ImportError as e:
        raise ImportError(
            "Forecasting libraries not installed. "
            "Run: pip install 'dbx-platform[forecast]'"
        ) from e


def _lgbm_params(q: float) -> dict:
    return {
        "objective": "quantile",
        "alpha": q,
        "n_estimators": 300,
        "learning_rate": 0.05,
        "num_leaves": 15,
        "min_child_samples": 10,
        "verbose": -1,
    }


def _fit_quantile_models(frame, feature_cols: list[str]):
    import lightgbm as lgb

    models = {}
    x, y = frame[feature_cols], frame["cost"]
    for q in QUANTILES:
        m = lgb.LGBMRegressor(**_lgbm_params(q))
        m.fit(x, y)
        models[q] = m
    return models


def run_training(
    w: WorkspaceClient,
    warehouse_id: str,
    catalog: str,
    schema: str,
    model_name: str,
    experiment: str,
    n_folds: int = 4,
    horizon: int = 14,
    min_improvement: float = 0.01,
) -> list[dict]:
    """Backtest candidates, log experiments, register + alias the model.

    Returns summary rows for ``emit``. The registry name is
    ``{catalog}.{schema}.{model_name}``; batch inference loads ``@champion``.
    """
    _require_forecast_deps()
    import mlflow
    import pandas as pd
    from mlflow.models import infer_signature
    from mlflow.tracking import MlflowClient

    rows = coerce_feature_rows(
        forecast_features.fetch_features(w, warehouse_id, catalog, schema)
    )
    if not rows:
        raise ValueError(
            f"no rows in {catalog}.{schema}.cost_features — run "
            "'dbx-platform forecast build-features' first."
        )
    frame = pd.DataFrame(rows)
    frame["feature_date"] = pd.to_datetime(frame["feature_date"]).dt.date
    series_names = sorted(frame["series"].unique())
    series_map = {name: i for i, name in enumerate(series_names)}
    frame["series_code"] = frame["series"].map(series_map).astype(float)
    feature_cols = [*FEATURE_COLUMNS, "series_code"]

    folds = backtest_folds(sorted(frame["feature_date"].unique()), n_folds, horizon)

    # Per-series daily history for the baseline.
    daily_by_series: dict[str, dict[date, float]] = {}
    for r in rows:
        daily_by_series.setdefault(r["series"], {})[
            date.fromisoformat(r["feature_date"])
        ] = r["cost"]

    naive_a: list[float] = []
    naive_p: list[float] = []
    lgbm_a: list[float] = []
    lgbm_p: list[float] = []
    lgbm_p10: list[float] = []
    lgbm_p90: list[float] = []
    for train_end, test_dates in folds:
        train = frame[frame["feature_date"] <= train_end]
        test = frame[frame["feature_date"].isin(test_dates)]
        if train.empty or test.empty:
            continue
        models = _fit_quantile_models(train, feature_cols)
        preds = {q: models[q].predict(test[feature_cols]) for q in QUANTILES}
        actual = test["cost"].tolist()
        lgbm_a += actual
        lgbm_p += list(preds[0.5])
        lgbm_p10 += list(preds[0.1])
        lgbm_p90 += list(preds[0.9])
        for name in series_names:
            mask = test["series"] == name
            targets = test[mask]["feature_date"].tolist()
            history = {
                d: v for d, v in daily_by_series[name].items() if d <= train_end
            }
            naive_a += test[mask]["cost"].tolist()
            naive_p += seasonal_naive(history, targets)

    metrics = {
        "seasonal-naive": {"wape": wape(naive_a, naive_p),
                           "smape": smape(naive_a, naive_p)},
        "lightgbm-quantile": {
            "wape": wape(lgbm_a, lgbm_p),
            "smape": smape(lgbm_a, lgbm_p),
            "pinball_p10": pinball(lgbm_a, lgbm_p10, 0.1),
            "pinball_p90": pinball(lgbm_a, lgbm_p90, 0.9),
        },
    }

    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(experiment)
    uc_name = f"{catalog}.{schema}.{model_name}"
    common_tags = {
        "feature_set_version": str(FEATURE_SET_VERSION),
        "n_folds": str(n_folds),
        "horizon_days": str(horizon),
        "series": ",".join(series_names),
    }

    with mlflow.start_run(run_name="seasonal-naive-backtest", tags=common_tags):
        mlflow.log_metrics({f"backtest_{k}": v
                            for k, v in metrics["seasonal-naive"].items()})

    with mlflow.start_run(run_name="lightgbm-quantile-backtest",
                          tags=common_tags) as run:
        mlflow.log_params({**_lgbm_params(0.5), "quantiles": str(QUANTILES)})
        mlflow.log_metrics({f"backtest_{k}": v
                            for k, v in metrics["lightgbm-quantile"].items()})
        final_models = _fit_quantile_models(frame, feature_cols)
        wrapper = make_quantile_forecaster(final_models, feature_cols, series_map)
        example = frame[["series", *FEATURE_COLUMNS]].head(5)
        signature = infer_signature(example, wrapper.predict(None, example))
        mlflow.pyfunc.log_model(
            name="model",
            python_model=wrapper,
            signature=signature,
            input_example=example,
            registered_model_name=uc_name,
        )
        run_id = run.info.run_id

    client = MlflowClient()
    versions = client.search_model_versions(f"name = '{uc_name}'")
    new_version = max(int(v.version) for v in versions if v.run_id == run_id)
    challenger_wape = metrics["lightgbm-quantile"]["wape"]
    client.set_model_version_tag(uc_name, str(new_version), WAPE_TAG,
                                 f"{challenger_wape:.6f}")
    client.set_registered_model_alias(uc_name, CHALLENGER_ALIAS, str(new_version))

    champion_wape = None
    try:
        champ = client.get_model_version_by_alias(uc_name, CHAMPION_ALIAS)
        champion_wape = float(champ.tags.get(WAPE_TAG, "nan"))
        if champion_wape != champion_wape:  # tag missing/NaN: treat as absent
            champion_wape = None
    except Exception:  # noqa: BLE001 — no champion yet is the normal first run
        champ = None

    promoted = should_promote(champion_wape, challenger_wape, min_improvement)
    if promoted:
        client.set_registered_model_alias(uc_name, CHAMPION_ALIAS, str(new_version))

    return [
        {"candidate": "seasonal-naive", "wape": round(metrics["seasonal-naive"]["wape"], 4),
         "smape": round(metrics["seasonal-naive"]["smape"], 4), "registered": ""},
        {"candidate": "lightgbm-quantile", "wape": round(challenger_wape, 4),
         "smape": round(metrics["lightgbm-quantile"]["smape"], 4),
         "registered": f"{uc_name} v{new_version}"},
        {"candidate": "promotion",
         "wape": round(champion_wape, 4) if champion_wape is not None else "",
         "smape": "",
         "registered": (f"@{CHAMPION_ALIAS} -> v{new_version}" if promoted
                        else f"kept incumbent @{CHAMPION_ALIAS}")},
    ]


def make_quantile_forecaster(models, feature_cols, series_map):
    """Pyfunc wrapper holding the three quantile boosters + series encoding.

    Defined lazily (mlflow import) so the core wheel stays lean; cloudpickle
    captures the class with the logged model. Input: a DataFrame with a
    ``series`` column plus FEATURE_COLUMNS. Output: p10/p50/p90, with
    p10/p90 clamped around p50 so the band never inverts on sparse data.
    """
    import mlflow

    class QuantileForecaster(mlflow.pyfunc.PythonModel):
        def __init__(self, models, feature_cols, series_map):
            self._models = models
            self._feature_cols = feature_cols
            self._series_map = series_map

        def predict(self, context, model_input, params=None):
            import pandas as pd

            frame = model_input.copy()
            frame["series_code"] = (
                frame["series"].map(self._series_map).fillna(-1).astype(float)
            )
            x = frame[self._feature_cols]
            p10 = self._models[0.1].predict(x)
            p50 = self._models[0.5].predict(x)
            p90 = self._models[0.9].predict(x)
            out = pd.DataFrame({"p50": p50})
            out["p10"] = [min(a, b) for a, b in zip(p10, p50, strict=True)]
            out["p90"] = [max(a, b) for a, b in zip(p90, p50, strict=True)]
            return out[["p10", "p50", "p90"]].clip(lower=0)

    return QuantileForecaster(models, feature_cols, series_map)
