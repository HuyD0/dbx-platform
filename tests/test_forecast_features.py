from datetime import date

from dbx_platform.forecast_features import (
    FEATURE_COLUMNS,
    TOTAL_SERIES,
    build_features,
    create_features_table_sql,
    daily_series,
    features_for_date,
    merge_features_sql,
)


def _rows(bucket="databricks", start=1, n=40, cost=100.0):
    return [
        {"usage_date": f"2026-06-{d:02d}" if d <= 30 else f"2026-07-{d - 30:02d}",
         "service_bucket": bucket, "cost": cost}
        for d in range(start, start + n)
    ]


# --- daily_series ---------------------------------------------------------------

def test_daily_series_adds_total():
    series = daily_series(
        [{"usage_date": "2026-07-01", "service_bucket": "databricks", "cost": 10},
         {"usage_date": "2026-07-01", "service_bucket": "storage", "cost": 5}]
    )
    assert series[TOTAL_SERIES][date(2026, 7, 1)] == 15.0


def test_daily_series_fills_gaps_with_zero():
    series = daily_series(
        [{"usage_date": "2026-07-01", "service_bucket": "s", "cost": 1},
         {"usage_date": "2026-07-04", "service_bucket": "s", "cost": 1}]
    )
    assert series["s"][date(2026, 7, 2)] == 0.0
    assert series["s"][date(2026, 7, 3)] == 0.0


def test_daily_series_skips_bad_dates():
    assert daily_series([{"usage_date": "not-a-date", "service_bucket": "s",
                          "cost": 1}]) == {}


# --- features_for_date (leakage safety) -----------------------------------------

def test_features_need_full_history():
    daily = {date(2026, 7, 1): 1.0}
    assert features_for_date(daily, date(2026, 7, 2)) is None


def test_features_use_only_past_days():
    # 28 days of history at 100, target day itself at 999 — no feature may
    # see the target day's value.
    daily = {date(2026, 6, 1): 100.0}
    for i in range(1, 40):
        daily[date(2026, 6, 1).fromordinal(date(2026, 6, 1).toordinal() + i)] = 100.0
    target = max(daily)
    daily[target] = 999.0
    feats = features_for_date(daily, target)
    assert feats is not None
    assert all(v != 999.0 for k, v in feats.items() if k.startswith(("lag_", "roll_")))


def test_lag_values_correct():
    base = date(2026, 6, 1)
    daily = {base.fromordinal(base.toordinal() + i): float(i) for i in range(40)}
    target = base.fromordinal(base.toordinal() + 39)
    feats = features_for_date(daily, target)
    assert feats["lag_1"] == 38.0
    assert feats["lag_7"] == 32.0
    assert feats["lag_28"] == 11.0


def test_calendar_features():
    daily = {date(2026, 6, 1).fromordinal(date(2026, 6, 1).toordinal() + i): 1.0
             for i in range(31)}
    feats = features_for_date(daily, date(2026, 7, 1))
    assert feats["is_month_start"] == 1.0
    assert feats["month"] == 7.0
    feats_end = features_for_date(daily, date(2026, 6, 30))
    assert feats_end["is_month_end"] == 1.0


# --- build_features -------------------------------------------------------------

def test_build_features_emits_all_columns():
    rows = build_features(_rows(n=40))
    assert rows, "40 days of history must produce feature rows"
    sample = rows[0]
    for col in FEATURE_COLUMNS:
        assert col in sample, col
    assert {r["series"] for r in rows} == {"databricks", TOTAL_SERIES}


def test_build_features_short_history_is_empty():
    assert build_features(_rows(n=10)) == []


# --- SQL builders ---------------------------------------------------------------

def test_features_ddl_covers_all_columns():
    ddl = create_features_table_sql("main", "dbx_platform")
    for col in FEATURE_COLUMNS:
        assert f"{col} DOUBLE" in ddl


def test_features_merge_keys():
    sql = merge_features_sql("main", "dbx_platform")
    assert "t.series = s.series AND t.feature_date = s.feature_date" in sql
    assert ":rows" in sql
