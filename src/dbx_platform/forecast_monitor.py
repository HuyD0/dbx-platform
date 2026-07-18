"""Drift + accuracy monitoring for the Azure cost forecaster.

Two independent signals, both computed with pure functions:

- **Feature drift** — PSI of each feature's recent distribution vs a
  reference window from the same ``cost_features`` table (0.1 warn /
  0.25 alert, the standard bands).
- **Forecast accuracy on matured forecasts** — once a forecasted day has an
  actual in ``azure_costs``, per-series WAPE and P10–P90 coverage tell us
  whether the champion still earns its alias.

``classify_retrain`` folds both into ok / warn / retrain findings. The
monitor CLI exits nonzero on a retrain recommendation so the scheduled job's
failure email doubles as the alert, and findings are stored to
``platform_findings`` so the digest + dashboards see them.
"""

from __future__ import annotations

from datetime import date

from databricks.sdk import WorkspaceClient

from dbx_platform import azure_cost, forecast_features, forecast_train
from dbx_platform.forecast_features import FEATURE_COLUMNS
from dbx_platform.system_tables import run_query

PSI_WARN = 0.1
PSI_ALERT = 0.25
WAPE_ALERT = 0.35
COVERAGE_FLOOR = 0.6
_MIN_SAMPLES = 14
_EPS = 1e-4


# --- PSI (pure) ---------------------------------------------------------------

def psi(expected: list[float], actual: list[float], bins: int = 10) -> float:
    """Population Stability Index of ``actual`` vs ``expected``.

    Bin edges are quantiles of the expected (reference) distribution;
    proportions are clamped to avoid log(0). Standard reading: <0.1 stable,
    0.1–0.25 moderate shift, >0.25 significant shift.
    """
    if not expected or not actual:
        return 0.0
    ref = sorted(expected)
    edges = [ref[int(len(ref) * i / bins)] for i in range(1, bins)]

    def proportions(values: list[float]) -> list[float]:
        counts = [0] * bins
        for v in values:
            k = 0
            while k < len(edges) and v > edges[k]:
                k += 1
            counts[k] += 1
        return [max(c / len(values), _EPS) for c in counts]

    import math

    p_ref, p_act = proportions(expected), proportions(actual)
    return sum((a - r) * math.log(a / r) for r, a in zip(p_ref, p_act, strict=True))


def classify_feature_drift(
    reference_rows: list[dict],
    recent_rows: list[dict],
    warn: float = PSI_WARN,
    alert: float = PSI_ALERT,
) -> list[dict]:
    """PSI per feature column between two windows of feature rows. Pure."""
    findings = []
    if len(reference_rows) < _MIN_SAMPLES or len(recent_rows) < _MIN_SAMPLES:
        return findings
    for col in FEATURE_COLUMNS:
        ref = [forecast_train._num(r.get(col)) for r in reference_rows]
        act = [forecast_train._num(r.get(col)) for r in recent_rows]
        value = psi(ref, act)
        if value >= warn:
            findings.append(
                {
                    "feature": col,
                    "psi": round(value, 4),
                    "reason": f"PSI {value:.3f} vs reference window "
                              f"(warn {warn}, alert {alert})",
                    "action": "drift-alert" if value >= alert else "drift-warn",
                }
            )
    findings.sort(key=lambda f: f["psi"], reverse=True)
    return findings


# --- matured-forecast accuracy (pure) -----------------------------------------

def forecast_error_report(
    forecast_rows: list[dict], actual_rows: list[dict]
) -> list[dict]:
    """Per-series WAPE + interval coverage over matured forecasts. Pure.

    ``forecast_rows``: cost_forecasts rows (run_date, target_date, series,
    p10/p50/p90). ``actual_rows``: daily (usage_date, service_bucket, cost)
    rows; the total series is derived. Only true forecasts count
    (run_date < target_date).
    """
    dense = forecast_features.daily_series(actual_rows)
    per_series: dict[str, dict[str, list[float]]] = {}
    for f in forecast_rows:
        series = str(f.get("series", ""))
        try:
            run_d = date.fromisoformat(str(f.get("run_date", ""))[:10])
            target = date.fromisoformat(str(f.get("target_date", ""))[:10])
        except ValueError:
            continue
        if run_d >= target or series not in dense or target not in dense[series]:
            continue
        actual = dense[series][target]
        p10 = forecast_train._num(f.get("p10"))
        p50 = forecast_train._num(f.get("p50"))
        p90 = forecast_train._num(f.get("p90"))
        acc = per_series.setdefault(series, {"a": [], "p": [], "cover": []})
        acc["a"].append(actual)
        acc["p"].append(p50)
        acc["cover"].append(1.0 if p10 <= actual <= p90 else 0.0)
    report = []
    for series, acc in sorted(per_series.items()):
        report.append(
            {
                "series": series,
                "matured_points": len(acc["a"]),
                "wape": round(forecast_train.wape(acc["a"], acc["p"]), 4),
                "coverage_p10_p90": round(sum(acc["cover"]) / len(acc["cover"]), 3),
            }
        )
    return report


def classify_retrain(
    drift_findings: list[dict],
    error_rows: list[dict],
    wape_alert: float = WAPE_ALERT,
    coverage_floor: float = COVERAGE_FLOOR,
) -> list[dict]:
    """Fold drift + accuracy into ok/warn/retrain findings. Pure."""
    findings = []
    drift_alerts = [f for f in drift_findings if f["action"] == "drift-alert"]
    if drift_alerts:
        worst = drift_alerts[0]
        findings.append(
            {
                "signal": "feature-drift",
                "resource": worst["feature"],
                "reason": f"{len(drift_alerts)} feature(s) past PSI {PSI_ALERT} "
                          f"(worst: {worst['feature']} at {worst['psi']})",
                "action": "retrain-recommended",
            }
        )
    elif drift_findings:
        findings.append(
            {
                "signal": "feature-drift",
                "resource": drift_findings[0]["feature"],
                "reason": f"{len(drift_findings)} feature(s) in the PSI warn band",
                "action": "watch",
            }
        )
    for row in error_rows:
        if row["matured_points"] < _MIN_SAMPLES:
            continue
        if row["wape"] >= wape_alert:
            findings.append(
                {
                    "signal": "forecast-error",
                    "resource": row["series"],
                    "reason": f"matured WAPE {row['wape']} over "
                              f"{row['matured_points']} points "
                              f"(alert {wape_alert})",
                    "action": "retrain-recommended",
                }
            )
        elif row["coverage_p10_p90"] < coverage_floor:
            findings.append(
                {
                    "signal": "interval-coverage",
                    "resource": row["series"],
                    "reason": f"P10-P90 coverage {row['coverage_p10_p90']} "
                              f"below floor {coverage_floor}",
                    "action": "retrain-recommended",
                }
            )
    if not findings:
        findings.append(
            {"signal": "all", "resource": "azure_cost_forecaster",
             "reason": "no drift past thresholds; matured accuracy within bounds",
             "action": "ok"}
        )
    return findings


# --- runner -------------------------------------------------------------------

def fetch_forecast_rows(
    w: WorkspaceClient, warehouse_id: str, catalog: str, schema: str, days: int
) -> list[dict]:
    return run_query(
        w,
        "SELECT run_date, target_date, series, p10, p50, p90 "
        f"FROM {catalog}.{schema}.cost_forecasts "
        "WHERE target_date >= DATE_SUB(CURRENT_DATE(), :days)",
        warehouse_id,
        {"days": days},
        row_limit=50000,
    )


def split_windows(
    feature_rows: list[dict], recent_days: int = 14, reference_days: int = 76
) -> tuple[list[dict], list[dict]]:
    """Split feature rows into (reference, recent) windows by date. Pure."""
    dated = []
    for r in feature_rows:
        try:
            dated.append((date.fromisoformat(str(r.get("feature_date", ""))[:10]), r))
        except ValueError:
            continue
    if not dated:
        return [], []
    latest = max(d for d, _ in dated)
    recent, reference = [], []
    for d, r in dated:
        age = (latest - d).days
        if age < recent_days:
            recent.append(r)
        elif age < recent_days + reference_days:
            reference.append(r)
    return reference, recent


def store_findings(
    w: WorkspaceClient, warehouse_id: str, catalog: str, schema: str,
    findings: list[dict],
) -> None:
    """Merge lifecycle-aware, workspace-scoped performance findings."""
    from dbx_platform.digest import store_findings as store_canonical_findings

    store_canonical_findings(
        w,
        warehouse_id,
        catalog,
        schema,
        {"performance/forecast-monitor": findings},
    )


def run_monitoring(
    w: WorkspaceClient, warehouse_id: str, catalog: str, schema: str, days: int = 45
) -> tuple[list[dict], list[dict], list[dict]]:
    """Fetch windows, run the pure checks. Returns (drift, errors, findings)."""
    feature_rows = forecast_train.coerce_feature_rows(
        forecast_features.fetch_features(w, warehouse_id, catalog, schema)
    )
    reference, recent = split_windows(feature_rows)
    drift = classify_feature_drift(reference, recent)
    forecasts = fetch_forecast_rows(w, warehouse_id, catalog, schema, days)
    actuals = azure_cost.fetch_daily_buckets(w, warehouse_id, catalog, schema, days)
    errors = forecast_error_report(forecasts, actuals)
    findings = classify_retrain(drift, errors)
    return drift, errors, findings
