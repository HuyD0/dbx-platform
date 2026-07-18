from datetime import date, timedelta

import pytest

from dbx_platform.forecast_train import (
    backtest_folds,
    coerce_feature_rows,
    pinball,
    seasonal_naive,
    should_promote,
    smape,
    wape,
)

# --- metrics --------------------------------------------------------------------


def test_wape_perfect_is_zero():
    assert wape([10, 20], [10, 20]) == 0.0


def test_wape_known_value():
    # |10-8| + |20-24| = 6 over 30 actual
    assert wape([10, 20], [8, 24]) == pytest.approx(0.2)


def test_wape_zero_actuals():
    assert wape([0, 0], [0, 0]) == 0.0
    assert wape([0, 0], [1, 0]) == float("inf")


def test_smape_skips_zero_pairs():
    assert smape([0, 10], [0, 10]) == 0.0


def test_pinball_asymmetry():
    # For q=0.9 an under-prediction hurts 9x more than an over-prediction.
    under = pinball([10.0], [0.0], 0.9)
    over = pinball([0.0], [10.0], 0.9)
    assert under == pytest.approx(9.0)
    assert over == pytest.approx(1.0)


# --- backtest_folds -------------------------------------------------------------

def _dates(n, start=date(2026, 1, 1)):
    return [start + timedelta(days=i) for i in range(n)]


def test_folds_cover_tail_without_overlap():
    folds = backtest_folds(_dates(60), n_folds=3, horizon=10)
    assert len(folds) == 3
    covered = [d for _, test in folds for d in test]
    assert len(covered) == len(set(covered)) == 30
    assert max(covered) == date(2026, 1, 1) + timedelta(days=59)


def test_folds_train_end_precedes_test():
    for train_end, test in backtest_folds(_dates(60), 3, 10):
        assert train_end < min(test)


def test_folds_insufficient_history_raises():
    with pytest.raises(ValueError, match="need >="):
        backtest_folds(_dates(30), n_folds=3, horizon=10)


def test_folds_empty_raises():
    with pytest.raises(ValueError):
        backtest_folds([], 3, 10)


# --- seasonal_naive -------------------------------------------------------------

def test_seasonal_naive_uses_same_weekday_last_week():
    daily = {date(2026, 7, 1) + timedelta(days=i): float(i) for i in range(14)}
    target = date(2026, 7, 1) + timedelta(days=14)
    assert seasonal_naive(daily, [target]) == [7.0]


def test_seasonal_naive_walks_back_when_missing():
    daily = {date(2026, 7, 1): 42.0}
    target = date(2026, 7, 1) + timedelta(days=21)
    assert seasonal_naive(daily, [target]) == [42.0]


# --- should_promote (the champion/challenger gate) ------------------------------

def test_first_model_always_promotes():
    assert should_promote(None, 0.5)


def test_better_challenger_promotes():
    assert should_promote(0.20, 0.15, min_improvement=0.01)


def test_tie_keeps_incumbent():
    assert not should_promote(0.20, 0.20, min_improvement=0.01)


def test_marginal_improvement_below_margin_keeps_incumbent():
    # 0.199 is <1% better than 0.20 — not enough.
    assert not should_promote(0.20, 0.199, min_improvement=0.01)


def test_nan_or_inf_challenger_never_promotes():
    assert not should_promote(None, float("nan"))
    assert not should_promote(0.5, float("inf"))


# --- coerce_feature_rows --------------------------------------------------------

def test_coerce_turns_statement_strings_numeric():
    rows = coerce_feature_rows(
        [{"series": "total", "feature_date": "2026-07-01", "cost": "12.5",
          "lag_1": "10", "lag_7": None}]
    )
    assert rows[0]["cost"] == 12.5
    assert rows[0]["lag_1"] == 10.0
    assert rows[0]["lag_7"] == 0.0
