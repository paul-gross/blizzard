"""``ForgeBudgets`` — each forge key's rate budget: its thresholds by priority, the cases that admit every caller, bucket
routing by the reading's bucket, spent-marking, once-per-window notices (unit tier). Readings come from GitHub-shaped
answers read through the GitHub rate dialect, as a live budget's do."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.forge.budget import (
    BudgetKey,
    BudgetNoticeKind,
    BudgetReading,
    ForgeBudgetLow,
    ForgeBudgets,
    ForgePriority,
)
from blizzard.hub.forge.landing import ForgeRateLimited
from blizzard.hub.forge.rates import rate_dialect

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
_RESET = _NOW + timedelta(seconds=600)
_KEY = BudgetKey("frg_github", "gh")


def _rate(remaining: int, *, limit: int = 100, reset: datetime = _RESET, bucket: str = "core") -> BudgetReading:
    return BudgetReading(bucket, limit, remaining, reset)


def _budgets() -> tuple[ForgeBudgets, FixedClock]:
    clock = FixedClock(_NOW)
    return ForgeBudgets(clock=clock, dialects=rate_dialect), clock


def _admitted(budgets: ForgeBudgets, priority: ForgePriority, key: BudgetKey = _KEY) -> bool:
    try:
        budgets.admit(key, priority)
    except ForgeBudgetLow:
        return False
    return True


@pytest.mark.parametrize(
    ("priority", "lowest_admitted"),
    [(ForgePriority.LANDING, 1), (ForgePriority.WATCHING, 21), (ForgePriority.SWEEPING, 41)],
)
def test_each_priority_is_admitted_above_its_threshold_and_refused_at_it(
    priority: ForgePriority, lowest_admitted: int
) -> None:
    budgets, _ = _budgets()

    budgets.learn(_KEY, _rate(lowest_admitted))
    assert _admitted(budgets, priority)

    budgets.learn(_KEY, _rate(lowest_admitted - 1))
    with pytest.raises(ForgeBudgetLow) as raised:
        budgets.admit(_KEY, priority)
    assert raised.value.resume_at == _RESET
    assert isinstance(raised.value, ForgeRateLimited)


def test_an_unheard_budget_admits_every_caller() -> None:
    budgets, _ = _budgets()

    assert all(_admitted(budgets, priority) for priority in ForgePriority)


def test_a_reading_past_its_reset_admits_every_caller() -> None:
    budgets, clock = _budgets()
    budgets.learn(_KEY, _rate(0))
    assert not _admitted(budgets, ForgePriority.LANDING)

    clock.advance(timedelta(seconds=600))

    assert all(_admitted(budgets, priority) for priority in ForgePriority)


def test_a_reading_lands_in_its_bucket() -> None:
    budgets, _ = _budgets()

    budgets.learn(_KEY, _rate(0, bucket="search"))

    assert budgets.readings(_KEY) == (BudgetReading("search", 100, 0, _RESET),)
    assert _admitted(budgets, ForgePriority.SWEEPING)
    assert not _admitted_bucket(budgets, "search")


def _admitted_bucket(budgets: ForgeBudgets, bucket: str) -> bool:
    try:
        budgets.admit(_KEY, ForgePriority.LANDING, bucket)
    except ForgeBudgetLow:
        return False
    return True


def test_a_spent_budget_refuses_every_caller_until_it_lifts() -> None:
    budgets, clock = _budgets()
    resume_at = _NOW + timedelta(seconds=120)
    budgets.learn(_KEY, _rate(90))

    budgets.spent(_KEY, resume_at)

    with pytest.raises(ForgeBudgetLow) as raised:
        budgets.admit(_KEY, ForgePriority.LANDING)
    assert raised.value.resume_at == resume_at
    clock.advance(timedelta(seconds=120))
    assert all(_admitted(budgets, priority) for priority in ForgePriority)


def test_budgets_are_kept_apart_by_forge_and_secret() -> None:
    budgets, _ = _budgets()
    budgets.learn(_KEY, _rate(0))

    assert _admitted(budgets, ForgePriority.SWEEPING, BudgetKey("frg_github", "other"))
    assert _admitted(budgets, ForgePriority.SWEEPING, BudgetKey("frg_github", None))
    assert _admitted(budgets, ForgePriority.SWEEPING, BudgetKey("frg_gitea", "gh"))


def test_a_turn_away_is_noticed_once_per_window() -> None:
    budgets, clock = _budgets()
    budgets.learn(_KEY, _rate(30))

    for _ in range(3):
        _admitted(budgets, ForgePriority.SWEEPING)
    (notice,) = budgets.drain_notices()
    assert (notice.kind, notice.key, notice.bucket) == (BudgetNoticeKind.BUDGET_LOW, _KEY, "core")
    assert (notice.remaining, notice.limit, notice.reset) == (30, 100, _RESET)
    assert budgets.drain_notices() == ()

    clock.advance(timedelta(seconds=600))
    budgets.learn(_KEY, _rate(30, reset=_RESET + timedelta(seconds=600)))
    _admitted(budgets, ForgePriority.SWEEPING)
    assert len(budgets.drain_notices()) == 1


def test_a_forge_refusal_is_noticed_once_per_window() -> None:
    budgets, clock = _budgets()
    resume_at = _NOW + timedelta(seconds=60)

    budgets.spent(_KEY, resume_at)
    budgets.spent(_KEY, resume_at)
    (notice,) = budgets.drain_notices()
    assert (notice.kind, notice.reset, notice.remaining) == (BudgetNoticeKind.RATE_LIMITED, resume_at, None)

    clock.advance(timedelta(seconds=60))
    budgets.spent(_KEY, resume_at + timedelta(seconds=60))
    assert [n.kind for n in budgets.drain_notices()] == [BudgetNoticeKind.RATE_LIMITED]
