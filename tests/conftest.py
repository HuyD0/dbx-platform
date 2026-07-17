"""Shared test fixtures. All tests are pure-logic — no network, no SDK mocks."""

import pytest

MS_PER_DAY = 86_400_000
MS_PER_HOUR = 3_600_000

NOW_MS = 1_752_710_400_000  # fixed reference instant


@pytest.fixture
def now_ms() -> int:
    return NOW_MS


def days_ago(days: float) -> int:
    return int(NOW_MS - days * MS_PER_DAY)


def hours_ago(hours: float) -> int:
    return int(NOW_MS - hours * MS_PER_HOUR)
