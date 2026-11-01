"""The levers — each a pure answer over its settings, its readings, and an instant.

Each case pins one edge: what a lever admits and the reading it judges, the instant it expects to admit again, and the
readings it refuses to guess at (a missing window, a turned-over window, a stale sample)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from blizzard.runner.subscriptions.subscription_sampler import ExternalSubscriptionUsageWindow
from blizzard.runner.throttle.levers import LeverName, budget_answer, pace_answer, schedule_answer, utilization_answer
from blizzard.runner.throttle.settings import (
    BudgetSettings,
    PaceSettings,
    ScheduleSettings,
    Span,
    TaperStep,
    UtilizationSettings,
)
from blizzard.runner.usage.repository import ExternalUsageSampleSummary, UsageTotals

pytestmark = pytest.mark.unit

_SLUG = "anthropic"
_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
_DAY = 86400
_WEEK = 7 * _DAY
_INTERVAL = 300


def _window(label: str, pct: float, resets_in: timedelta, *, seconds: int) -> ExternalSubscriptionUsageWindow:
    return ExternalSubscriptionUsageWindow(
        window=label, utilization_pct=pct, resets_at=_NOW + resets_in, window_seconds=seconds
    )


def _week(pct: float, *, elapsed_days: float = 5.0) -> ExternalSubscriptionUsageWindow:
    return _window("7d", pct, timedelta(days=7 - elapsed_days), seconds=_WEEK)


def _sample(
    *windows: ExternalSubscriptionUsageWindow, unlimited: tuple[str, ...] = (), age: timedelta = timedelta(minutes=1)
) -> ExternalUsageSampleSummary:
    return ExternalUsageSampleSummary(slug=_SLUG, sampled_at=_NOW - age, windows=windows, unlimited=unlimited)


def _pace(settings: PaceSettings, sample: ExternalUsageSampleSummary | None):
    return pace_answer(settings, sample=sample, slug=_SLUG, sample_interval_seconds=_INTERVAL, now=_NOW)


def _line(settings: UtilizationSettings, sample: ExternalUsageSampleSummary | None):
    return utilization_answer(settings, sample=sample, slug=_SLUG, sample_interval_seconds=_INTERVAL, now=_NOW)


# --- pace ------------------------------------------------------------------------------------------------------------


def test_pacing_five_days_into_seven_at_86_declines_under_lead_5_until_the_week_catches_up() -> None:
    answer = _pace(PaceSettings(window="7d", lead_pct=5.0), _sample(_week(86.0)))

    assert not answer.admits
    assert answer.lever is LeverName.PACE
    assert answer.reading == "7d: 86% spent, 71% elapsed"
    assert answer.in_force == "lead 5"
    started = _NOW + timedelta(days=2) - timedelta(seconds=_WEEK)
    assert answer.until == started + timedelta(seconds=_WEEK * (86.0 - 5.0) / 100)
    assert answer.until is not None and answer.until > _NOW


def test_pace_within_the_lead_admits_and_names_no_until() -> None:
    answer = _pace(PaceSettings(window="7d", lead_pct=5.0), _sample(_week(75.0)))

    assert answer.admits
    assert answer.until is None


@pytest.mark.parametrize("lead", [5.0, 0.0, -5.0])
def test_pace_catches_up_to_each_signed_lead(lead: float) -> None:
    window = _week(40, elapsed_days=7 * 0.30)
    answer = _pace(PaceSettings(window="7d", lead_pct=lead), _sample(window))
    assert not answer.admits
    assert answer.until == window.resets_at - timedelta(seconds=_WEEK * (100 - (40 - lead)) / 100)


def test_negative_lead_holds_opening_and_admits_at_boundary() -> None:
    settings = PaceSettings(window="7d", lead_pct=-5)
    opening = _pace(settings, _sample(_week(0, elapsed_days=7 * 0.01)))
    assert not opening.admits
    assert opening.setting == "7d, lag 5 points"
    assert opening.in_force == "lag 5"
    assert _pace(settings, _sample(_week(40, elapsed_days=7 * 0.46))).admits
    assert not _pace(settings, _sample(_week(40, elapsed_days=7 * 0.44))).admits


def test_negative_taper_and_reset_cap() -> None:
    settings = PaceSettings(window="7d", lead_pct=5, taper=(TaperStep(90, -5),))
    window = _week(97, elapsed_days=7 * 0.98)
    answer = _pace(settings, _sample(window))
    assert not answer.admits
    assert answer.until == window.resets_at
    assert answer.in_force == "lag 5 above 90%"
    assert answer.setting == "7d, lead 5 points, lag 5 above 90%"


def test_pace_without_a_named_window_judges_the_longest_the_sample_reports() -> None:
    short = _window("5h", 99.0, timedelta(hours=1), seconds=5 * 3600)

    answer = _pace(PaceSettings(), _sample(short, _week(40.0)))

    assert answer.admits
    assert answer.reading.startswith("7d:")


_TAPER = PaceSettings(window="7d", lead_pct=5.0, taper=(TaperStep(60.0, 3.0), TaperStep(90.0, 1.0)))


@pytest.mark.parametrize(
    ("spent", "in_force"),
    [
        (59.0, "lead 5"),
        (60.0, "lead 3 above 60%"),
        (75.0, "lead 3 above 60%"),
        (90.0, "lead 1 above 90%"),
        (97.0, "lead 1 above 90%"),
    ],
)
def test_the_taper_names_the_step_in_force_below_at_and_between_its_steps(spent: float, in_force: str) -> None:
    answer = _pace(_TAPER, _sample(_week(spent, elapsed_days=7 - 0.0001)))

    assert answer.in_force == in_force


def test_a_narrower_lead_declines_what_the_base_lead_admits() -> None:
    # Elapsed 88%, spent 92%: inside a lead of 5, outside the lead of 1 the taper sets above 90%.
    sample = _sample(_week(92.0, elapsed_days=7 * 0.88))

    assert _pace(PaceSettings(window="7d", lead_pct=5.0), sample).admits
    declined = _pace(_TAPER, sample)
    assert not declined.admits
    assert declined.reading == "7d: 92% spent, 88% elapsed"
    assert declined.in_force == "lead 1 above 90%"


def test_a_named_window_the_sample_lacks_says_no_naming_it_and_gives_no_until() -> None:
    answer = _pace(PaceSettings(window="7d"), _sample(_window("5h", 10.0, timedelta(hours=1), seconds=5 * 3600)))

    assert not answer.admits
    assert answer.reading == "window 7d not reported"
    assert answer.until is None


def test_a_paced_window_the_plan_imposes_no_limit_on_admits() -> None:
    answer = _pace(PaceSettings(window="5h"), _sample(_week(99.0), unlimited=("5h",)))

    assert answer.admits
    assert answer.reading == "5h: no limit"
    assert answer.until is None


def test_a_window_whose_reset_has_passed_is_unread_not_a_zero() -> None:
    gone = _window("7d", 99.0, -timedelta(minutes=1), seconds=_WEEK)

    answer = _pace(PaceSettings(window="7d"), _sample(gone))

    assert not answer.admits
    assert answer.reading == "window 7d has reset since its sample"
    assert answer.until is None


def test_a_stale_sample_says_no_naming_the_slug_and_its_time() -> None:
    stale = _sample(_week(10.0), age=timedelta(seconds=3 * _INTERVAL))

    answer = _pace(PaceSettings(window="7d"), stale)

    assert not answer.admits
    assert answer.reading == "no fresh sample of anthropic since 11:45Z"
    assert _pace(PaceSettings(window="7d"), _sample(_week(10.0), age=timedelta(seconds=3 * _INTERVAL - 1))).admits


def test_no_sample_at_all_says_no() -> None:
    answer = _pace(PaceSettings(window="7d"), None)

    assert not answer.admits
    assert answer.reading == "no sample of anthropic"


# --- the utilization line --------------------------------------------------------------------------------------------


def test_utilization_admits_while_every_window_stands_under_the_line() -> None:
    answer = _line(
        UtilizationSettings(below_pct=95.0),
        _sample(_window("5h", 40.0, timedelta(hours=1), seconds=5 * 3600), _week(80.0)),
    )

    assert answer.admits
    assert answer.reading == "5h: 40%, 7d: 80%"


def test_utilization_until_is_the_soonest_reset_among_the_windows_over_the_line() -> None:
    five = _window("5h", 96.0, timedelta(hours=2), seconds=5 * 3600)
    day = _window("1d", 97.0, timedelta(hours=1), seconds=_DAY)
    week = _week(10.0)

    answer = _line(UtilizationSettings(below_pct=95.0), _sample(five, day, week))

    assert not answer.admits
    assert answer.reading == "5h: 96%, 1d: 97%"
    assert answer.in_force == "line 95%"
    assert answer.until == day.resets_at


def test_utilization_judges_only_the_windows_it_names() -> None:
    five = _window("5h", 99.0, timedelta(hours=2), seconds=5 * 3600)

    answer = _line(UtilizationSettings(below_pct=95.0, windows=("7d",)), _sample(five, _week(10.0)))

    assert answer.admits
    assert answer.reading == "7d: 10%"


def test_a_named_utilization_window_the_sample_merely_lacks_says_no() -> None:
    answer = _line(UtilizationSettings(below_pct=95.0, windows=("7d", "1d")), _sample(_week(10.0)))

    assert not answer.admits
    assert answer.reading == "window 1d not reported"


def test_a_named_window_the_plan_imposes_no_limit_on_admits_however_spent_the_rest_is() -> None:
    answer = _line(UtilizationSettings(below_pct=75.0, windows=("5h",)), _sample(_week(99.0), unlimited=("5h",)))

    assert answer.admits
    assert answer.reading == "5h: no limit"
    assert answer.until is None


def test_a_no_limit_window_reads_in_the_order_the_line_names_it() -> None:
    answer = _line(UtilizationSettings(below_pct=95.0, windows=("5h", "7d")), _sample(_week(10.0), unlimited=("5h",)))

    assert answer.admits
    assert answer.reading == "5h: no limit, 7d: 10%"


def test_a_no_limit_window_does_not_lift_a_named_window_over_the_line() -> None:
    week = _week(96.0)

    answer = _line(UtilizationSettings(below_pct=95.0, windows=("5h", "7d")), _sample(week, unlimited=("5h",)))

    assert not answer.admits
    assert answer.reading == "7d: 96%"
    assert answer.until == week.resets_at


def test_a_stale_sample_holds_a_line_on_a_no_limit_window() -> None:
    stale = _sample(_week(1.0), unlimited=("5h",), age=timedelta(hours=1))

    answer = _line(UtilizationSettings(below_pct=75.0, windows=("5h",)), stale)

    assert not answer.admits
    assert answer.reading.startswith("no fresh sample of anthropic")


def test_an_unnamed_utilization_line_treats_a_turned_over_window_as_unread() -> None:
    gone = _window("5h", 1.0, -timedelta(minutes=1), seconds=5 * 3600)

    answer = _line(UtilizationSettings(below_pct=95.0), _sample(gone, _week(10.0)))

    assert not answer.admits
    assert answer.reading == "window 5h has reset since its sample"
    assert answer.until is None


def test_a_stale_sample_holds_the_utilization_line() -> None:
    answer = _line(UtilizationSettings(below_pct=95.0), _sample(_week(1.0), age=timedelta(hours=1)))

    assert not answer.admits
    assert answer.reading.startswith("no fresh sample of anthropic")


# --- the schedule ----------------------------------------------------------------------------------------------------

_PACIFIC = ZoneInfo("America/Los_Angeles")
_WEEKDAYS = (0, 1, 2, 3, 4)
_NIGHT = Span(19 * 60, 8 * 60)


def _at(zone: ZoneInfo, *args: int) -> datetime:
    return datetime(*args, tzinfo=zone).astimezone(UTC)


def test_a_span_across_midnight_belongs_to_the_day_it_starts_on() -> None:
    settings = ScheduleSettings(days=_WEEKDAYS, spans=(_NIGHT,))

    # Friday 2026-10-23 evening runs into Saturday morning; Saturday evening does not start a span.
    assert schedule_answer(settings, zone=_PACIFIC, now=_at(_PACIFIC, 2026, 10, 24, 7, 0)).admits
    assert not schedule_answer(settings, zone=_PACIFIC, now=_at(_PACIFIC, 2026, 10, 24, 20, 0)).admits
    assert not schedule_answer(settings, zone=_PACIFIC, now=_at(_PACIFIC, 2026, 10, 25, 7, 0)).admits


def test_outside_the_spans_the_schedule_declines_until_the_next_span_starts() -> None:
    settings = ScheduleSettings(days=_WEEKDAYS, spans=(_NIGHT,))
    friday_noon = _at(_PACIFIC, 2026, 10, 23, 12, 0)

    answer = schedule_answer(settings, zone=_PACIFIC, now=friday_noon)

    assert not answer.admits
    assert answer.reading == "outside"
    assert answer.setting == "mon\N{EN DASH}fri 19:00\N{EN DASH}08:00 PDT"
    assert answer.until == _at(_PACIFIC, 2026, 10, 23, 19, 0)


def test_a_schedule_with_days_alone_admits_those_whole_days() -> None:
    settings = ScheduleSettings(days=(5, 6))

    assert schedule_answer(settings, zone=_PACIFIC, now=_at(_PACIFIC, 2026, 10, 24, 3, 0)).admits
    declined = schedule_answer(settings, zone=_PACIFIC, now=_at(_PACIFIC, 2026, 10, 23, 12, 0))
    assert not declined.admits
    assert declined.until == _at(_PACIFIC, 2026, 10, 24, 0, 0)


def test_a_span_starting_in_the_spring_forward_gap_begins_at_the_first_minute_that_exists() -> None:
    # 2026-03-08 02:00 to 03:00 does not exist in Los Angeles; a 02:30 start begins at 03:00 PDT.
    settings = ScheduleSettings(days=(6,), spans=(Span(2 * 60 + 30, 5 * 60),))
    before = _at(_PACIFIC, 2026, 3, 8, 1, 0)

    answer = schedule_answer(settings, zone=_PACIFIC, now=before)

    assert not answer.admits
    assert answer.until == datetime(2026, 3, 8, 10, 0, tzinfo=UTC)
    assert schedule_answer(settings, zone=_PACIFIC, now=datetime(2026, 3, 8, 10, 0, tzinfo=UTC)).admits


def test_a_span_across_the_fall_back_change_runs_by_the_clock_on_the_wall() -> None:
    # 2026-11-01 01:00 to 02:00 occurs twice; the span 00:30-06:00 holds the extra hour, ending at 06:00 PST.
    settings = ScheduleSettings(days=(6,), spans=(Span(30, 6 * 60),))

    assert schedule_answer(settings, zone=_PACIFIC, now=datetime(2026, 11, 1, 9, 30, tzinfo=UTC)).admits
    assert schedule_answer(settings, zone=_PACIFIC, now=datetime(2026, 11, 1, 13, 59, tzinfo=UTC)).admits
    assert not schedule_answer(settings, zone=_PACIFIC, now=datetime(2026, 11, 1, 14, 0, tzinfo=UTC)).admits


def test_a_time_that_occurs_twice_is_taken_at_its_first_occurrence() -> None:
    settings = ScheduleSettings(days=(6,), spans=(Span(60 + 30, 4 * 60),))
    first = datetime(2026, 11, 1, 8, 30, tzinfo=UTC)  # 01:30 PDT

    assert not schedule_answer(settings, zone=_PACIFIC, now=first - timedelta(minutes=1)).admits
    assert schedule_answer(settings, zone=_PACIFIC, now=first).admits


def _spend(billed: float, *, estimated: float | None = None, partial: bool = False) -> UsageTotals:
    return UsageTotals(0, 0, 0, 0, billed, partial, estimated)


def test_budget_admits_under_the_line_and_declines_at_and_over_it() -> None:
    settings = BudgetSettings(usd=40.0)

    under = budget_answer(settings, spend=_spend(39.99))
    at = budget_answer(settings, spend=_spend(40.0))
    over = budget_answer(settings, spend=_spend(41.2))

    assert [a.admits for a in (under, at, over)] == [True, False, False]
    assert over.lever is LeverName.BUDGET
    assert over.reading == "$41.20 of $40.00 over the trailing 24h"
    assert over.setting == "$40.00 over 24h"


def test_budget_gives_no_until_because_it_cannot_read_when_spend_ages_out() -> None:
    assert budget_answer(BudgetSettings(usd=10.0), spend=_spend(12.0)).until is None


def test_budget_counts_billed_plus_estimated_as_the_one_figure() -> None:
    answer = budget_answer(BudgetSettings(usd=10.0, window_hours=6.0), spend=_spend(4.0, estimated=7.0))

    assert not answer.admits
    assert answer.reading == "~$11.00 of $10.00 over the trailing 6h"


def test_budget_marks_a_lower_bound_with_the_partial_plus() -> None:
    answer = budget_answer(BudgetSettings(usd=40.0), spend=_spend(41.2, partial=True))

    assert answer.reading == "$41.20+ of $40.00 over the trailing 24h"
