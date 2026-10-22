"""The subscription floor — whether a subscription's exhaustion holds its runners back, derived by value.

A subscription is held while its newest usage-limit mark stands or its newest sample shows a window exhausted; each
hold opens on evidence its window turned over, on the clock where no sample can confirm, or by a hand clear. The answer
is derived from the newest mark, sample and clear on every ask — nothing about exhaustion is stored."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.runner.subscriptions.subscription_sampler import ExternalSubscriptionUsageWindow
from blizzard.runner.throttle.usage_limits import FloorClear, UsageLimitMark
from blizzard.runner.usage.repository import ExternalUsageSampleSummary

__all__ = [
    "FLOOR_NO_RESET_FALLBACK",
    "FLOOR_UNCONFIRMED_GRACE",
    "FloorAnswer",
    "FloorHold",
    "HoldKind",
    "RunnerFloorClearRefused",
    "floor_answer",
    "refuse_runner_floor_clear",
]

#: How long past its expected reset a hold waits for a sample that is evidence either way before it clears on the
#: clock — a sampler that cannot see, or a provider slow to roll its counter, never holds the plan longer.
FLOOR_UNCONFIRMED_GRACE = timedelta(hours=1)

#: A mark naming no reset, with no sample since it placing one, expects its reset this long after it was marked.
FLOOR_NO_RESET_FALLBACK = timedelta(minutes=30)


class HoldKind(StrEnum):
    """What holds a floor: a harness's usage-limit mark, or a window a sample showed exhausted."""

    MARK = "mark"
    WINDOW = "window"


@domain_model
@dataclass(frozen=True)
class FloorHold:
    """One hold standing: its kind, the harness that marked it (a mark's) or the window it names (an exhausted
    window's), its expected reset, and whether a sample must confirm it open once that reset passes — ``False`` when
    the expected reset decides alone."""

    kind: HoldKind
    harness_id: str | None
    window: str | None
    expected_reset: datetime
    awaits_sample: bool


@domain_model
@dataclass(frozen=True)
class FloorAnswer:
    """The floor's answer for one subscription (``slug``), or for a runner spending none (``slug`` ``None``):
    whether it admits, the hold that stands when it does not, and the hand clear that alone keeps it open."""

    admits: bool
    slug: str | None
    hold: FloorHold | None = None
    clear: FloorClear | None = None


class RunnerFloorClearRefused(Exception):
    """A runner-target clear named a runner spending a subscription; its floor is the subscription's, cleared by
    ``slug``."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"the runner spends subscription {slug!r}; clear that subscription's floor instead")
        self.slug = slug


def refuse_runner_floor_clear(subscription: str | None) -> None:
    """Pass when a runner naming ``subscription`` may have its own floor cleared — it names none — else raise
    :class:`RunnerFloorClearRefused`, so nobody lifts a whole plan believing they lifted one machine."""
    if subscription is not None:
        raise RunnerFloorClearRefused(subscription)


def floor_answer(
    *,
    slug: str | None,
    sampled: bool,
    mark: UsageLimitMark | None,
    sample: ExternalUsageSampleSummary | None,
    clear: FloorClear | None,
    clear_sample: ExternalUsageSampleSummary | None = None,
    now: datetime,
) -> FloorAnswer:
    """The floor over the newest ``mark``, the newest ``sample`` and the newest ``clear`` at ``now``.

    ``sampled`` is whether a sampler reads this floor at all — ``False`` for a provider binding none and for a runner
    naming no subscription, whose holds then clear at their expected reset alone; ``sample`` is ignored there.
    ``clear_sample`` is the newest sample taken at or before ``clear`` — the windows the clear set aside; it may be
    left out when ``sample`` itself was taken by the clear."""
    newest = sample if sampled else None
    if clear_sample is None and newest is not None and clear is not None and newest.sampled_at <= clear.cleared_at:
        clear_sample = newest
    standing: list[FloorHold] = []
    set_aside = False
    if mark is not None:
        mark_hold = _mark_hold(mark, newest, sampled=sampled, now=now)
        if mark_hold is not None:
            if clear is not None and clear.cleared_at >= mark.marked_at:
                set_aside = True
            else:
                standing.append(mark_hold)
    if newest is not None:
        for window in newest.windows:
            window_hold = _window_hold(window, now)
            if window_hold is None:
                continue
            if clear is not None and _cleared(window, clear_sample if sampled else None):
                set_aside = True
            else:
                standing.append(window_hold)
    if standing:
        return FloorAnswer(admits=False, slug=slug, hold=_reported(standing))
    return FloorAnswer(admits=True, slug=slug, clear=clear if set_aside else None)


def _mark_hold(
    mark: UsageLimitMark, sample: ExternalUsageSampleSummary | None, *, sampled: bool, now: datetime
) -> FloorHold | None:
    """The hold ``mark`` still makes at ``now``, or ``None`` once it has opened."""
    expected, from_fallback = _mark_expected_reset(mark, sample)
    hold = FloorHold(
        kind=HoldKind.MARK,
        harness_id=mark.harness_id,
        window=mark.window,
        expected_reset=expected,
        awaits_sample=sampled and not from_fallback,
    )
    if sample is not None and _held_window_turned_over(mark, sample):
        return None
    if now < expected:
        return hold
    if not hold.awaits_sample:
        return None
    if sample is not None and _evidence_since(sample, expected):
        # A window it shows exhausted in its new period stands as its own hold, under its own reset.
        return None
    if now >= expected + FLOOR_UNCONFIRMED_GRACE:
        return None
    return hold


def _mark_expected_reset(mark: UsageLimitMark, sample: ExternalUsageSampleSummary | None) -> tuple[datetime, bool]:
    """``mark``'s expected reset, and whether it is the no-reset fallback: the reset its harness named, else the
    soonest pending reset of an exhausted window in a sample taken at or after it, else the fallback."""
    if mark.resets_at is not None:
        return mark.resets_at, False
    if sample is not None and sample.sampled_at >= mark.marked_at:
        pending = [w.resets_at for w in sample.windows if w.exhausted_pending(sample.sampled_at)]
        if pending:
            return min(pending), False
    return mark.marked_at + FLOOR_NO_RESET_FALLBACK, True


def _held_window_turned_over(mark: UsageLimitMark, sample: ExternalUsageSampleSummary) -> bool:
    """``sample`` shows ``mark``'s held window in a later period, under 100 percent — never true of a mark with no
    held window, which no sample opens early."""
    if mark.window is None or mark.window_resets_at is None:
        return False
    return any(
        w.window == mark.window and w.resets_at > mark.window_resets_at and w.utilization_pct < 100.0
        for w in sample.windows
    )


def _evidence_since(sample: ExternalUsageSampleSummary, reset: datetime) -> bool:
    """``sample`` was taken at or after ``reset`` and every window it reports is in a period still running when it was
    sampled — a window still reporting a passed period is a provider that has not rolled its counter yet."""
    return sample.sampled_at >= reset and all(w.resets_at > sample.sampled_at for w in sample.windows)


def _window_hold(window: ExternalSubscriptionUsageWindow, now: datetime) -> FloorHold | None:
    """The hold an exhausted ``window`` makes at ``now``: until its reset, then — the newest sample still showing that
    period, so no evidence it turned over — until the grace runs out."""
    if window.utilization_pct < 100.0:
        return None
    if now >= window.resets_at + FLOOR_UNCONFIRMED_GRACE:
        return None
    return FloorHold(
        kind=HoldKind.WINDOW, harness_id=None, window=window.window, expected_reset=window.resets_at, awaits_sample=True
    )


def _cleared(window: ExternalSubscriptionUsageWindow, clear_sample: ExternalUsageSampleSummary | None) -> bool:
    """A clear sets aside ``window``'s exhaustion: the sample standing when the clear was made showed that same window
    exhausted in that same period. A window exhausted under a later reset, or one not exhausted at the clear, is new
    evidence and holds again."""
    if clear_sample is None:
        return False
    return any(
        w.window == window.window and w.resets_at == window.resets_at and w.utilization_pct >= 100.0
        for w in clear_sample.windows
    )


def _reported(standing: list[FloorHold]) -> FloorHold:
    """The hold an answer names: a mark, the freshest reading, before any window; else the window lifting last."""
    marks = [hold for hold in standing if hold.kind is HoldKind.MARK]
    if marks:
        return marks[0]
    return max(standing, key=lambda hold: hold.expected_reset)
