"""The schedule module (unit tier) pinned against the cron library at each edge the due rule relies on: a moment
exactly at the instant asked, both daylight-saving changes for a fixed-hour and an hourly expression, the
day-of-month or day-of-week match, the next several moments across both changes and a sparse expression, and the
refusals of a malformed, six-field, or never-firing expression or an
unknown zone."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.cadence.schedule import CRON_FIELD, ZONE_FIELD, Schedule
from blizzard.hub.domain.config.errors import ConfigFieldError

_AT = datetime(2026, 1, 1, tzinfo=UTC)
_CHICAGO = "America/Chicago"


def _schedule(cron: str, zone: str = _CHICAGO) -> Schedule:
    return Schedule.parse(cron, zone, at=_AT)


def _utc(month: int, day: int, hour: int, minute: int = 0, second: int = 0, micro: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, second, micro, tzinfo=UTC)


def test_a_moment_exactly_at_the_instant_asked_is_the_latest_at_or_before_it() -> None:
    schedule = _schedule("0 20 * * *")
    at = _utc(6, 1, 1)  # 20:00 CDT on May 31
    assert schedule.latest_at_or_before(at) == at


def test_a_moment_exactly_at_the_instant_asked_is_not_the_first_after_it() -> None:
    schedule = _schedule("0 20 * * *")
    assert schedule.first_after(_utc(6, 1, 1)) == _utc(6, 2, 1)


def test_the_latest_moment_ignores_the_sub_second_part_of_the_instant() -> None:
    schedule = _schedule("0 20 * * *")
    assert schedule.latest_at_or_before(_utc(6, 1, 1, 0, 0, 500)) == _utc(6, 1, 1)
    assert schedule.latest_at_or_before(_utc(6, 1, 0, 59, 59, 999_999)) == _utc(5, 31, 1)


def test_a_moment_the_spring_forward_skips_fires_at_the_first_instant_after_the_jump() -> None:
    schedule = _schedule("30 2 * * *")
    # 2026-03-08 02:00 CST jumps to 03:00 CDT (08:00 UTC).
    assert schedule.first_after(_utc(3, 8, 6)) == _utc(3, 8, 8)
    assert schedule.latest_at_or_before(_utc(3, 8, 8)) == _utc(3, 8, 8)
    assert schedule.latest_at_or_before(_utc(3, 8, 7, 59)) == _utc(3, 7, 8, 30)


def test_a_fixed_hour_moment_the_fall_back_repeats_fires_once_at_its_first_reading() -> None:
    schedule = _schedule("30 1 * * *")
    # 2026-11-01 01:30 CDT is 06:30 UTC; the repeated 01:30 CST is 07:30 UTC and never fires.
    assert schedule.first_after(_utc(11, 1, 5)) == _utc(11, 1, 6, 30)
    assert schedule.first_after(_utc(11, 1, 6, 30)) == _utc(11, 2, 7, 30)
    assert schedule.latest_at_or_before(_utc(11, 1, 7)) == _utc(11, 1, 6, 30)
    assert schedule.latest_at_or_before(_utc(11, 1, 7, 45)) == _utc(11, 1, 6, 30)


def test_the_spring_forward_leaves_an_hourly_expression_no_moment_in_the_hour_that_never_happened() -> None:
    schedule = _schedule("0 * * * *")
    # 01:00 CST (07:00 UTC), then 03:00 CDT (08:00 UTC): no 02:00.
    assert schedule.first_after(_utc(3, 8, 7)) == _utc(3, 8, 8)


def test_an_hourly_expression_fires_at_both_readings_of_the_hour_the_fall_back_repeats() -> None:
    schedule = _schedule("0 * * * *")
    assert schedule.first_after(_utc(11, 1, 5, 30)) == _utc(11, 1, 6)
    assert schedule.first_after(_utc(11, 1, 6)) == _utc(11, 1, 7)
    assert schedule.latest_at_or_before(_utc(11, 1, 6, 59)) == _utc(11, 1, 6)
    assert schedule.latest_at_or_before(_utc(11, 1, 7, 30)) == _utc(11, 1, 7)


def test_a_restricted_day_of_month_and_day_of_week_match_a_day_satisfying_either() -> None:
    schedule = _schedule("0 20 1 * mon", zone="UTC")
    # 2026-06-01 is a Monday; June 8 is the next Monday; July 1 is a Wednesday.
    moments: list[datetime] = []
    at = _utc(6, 1, 21)
    for _ in range(5):
        moment = schedule.first_after(at)
        assert moment is not None
        moments.append(moment)
        at = moment
    assert [m.day for m in moments] == [8, 15, 22, 29, 1]
    assert moments[-1].month == 7


@pytest.mark.parametrize(
    ("cron", "zone", "field"),
    [
        (None, _CHICAGO, CRON_FIELD),
        ("", _CHICAGO, CRON_FIELD),
        ("0 0 30 2 *", _CHICAGO, CRON_FIELD),
        ("0 0 * * * *", _CHICAGO, CRON_FIELD),
        ("0 0 * *", _CHICAGO, CRON_FIELD),
        ("61 * * * *", _CHICAGO, CRON_FIELD),
        # The fifth Sunday of February always falls on the 29th, never the 1st or the 31st.
        ("0 0 */30 2 sun#5", _CHICAGO, CRON_FIELD),
        ("0 20 * * *", None, ZONE_FIELD),
        ("0 20 * * *", "Mars/Base", ZONE_FIELD),
        ("0 20 * * *", "../etc/passwd", ZONE_FIELD),
    ],
)
def test_a_schedule_is_refused_naming_its_field(cron: str | None, zone: str | None, field: str) -> None:
    with pytest.raises(ConfigFieldError) as caught:
        Schedule.parse(cron, zone, at=_AT)
    assert caught.value.field == field


def test_a_moment_exactly_at_the_instant_asked_is_the_first_at_or_after_it() -> None:
    schedule = _schedule("0 20 * * *")
    at = _utc(6, 1, 1)
    assert schedule.first_at_or_after(at) == at


def test_an_instant_between_two_moments_has_the_later_as_its_first_at_or_after() -> None:
    schedule = _schedule("0 20 * * *")
    assert schedule.first_at_or_after(_utc(6, 1, 1, micro=1)) == _utc(6, 2, 1)
    assert schedule.first_at_or_after(_utc(6, 1, 0, 59)) == _utc(6, 1, 1)


def test_the_next_moments_are_the_count_asked_earliest_first_the_first_strictly_after_the_instant() -> None:
    schedule = _schedule("0 20 * * *")
    at = _utc(6, 1, 1)  # exactly 20:00 CDT on May 31
    assert schedule.next_moments(at, 5) == tuple(_utc(6, day, 1) for day in range(2, 7))


def test_the_next_moments_across_the_spring_forward_fire_the_skipped_moment_after_the_jump() -> None:
    schedule = _schedule("30 2 * * *")
    assert schedule.next_moments(_utc(3, 6, 12), 5) == (
        _utc(3, 7, 8, 30),  # 02:30 CST
        _utc(3, 8, 8),  # the skipped 02:30 fires at 03:00 CDT
        _utc(3, 9, 7, 30),  # 02:30 CDT
        _utc(3, 10, 7, 30),
        _utc(3, 11, 7, 30),
    )


def test_the_next_moments_across_the_fall_back_fire_the_repeated_moment_once() -> None:
    schedule = _schedule("30 1 * * *")
    assert schedule.next_moments(_utc(10, 31, 12), 5) == (
        _utc(11, 1, 6, 30),  # 01:30 CDT; the repeated 01:30 CST never fires
        _utc(11, 2, 7, 30),
        _utc(11, 3, 7, 30),
        _utc(11, 4, 7, 30),
        _utc(11, 5, 7, 30),
    )


def test_a_sparse_yearly_expression_still_yields_five_moments() -> None:
    schedule = _schedule("0 12 29 2 *")  # noon on leap days only
    moments = schedule.next_moments(_AT, 5)
    assert [m.year for m in moments] == [2028, 2032, 2036, 2040, 2044]
    assert all((m.month, m.day) == (2, 29) for m in moments)
