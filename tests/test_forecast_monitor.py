import random
from datetime import date, timedelta

import pytest

from dbx_platform.forecast_infer import (
    recursive_forecast,
    store_forecasts,
    validate_forecast_alignment,
)
from dbx_platform.forecast_monitor import (
    classify_feature_drift,
    classify_retrain,
    forecast_coverage_report,
    forecast_error_report,
    psi,
    split_windows,
)

# --- psi ------------------------------------------------------------------------


def test_psi_identical_distributions_near_zero():
    rng = random.Random(7)
    values = [rng.gauss(100, 10) for _ in range(500)]
    assert psi(values, list(values)) < 0.01


def test_psi_shifted_distribution_is_large():
    rng = random.Random(7)
    ref = [rng.gauss(100, 10) for _ in range(500)]
    act = [rng.gauss(160, 10) for _ in range(500)]
    assert psi(ref, act) > 0.25


def test_psi_empty_inputs():
    assert psi([], [1.0]) == 0.0
    assert psi([1.0], []) == 0.0


# --- classify_feature_drift -----------------------------------------------------

def _feature_rows(n, value_fn, start=date(2026, 6, 1)):
    rows = []
    for i in range(n):
        rows.append({"feature_date": (start + timedelta(days=i)).isoformat(),
                     **{c: value_fn(i) for c in ("lag_1", "lag_7", "lag_14", "lag_28",
                                                 "roll_mean_7", "roll_mean_28",
                                                 "roll_std_7", "dow", "dom", "month",
                                                 "is_weekend", "is_month_start",
                                                 "is_month_end")}})
    return rows


def test_no_drift_no_findings():
    ref = _feature_rows(60, lambda i: 100.0 + (i % 5))
    act = _feature_rows(20, lambda i: 100.0 + (i % 5))
    assert classify_feature_drift(ref, act) == []


def test_shifted_features_flagged_with_alert():
    ref = _feature_rows(60, lambda i: 100.0 + (i % 5))
    act = _feature_rows(20, lambda i: 500.0 + (i % 5))
    findings = classify_feature_drift(ref, act)
    assert findings
    assert findings[0]["action"] == "drift-alert"


def test_small_windows_are_skipped():
    ref = _feature_rows(5, lambda i: 100.0)
    act = _feature_rows(5, lambda i: 500.0)
    assert classify_feature_drift(ref, act) == []


# --- forecast_error_report ------------------------------------------------------

def _actuals(costs, bucket="databricks", start=date(2026, 7, 1)):
    return [{"usage_date": (start + timedelta(days=i)).isoformat(),
             "service_bucket": bucket, "cost": c} for i, c in enumerate(costs)]


def test_error_report_joins_matured_only():
    actuals = _actuals([100.0] * 5)
    forecasts = [
        # matured true forecast (run before target)
        {"run_date": "2026-06-30", "target_date": "2026-07-02",
         "series": "databricks", "p10": 90, "p50": 110, "p90": 130},
        # run on/after target: not a true forecast, excluded
        {"run_date": "2026-07-03", "target_date": "2026-07-03",
         "series": "databricks", "p10": 0, "p50": 0, "p90": 0},
        # target with no actual yet: excluded
        {"run_date": "2026-07-01", "target_date": "2026-09-01",
         "series": "databricks", "p10": 0, "p50": 0, "p90": 0},
    ]
    report = forecast_error_report(forecasts, actuals)
    assert len(report) == 1
    row = report[0]
    assert row["matured_points"] == 1
    assert row["wape"] == 0.1
    assert row["coverage_p10_p90"] == 1.0


def test_error_report_coverage_counts_band_misses():
    actuals = _actuals([100.0, 100.0])
    forecasts = [
        {"run_date": "2026-06-30", "target_date": "2026-07-01",
         "series": "databricks", "p10": 90, "p50": 100, "p90": 110},
        {"run_date": "2026-06-30", "target_date": "2026-07-02",
         "series": "databricks", "p10": 150, "p50": 160, "p90": 170},
    ]
    assert forecast_error_report(forecasts, actuals)[0]["coverage_p10_p90"] == 0.5


def test_forecast_coverage_distinguishes_missing_and_not_yet_mature_actuals():
    actuals = _actuals([100.0] * 5)
    forecasts = [
        {
            "run_date": "2026-06-30",
            "target_date": "2026-07-02",
            "series": "databricks",
        },
        {
            "run_date": "2026-06-20",
            "target_date": "2026-06-29",
            "series": "search",
        },
        {
            "run_date": "2026-07-01",
            "target_date": "2026-07-10",
            "series": "databricks",
        },
    ]
    report = {row["series"]: row for row in forecast_coverage_report(forecasts, actuals)}
    assert report["databricks"]["matured_points"] == 1
    assert report["databricks"]["not_yet_mature"] == 1
    assert report["databricks"]["missing_actuals"] == 0
    assert report["search"]["missing_actuals"] == 1
    assert report["search"]["not_yet_mature"] == 0


# --- classify_retrain -----------------------------------------------------------

def test_clean_state_is_ok():
    findings = classify_retrain([], [])
    assert [f["action"] for f in findings] == ["ok"]


def test_drift_alert_recommends_retrain():
    drift = [{"feature": "lag_7", "psi": 0.4, "action": "drift-alert",
              "reason": "x"}]
    findings = classify_retrain(drift, [])
    assert findings[0]["action"] == "retrain-recommended"


def test_drift_warn_only_watches():
    drift = [{"feature": "lag_7", "psi": 0.15, "action": "drift-warn",
              "reason": "x"}]
    assert classify_retrain(drift, [])[0]["action"] == "watch"


def test_bad_wape_recommends_retrain():
    errors = [{"series": "total", "matured_points": 30, "wape": 0.5,
               "coverage_p10_p90": 0.9}]
    findings = classify_retrain([], errors)
    assert findings[0]["action"] == "retrain-recommended"
    assert findings[0]["signal"] == "forecast-error"


def test_low_coverage_recommends_retrain():
    errors = [{"series": "total", "matured_points": 30, "wape": 0.1,
               "coverage_p10_p90": 0.3}]
    assert classify_retrain([], errors)[0]["signal"] == "interval-coverage"


def test_few_matured_points_not_judged():
    errors = [{"series": "total", "matured_points": 3, "wape": 0.9,
               "coverage_p10_p90": 0.0}]
    assert classify_retrain([], errors)[0]["action"] == "ok"


def test_missing_matured_actual_is_a_source_data_finding():
    coverage = [
        {
            "series": "search",
            "forecast_points": 1,
            "matured_points": 0,
            "not_yet_mature": 0,
            "missing_actuals": 1,
            "invalid_forecasts": 0,
        }
    ]
    finding = classify_retrain([], [], coverage_rows=coverage)[0]
    assert finding["signal"] == "forecast-actual-alignment"
    assert finding["action"] == "source-data-missing"


# --- split_windows --------------------------------------------------------------

def test_split_windows_partitions_by_age():
    rows = _feature_rows(40, lambda i: 1.0)
    reference, recent = split_windows(rows, recent_days=14, reference_days=76)
    assert len(recent) == 14
    assert len(reference) == 26
    latest = max(r["feature_date"] for r in rows)
    assert all(r["feature_date"] != latest for r in reference)


# --- recursive_forecast (pure, fake model) --------------------------------------

def test_recursive_forecast_feeds_predictions_forward():
    start = date(2026, 6, 1)
    dense = {"total": {start + timedelta(days=i): 100.0 for i in range(40)}}
    seen_lags = []

    def fake_predict(rows):
        seen_lags.append(rows[0]["lag_1"])
        return [(50.0, 70.0, 90.0) for _ in rows]

    out = recursive_forecast(dense, horizon=3, predict_fn=fake_predict)
    assert len(out) == 3
    # step 2's lag_1 must be step 1's p50 (70), not the last actual (100)
    assert seen_lags[0] == 100.0
    assert seen_lags[1] == 70.0
    assert [r["target_date"] for r in out] == [
        (start + timedelta(days=39 + k)).isoformat() for k in (1, 2, 3)
    ]


def test_recursive_forecast_short_history_skipped():
    dense = {"total": {date(2026, 7, 1): 1.0}}
    assert recursive_forecast(dense, 3, lambda rows: [(0, 0, 0)] * len(rows)) == []


def test_forecast_alignment_reports_forecasted_and_short_history_series():
    start = date(2026, 6, 1)
    dense = {
        "ready": {start + timedelta(days=i): 100.0 for i in range(40)},
        "short": {start + timedelta(days=i): 10.0 for i in range(10)},
    }
    forecasts = recursive_forecast(
        dense, horizon=3, predict_fn=lambda rows: [(50, 70, 90)] * len(rows)
    )
    alignment = validate_forecast_alignment(dense, 3, forecasts)
    assert alignment == [
        {
            "series": "ready",
            "source_days": 40,
            "expected_forecast_rows": 3,
            "forecast_rows": 3,
            "status": "forecasted",
        },
        {
            "series": "short",
            "source_days": 10,
            "expected_forecast_rows": 0,
            "forecast_rows": 0,
            "status": "insufficient-history",
        },
    ]


def test_forecast_alignment_fails_when_horizon_row_is_missing():
    start = date(2026, 6, 1)
    dense = {"total": {start + timedelta(days=i): 100.0 for i in range(40)}}
    forecasts = recursive_forecast(
        dense, horizon=3, predict_fn=lambda rows: [(50, 70, 90)] * len(rows)
    )
    with pytest.raises(RuntimeError, match="Forecast alignment failed"):
        validate_forecast_alignment(dense, 3, forecasts[:-1])


def test_store_forecasts_missing_storage_has_migration_guidance(monkeypatch):
    monkeypatch.setattr(
        "dbx_platform.forecast_infer.run_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(Exception("TABLE_NOT_FOUND")),
    )
    with pytest.raises(RuntimeError, match="schema_migrations"):
        store_forecasts(
            object(),
            "warehouse",
            "main",
            "dbx_platform",
            [{"run_date": "2026-07-18", "target_date": "2026-07-19"}],
        )
