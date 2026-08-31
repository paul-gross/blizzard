"""``compute_trend`` (unit tier, blizzard#394 Phase 4) — the pure fold over a window's
own facts into fixed-length periods, the outflow/withdrawn roll-ups (D2), and the D5
introduced-age cut. No store, no clock — a plain list of ``TrendFact`` in, a ``Trend``
out."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.findings import EXIT_KINDS
from blizzard.hub.domain.garden_trend import TrendFact, compute_trend

pytestmark = pytest.mark.unit

_SINCE = datetime(2026, 1, 1, tzinfo=UTC)
_UNTIL = datetime(2026, 1, 15, tzinfo=UTC)  # two 7-day periods


def _fact(kind: str, *, day: int, introduced_at: datetime | None = None) -> TrendFact:
    return TrendFact(kind=kind, recorded_at=datetime(2026, 1, day, tzinfo=UTC), introduced_at=introduced_at)


def test_periods_span_the_window_in_fixed_width_slices() -> None:
    trend = compute_trend(
        [], routine_name="nightly", since=_SINCE, until=_UNTIL, period_days=7, introduced_boundary=_SINCE
    )

    assert [(p.period_start, p.period_end) for p in trend.periods] == [
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 8, tzinfo=UTC)),
        (datetime(2026, 1, 8, tzinfo=UTC), datetime(2026, 1, 15, tzinfo=UTC)),
    ]


def test_a_short_final_period_is_clipped_to_until() -> None:
    trend = compute_trend(
        [],
        routine_name="nightly",
        since=_SINCE,
        until=datetime(2026, 1, 10, tzinfo=UTC),
        period_days=7,
        introduced_boundary=_SINCE,
    )

    assert trend.periods[-1].period_end == datetime(2026, 1, 10, tzinfo=UTC)


def test_created_counts_add_facts_per_period() -> None:
    facts = [_fact("add", day=2), _fact("add", day=3), _fact("add", day=9)]

    trend = compute_trend(
        facts, routine_name="nightly", since=_SINCE, until=_UNTIL, period_days=7, introduced_boundary=_SINCE
    )

    assert trend.periods[0].created == 2
    assert trend.periods[1].created == 1


def test_every_exit_kind_is_counted_and_zero_kinds_are_still_reported() -> None:
    facts = [_fact("resolved", day=2), _fact("resolved", day=3), _fact("wont-fix", day=4)]

    trend = compute_trend(
        facts, routine_name="nightly", since=_SINCE, until=_UNTIL, period_days=7, introduced_boundary=_SINCE
    )

    period = trend.periods[0]
    assert period.exits == {
        "gone-confirmed": 0,
        "not-a-finding": 0,
        "resolved": 2,
        "superseded": 0,
        "wont-fix": 1,
    }
    assert set(period.exits) == EXIT_KINDS


def test_outflow_is_resolved_and_gone_confirmed_only() -> None:
    facts = [_fact("resolved", day=2), _fact("gone-confirmed", day=2), _fact("wont-fix", day=2)]

    trend = compute_trend(
        facts, routine_name="nightly", since=_SINCE, until=_UNTIL, period_days=7, introduced_boundary=_SINCE
    )

    assert trend.periods[0].outflow == 2


def test_withdrawn_excludes_outflow() -> None:
    facts = [
        _fact("resolved", day=2),
        _fact("wont-fix", day=2),
        _fact("not-a-finding", day=2),
        _fact("superseded", day=2),
    ]

    trend = compute_trend(
        facts, routine_name="nightly", since=_SINCE, until=_UNTIL, period_days=7, introduced_boundary=_SINCE
    )

    assert trend.periods[0].outflow == 1
    assert trend.periods[0].withdrawn == 3


def test_age_cut_splits_created_findings_by_introduced_at_against_the_boundary() -> None:
    boundary = datetime(2026, 1, 5, tzinfo=UTC)
    facts = [
        _fact("add", day=2, introduced_at=datetime(2026, 1, 6, tzinfo=UTC)),  # recent — after boundary
        _fact("add", day=3, introduced_at=datetime(2026, 1, 5, tzinfo=UTC)),  # recent — at boundary
        _fact("add", day=4, introduced_at=datetime(2026, 1, 4, tzinfo=UTC)),  # older
        _fact("add", day=5, introduced_at=None),  # unattributed
    ]

    trend = compute_trend(
        facts, routine_name="nightly", since=_SINCE, until=_UNTIL, period_days=7, introduced_boundary=boundary
    )

    assert trend.age.boundary == boundary
    assert trend.age.recent == 2
    assert trend.age.older == 1
    assert trend.age.unattributed == 1


def test_age_cut_ignores_exit_facts_counting_only_created_findings() -> None:
    facts = [_fact("add", day=2, introduced_at=None), _fact("resolved", day=2, introduced_at=None)]

    trend = compute_trend(
        facts, routine_name="nightly", since=_SINCE, until=_UNTIL, period_days=7, introduced_boundary=_SINCE
    )

    assert trend.age.unattributed == 1  # the resolved fact is not double-counted
