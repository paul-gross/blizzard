"""The subscription floor, derived by value over the newest mark, sample and clear at an instant.

Each case pins one edge of when a hold stands and when it opens: on a turned-over window, on a sample since the reset,
on the clock where nothing can confirm, after the grace, or by a hand clear."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.subscriptions.subscription_sampler import ExternalSubscriptionUsageWindow
from blizzard.runner.throttle.floor import (
    FLOOR_NO_RESET_FALLBACK,
    FLOOR_UNCONFIRMED_GRACE,
    FloorAnswer,
    HoldKind,
    RunnerFloorClearRefused,
    floor_answer,
    refuse_runner_floor_clear,
)
from blizzard.runner.throttle.usage_limits import FloorClear, UsageLimitMark
from blizzard.runner.usage.repository import ExternalUsageSampleSummary

pytestmark = pytest.mark.unit

_SLUG = "anthropic"
_MARKED = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_RESET = _MARKED + timedelta(hours=2)
_FIVE_HOURS = 5 * 3600


def _mark(
    *,
    at: datetime = _MARKED,
    resets_at: datetime | None = _RESET,
    window: str | None = "five_hour",
    window_resets_at: datetime | None = _RESET,
) -> UsageLimitMark:
    return UsageLimitMark(
        marked_at=at,
        resets_at=resets_at,
        window=window,
        window_resets_at=window_resets_at,
        harness_id="claude-code",
        detail="limit reached",
        lease_id="lease_1",
        epoch=1,
        generation=1,
        invocation_kind="worker",
    )


def _window(pct: float, resets_at: datetime, *, label: str = "five_hour") -> ExternalSubscriptionUsageWindow:
    return ExternalSubscriptionUsageWindow(
        window=label, utilization_pct=pct, resets_at=resets_at, window_seconds=_FIVE_HOURS
    )


def _sample(at: datetime, *windows: ExternalSubscriptionUsageWindow) -> ExternalUsageSampleSummary:
    return ExternalUsageSampleSummary(slug=_SLUG, sampled_at=at, windows=windows)


def _floor(
    *,
    now: datetime,
    mark: UsageLimitMark | None = None,
    sample: ExternalUsageSampleSummary | None = None,
    clear: FloorClear | None = None,
    clear_sample: ExternalUsageSampleSummary | None = None,
    sampled: bool = True,
    slug: str | None = _SLUG,
) -> FloorAnswer:
    return floor_answer(
        slug=slug, sampled=sampled, mark=mark, sample=sample, clear=clear, clear_sample=clear_sample, now=now
    )


# --- a mark ----------------------------------------------------------------------------------------------------------


def test_nothing_on_record_admits() -> None:
    assert _floor(now=_MARKED) == FloorAnswer(admits=True, slug=_SLUG)


def test_a_mark_holds_until_its_reset_naming_its_harness_and_held_window() -> None:
    answer = _floor(now=_MARKED + timedelta(minutes=5), mark=_mark())

    assert not answer.admits
    assert answer.hold is not None
    assert (answer.hold.kind, answer.hold.harness_id, answer.hold.window) == (HoldKind.MARK, "claude-code", "five_hour")
    assert answer.hold.expected_reset == _RESET
    assert answer.hold.lease_id == "lease_1"


def test_a_mark_outranks_an_older_sample_under_the_line() -> None:
    sample = _sample(_MARKED - timedelta(minutes=3), _window(94.0, _RESET))

    assert not _floor(now=_MARKED + timedelta(minutes=1), mark=_mark(), sample=sample).admits


def test_early_turnover_of_the_held_window_opens_before_the_reset() -> None:
    turned_over = _sample(_MARKED + timedelta(minutes=30), _window(3.0, _RESET + timedelta(hours=5)))

    assert _floor(now=_MARKED + timedelta(minutes=31), mark=_mark(), sample=turned_over).admits


@pytest.mark.parametrize("now", [_RESET - timedelta(minutes=1), _RESET + timedelta(minutes=10)])
def test_a_99_percent_sample_in_the_limits_period_opens_nothing_before_or_after_the_reset(now: datetime) -> None:
    lagging = _sample(_MARKED + timedelta(minutes=20), _window(99.0, _RESET))

    assert not _floor(now=now, mark=_mark(), sample=lagging).admits


def test_a_mark_with_no_held_window_is_never_opened_early_by_a_sample() -> None:
    mark = _mark(window=None, window_resets_at=None)
    turned_over = _sample(_MARKED + timedelta(minutes=30), _window(3.0, _RESET + timedelta(hours=5)))

    assert not _floor(now=_MARKED + timedelta(minutes=31), mark=mark, sample=turned_over).admits


def test_past_the_reset_with_no_sample_since_the_mark_awaits_one() -> None:
    before = _sample(_MARKED - timedelta(minutes=3), _window(94.0, _RESET))
    answer = _floor(now=_RESET + timedelta(minutes=5), mark=_mark(), sample=before)

    assert not answer.admits
    assert answer.hold is not None and answer.hold.awaits_sample


def test_past_the_reset_an_unsampled_subscription_opens_on_the_clock() -> None:
    assert not _floor(now=_RESET - timedelta(seconds=1), mark=_mark(), sampled=False).admits
    assert _floor(now=_RESET, mark=_mark(), sampled=False).admits


def test_an_unsampled_subscription_ignores_any_sample() -> None:
    exhausted = _sample(_MARKED, _window(100.0, _RESET + timedelta(hours=3)))

    assert _floor(now=_MARKED, sample=exhausted, sampled=False).admits


def test_a_runner_naming_no_subscription_holds_on_its_own_mark_until_the_reset() -> None:
    held = _floor(now=_RESET - timedelta(seconds=1), mark=_mark(), sampled=False, slug=None)

    assert not held.admits and held.slug is None
    assert _floor(now=_RESET, mark=_mark(), sampled=False, slug=None).admits


def test_a_sample_since_the_reset_in_a_new_period_under_the_line_opens() -> None:
    opened = _sample(_RESET + timedelta(minutes=2), _window(1.0, _RESET + timedelta(hours=5)))

    assert _floor(now=_RESET + timedelta(minutes=3), mark=_mark(), sample=opened).admits


def test_a_sample_still_reporting_the_passed_period_is_no_evidence() -> None:
    passed = _sample(_RESET + timedelta(minutes=2), _window(40.0, _RESET))

    assert not _floor(now=_RESET + timedelta(minutes=3), mark=_mark(), sample=passed).admits


def test_one_window_still_in_a_passed_period_withholds_the_evidence() -> None:
    mixed = _sample(
        _RESET + timedelta(minutes=2),
        _window(1.0, _RESET + timedelta(hours=5)),
        _window(20.0, _RESET - timedelta(hours=1), label="seven_day"),
    )
    mark = _mark(window=None, window_resets_at=None)

    assert not _floor(now=_RESET + timedelta(minutes=3), mark=mark, sample=mixed).admits


@pytest.mark.parametrize(
    "sample",
    [
        None,
        _sample(_MARKED - timedelta(minutes=3), _window(94.0, _RESET)),
        _sample(_RESET + timedelta(minutes=2), _window(40.0, _RESET)),
    ],
    ids=["no-sample", "only-before-the-reset", "passed-period"],
)
def test_the_grace_clears_a_hold_an_hour_past_its_reset_when_no_evidence_comes(
    sample: ExternalUsageSampleSummary | None,
) -> None:
    assert not _floor(now=_RESET + FLOOR_UNCONFIRMED_GRACE - timedelta(seconds=1), mark=_mark(), sample=sample).admits
    assert _floor(now=_RESET + FLOOR_UNCONFIRMED_GRACE, mark=_mark(), sample=sample).admits


def test_a_new_period_at_100_percent_holds_past_the_hour() -> None:
    next_reset = _RESET + timedelta(hours=5)
    shut = _sample(_RESET + timedelta(minutes=2), _window(100.0, next_reset))
    answer = _floor(now=_RESET + FLOOR_UNCONFIRMED_GRACE + timedelta(minutes=5), mark=_mark(), sample=shut)

    assert not answer.admits
    assert answer.hold is not None
    assert (answer.hold.kind, answer.hold.expected_reset) == (HoldKind.WINDOW, next_reset)


# --- a mark naming no reset ------------------------------------------------------------------------------------------


def test_a_mark_naming_no_reset_with_no_sample_falls_back_and_opens_on_the_clock() -> None:
    mark = _mark(resets_at=None, window=None, window_resets_at=None)
    held = _floor(now=_MARKED + FLOOR_NO_RESET_FALLBACK - timedelta(seconds=1), mark=mark)

    assert not held.admits
    assert held.hold is not None
    assert held.hold.expected_reset == _MARKED + FLOOR_NO_RESET_FALLBACK
    assert not held.hold.awaits_sample
    assert _floor(now=_MARKED + FLOOR_NO_RESET_FALLBACK, mark=mark).admits


def test_a_mark_naming_no_reset_takes_an_exhausted_windows_reset_from_a_later_sample() -> None:
    mark = _mark(resets_at=None, window=None, window_resets_at=None)
    shut = _sample(_MARKED + timedelta(minutes=1), _window(100.0, _RESET))
    answer = _floor(now=_MARKED + FLOOR_NO_RESET_FALLBACK + timedelta(minutes=1), mark=mark, sample=shut)

    assert not answer.admits
    assert answer.hold is not None
    assert (answer.hold.kind, answer.hold.expected_reset) == (HoldKind.MARK, _RESET)


def test_a_mark_naming_no_reset_whose_later_sample_shows_none_exhausted_falls_back() -> None:
    mark = _mark(resets_at=None, window=None, window_resets_at=None)
    open_sample = _sample(_MARKED + timedelta(minutes=1), _window(60.0, _RESET))

    assert not _floor(now=_MARKED + timedelta(minutes=29), mark=mark, sample=open_sample).admits
    assert _floor(now=_MARKED + FLOOR_NO_RESET_FALLBACK, mark=mark, sample=open_sample).admits


# --- an exhausted window ---------------------------------------------------------------------------------------------


def test_an_exhausted_window_holds_until_its_reset_with_no_mark() -> None:
    shut = _sample(_MARKED, _window(100.0, _RESET))
    answer = _floor(now=_MARKED + timedelta(minutes=1), sample=shut)

    assert not answer.admits
    assert answer.hold is not None
    assert (answer.hold.kind, answer.hold.window, answer.hold.harness_id) == (HoldKind.WINDOW, "five_hour", None)
    assert answer.hold.lease_id is None


def test_an_exhausted_window_past_its_reset_awaits_a_sample_within_the_grace() -> None:
    shut = _sample(_MARKED, _window(100.0, _RESET))

    assert not _floor(now=_RESET + timedelta(minutes=30), sample=shut).admits
    assert _floor(now=_RESET + FLOOR_UNCONFIRMED_GRACE, sample=shut).admits


def test_a_window_under_100_percent_holds_nothing() -> None:
    assert _floor(now=_MARKED, sample=_sample(_MARKED, _window(99.9, _RESET))).admits


def test_of_two_exhausted_windows_the_one_lifting_last_is_named() -> None:
    later = _RESET + timedelta(days=3)
    shut = _sample(_MARKED, _window(100.0, _RESET), _window(100.0, later, label="seven_day"))
    answer = _floor(now=_MARKED, sample=shut)

    assert answer.hold is not None
    assert (answer.hold.window, answer.hold.expected_reset) == ("seven_day", later)


# --- clearing by hand ------------------------------------------------------------------------------------------------


def _clear(at: datetime) -> FloorClear:
    return FloorClear(cleared_at=at, by="operator")


def test_a_clear_after_the_mark_opens_it_and_is_named() -> None:
    clear = _clear(_MARKED + timedelta(minutes=5))
    answer = _floor(now=_MARKED + timedelta(minutes=6), mark=_mark(), clear=clear)

    assert answer == FloorAnswer(admits=True, slug=_SLUG, clear=clear)


def test_a_mark_after_the_clear_holds_again() -> None:
    mark = _mark(at=_MARKED + timedelta(minutes=10))

    assert not _floor(now=_MARKED + timedelta(minutes=11), mark=mark, clear=_clear(_MARKED)).admits


def test_a_clear_sets_aside_a_window_the_sample_before_it_showed_exhausted() -> None:
    shut = _sample(_MARKED, _window(100.0, _RESET))

    assert _floor(now=_MARKED + timedelta(minutes=6), sample=shut, clear=_clear(_MARKED + timedelta(minutes=5))).admits


def test_a_later_sample_still_showing_the_cleared_window_shut_does_not_undo_the_clear() -> None:
    at_clear = _sample(_MARKED, _window(100.0, _RESET))
    still_shut = _sample(_MARKED + timedelta(minutes=20), _window(100.0, _RESET))
    clear = _clear(_MARKED + timedelta(minutes=5))

    answer = _floor(
        now=_MARKED + timedelta(minutes=21), mark=_mark(), sample=still_shut, clear=clear, clear_sample=at_clear
    )

    assert answer.admits


def test_a_window_not_exhausted_at_the_clear_holds_once_it_is_in_the_same_period() -> None:
    at_clear = _sample(_MARKED, _window(90.0, _RESET))
    shut_since = _sample(_MARKED + timedelta(minutes=20), _window(100.0, _RESET))
    clear = _clear(_MARKED + timedelta(minutes=5))

    answer = _floor(now=_MARKED + timedelta(minutes=21), sample=shut_since, clear=clear, clear_sample=at_clear)

    assert not answer.admits
    assert answer.hold is not None and answer.hold.kind is HoldKind.WINDOW


def test_a_window_with_no_sample_standing_at_the_clear_holds() -> None:
    shut_since = _sample(_MARKED + timedelta(minutes=20), _window(100.0, _RESET))

    assert not _floor(now=_MARKED + timedelta(minutes=21), sample=shut_since, clear=_clear(_MARKED)).admits


def test_a_window_exhausted_under_a_later_reset_holds_despite_the_clear() -> None:
    next_reset = _RESET + timedelta(hours=5)
    shut_again = _sample(_RESET + timedelta(minutes=10), _window(100.0, next_reset))
    clear = _clear(_MARKED + timedelta(minutes=5))

    answer = _floor(now=_RESET + timedelta(minutes=11), mark=_mark(), sample=shut_again, clear=clear)

    assert not answer.admits
    assert answer.hold is not None and answer.hold.expected_reset == next_reset


def test_a_clear_on_an_open_floor_changes_nothing() -> None:
    assert _floor(now=_MARKED, clear=_clear(_MARKED)) == FloorAnswer(admits=True, slug=_SLUG)


def test_a_clear_before_an_exhausted_period_began_does_not_reach_it() -> None:
    shut = _sample(_MARKED, _window(100.0, _RESET))
    period_start = _RESET - timedelta(seconds=_FIVE_HOURS)

    assert not _floor(now=_MARKED, sample=shut, clear=_clear(period_start - timedelta(minutes=1))).admits


def test_a_runner_naming_no_subscription_clears_by_its_own_clear() -> None:
    clear = _clear(_MARKED + timedelta(minutes=1))

    assert _floor(now=_MARKED + timedelta(minutes=2), mark=_mark(), clear=clear, sampled=False, slug=None).admits


def test_a_runner_spending_a_subscription_refuses_a_runner_clear_naming_the_slug() -> None:
    with pytest.raises(RunnerFloorClearRefused) as refused:
        refuse_runner_floor_clear(_SLUG)

    assert refused.value.slug == _SLUG


def test_a_runner_naming_no_subscription_may_clear_its_own_floor() -> None:
    refuse_runner_floor_clear(None)
