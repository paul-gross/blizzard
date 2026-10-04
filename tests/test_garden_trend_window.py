"""``TrendWindow.of`` (unit tier, by value): the window a trend read can bucket, and
``compute_trend`` refusing one it could not."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.garden.findings.trend import InvalidTrendWindow, TrendWindow, compute_trend
from blizzard.hub.domain.garden.runs.window import InvalidWindowError

pytestmark = pytest.mark.unit

_SINCE = datetime(2026, 1, 1, tzinfo=UTC)
_UNTIL = datetime(2026, 1, 15, tzinfo=UTC)


def test_a_valid_window_carries_its_values() -> None:
    window = TrendWindow.of(since=_SINCE, until=_UNTIL, introduced_boundary=_SINCE, period_days=7)

    assert window == TrendWindow(since=_SINCE, until=_UNTIL, introduced_boundary=_SINCE, period_days=7)


@pytest.mark.parametrize("period_days", [0, -1])
def test_a_non_positive_period_is_refused(period_days: int) -> None:
    with pytest.raises(InvalidTrendWindow, match="period_days must be at least 1"):
        TrendWindow.of(since=_SINCE, until=_UNTIL, introduced_boundary=_SINCE, period_days=period_days)


@pytest.mark.parametrize("until", [_SINCE, _SINCE - timedelta(days=1)])
def test_a_non_positive_span_is_refused_by_the_shared_forward_span_rule(until: datetime) -> None:
    with pytest.raises(InvalidWindowError, match="until must be after since"):
        TrendWindow.of(since=_SINCE, until=until, introduced_boundary=_SINCE, period_days=1)


def test_the_period_cap_is_inclusive() -> None:
    until = _SINCE + timedelta(days=TrendWindow.MAX_PERIODS)

    assert TrendWindow.of(since=_SINCE, until=until, introduced_boundary=_SINCE, period_days=1).until == until


def test_a_window_past_the_period_cap_is_refused() -> None:
    with pytest.raises(InvalidTrendWindow, match="366"):
        TrendWindow.of(since=_SINCE, until=_SINCE + timedelta(days=367), introduced_boundary=_SINCE, period_days=1)


def test_compute_trend_refuses_a_window_it_could_not_bucket() -> None:
    with pytest.raises(InvalidTrendWindow):
        compute_trend([], routine_name="nightly", since=_SINCE, until=_UNTIL, period_days=0, introduced_boundary=_SINCE)
