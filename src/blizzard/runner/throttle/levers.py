"""The operator-configured levers — pace, utilization line, budget, and schedule, each a pure function of its readings.

A lever takes its settings, the newest sample (with its subscription's interval) or the host's zone, and ``now``; it
never reads a store, the clock, or another lever. The answer keeps the judged reading apart from the setting: the
reading alone rides registration, and the setting only the runner's own surfaces."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.runner.subscriptions.subscription_sampler import ExternalSubscriptionUsageWindow
from blizzard.runner.throttle.settings import (
    DAY_NAMES,
    MINUTES_PER_DAY,
    BudgetSettings,
    PaceSettings,
    ScheduleSettings,
    Span,
    UtilizationSettings,
)
from blizzard.runner.usage.repository import ExternalUsageSampleSummary, UsageTotals

__all__ = [
    "FRESH_SAMPLE_INTERVALS",
    "LeverAnswer",
    "LeverName",
    "budget_answer",
    "pace_answer",
    "schedule_answer",
    "utilization_answer",
]

#: A sample is fresh while it is younger than this many of its subscription's sampling intervals.
FRESH_SAMPLE_INTERVALS = 3

#: How many days ahead the schedule looks for the start of its next span.
_SCHEDULE_LOOKAHEAD_DAYS = 8

#: What joins the ends of a span or a run of days in the setting's words.
_DASH = "\N{EN DASH}"


class LeverName(StrEnum):
    """The levers, in the gate's order."""

    PACE = "pace"
    UTILIZATION = "utilization"
    BUDGET = "budget"
    SCHEDULE = "schedule"


@domain_model
@dataclass(frozen=True)
class LeverAnswer:
    """One lever's answer: whether it ``admits``, the ``reading`` it judged (figures only), the ``setting`` in the
    operator's words, the ``in_force`` part of the setting it judged by when that differs from the setting as
    configured, and ``until`` — the instant the same readings expect it to admit — when one can be known."""

    lever: LeverName
    admits: bool
    reading: str
    setting: str
    in_force: str | None = None
    until: datetime | None = None


def pace_answer(
    settings: PaceSettings,
    *,
    sample: ExternalUsageSampleSummary | None,
    slug: str,
    sample_interval_seconds: int,
    now: datetime,
) -> LeverAnswer:
    """Whether the spent share of the paced window is within its elapsed share plus the effective lead."""
    setting = _pace_setting(settings)
    unread = _unread(sample, slug, sample_interval_seconds, now)
    if unread is not None:
        return LeverAnswer(LeverName.PACE, False, unread, setting)
    assert sample is not None
    window = _paced_window(settings, sample)
    if window is None:
        return LeverAnswer(LeverName.PACE, False, _not_reported(settings.window), setting)
    if window.resets_at <= now:
        return LeverAnswer(LeverName.PACE, False, _turned_over(window.window), setting)
    spent = window.utilization_pct
    remaining = (window.resets_at - now).total_seconds() / window.window_seconds
    elapsed = min(100.0, max(0.0, 100.0 * (1.0 - remaining)))
    step = None
    for taper in settings.taper:
        if taper.from_pct <= spent:
            step = taper
    lead = settings.lead_pct if step is None else step.lead_pct
    in_force = f"lead {lead:g}" if step is None else f"lead {lead:g} above {step.from_pct:g}%"
    reading = f"{window.window}: {spent:.0f}% spent, {elapsed:.0f}% elapsed"
    if spent <= elapsed + lead:
        return LeverAnswer(LeverName.PACE, True, reading, setting, in_force)
    started = window.resets_at - timedelta(seconds=window.window_seconds)
    until = started + timedelta(seconds=window.window_seconds * (spent - lead) / 100.0)
    return LeverAnswer(LeverName.PACE, False, reading, setting, in_force, until)


def utilization_answer(
    settings: UtilizationSettings,
    *,
    sample: ExternalUsageSampleSummary | None,
    slug: str,
    sample_interval_seconds: int,
    now: datetime,
) -> LeverAnswer:
    """Whether every named window (every reported one when none is named) stands under the line."""
    setting = _utilization_setting(settings)
    in_force = f"line {settings.below_pct:g}%"
    unread = _unread(sample, slug, sample_interval_seconds, now)
    if unread is not None:
        return LeverAnswer(LeverName.UTILIZATION, False, unread, setting)
    assert sample is not None
    windows: list[ExternalSubscriptionUsageWindow] = []
    if settings.windows is None:
        windows = list(sample.windows)
        if not windows:
            return LeverAnswer(LeverName.UTILIZATION, False, "no window reported", setting)
    else:
        for label in settings.windows:
            found = next((w for w in sample.windows if w.window == label), None)
            if found is None:
                return LeverAnswer(LeverName.UTILIZATION, False, _not_reported(label), setting)
            windows.append(found)
    for window in windows:
        if window.resets_at <= now:
            return LeverAnswer(LeverName.UTILIZATION, False, _turned_over(window.window), setting)
    over = [w for w in windows if w.utilization_pct >= settings.below_pct]
    if not over:
        reading = ", ".join(f"{w.window}: {w.utilization_pct:.0f}%" for w in windows)
        return LeverAnswer(LeverName.UTILIZATION, True, reading, setting, in_force)
    reading = ", ".join(f"{w.window}: {w.utilization_pct:.0f}%" for w in over)
    return LeverAnswer(LeverName.UTILIZATION, False, reading, setting, in_force, min(w.resets_at for w in over))


def budget_answer(settings: BudgetSettings, *, spend: UsageTotals) -> LeverAnswer:
    """Whether the spend over the trailing window, the one figure every dollar line reads, is under the budget. No
    ``until``: when enough spend ages out depends on spend this lever does not read."""
    reading = f"{spend.figure.rendered()} of ${settings.usd:.2f} over the trailing {settings.window_hours:g}h"
    return LeverAnswer(
        LeverName.BUDGET,
        spend.spend_usd < settings.usd,
        reading,
        f"${settings.usd:.2f} over {settings.window_hours:g}h",
    )


def schedule_answer(settings: ScheduleSettings, *, zone: tzinfo, now: datetime) -> LeverAnswer:
    """Whether ``now``, on the host's wall clock in ``zone``, falls inside one of the spans; a span belongs to the day
    it starts on."""
    local_today = now.astimezone(zone).date()
    setting = _schedule_setting(settings, now.astimezone(zone).tzname() or "")
    runs = [
        _span_instants(day, span, zone)
        for offset in range(-1, _SCHEDULE_LOOKAHEAD_DAYS)
        for day in (local_today + timedelta(days=offset),)
        if day.weekday() in settings.days
        for span in settings.spans or (Span(0, MINUTES_PER_DAY),)
    ]
    if any(start <= now < end for start, end in runs):
        return LeverAnswer(LeverName.SCHEDULE, True, "inside", setting)
    upcoming = [start for start, _ in runs if start > now]
    return LeverAnswer(LeverName.SCHEDULE, False, "outside", setting, None, min(upcoming, default=None))


def _unread(sample: ExternalUsageSampleSummary | None, slug: str, interval_seconds: int, now: datetime) -> str | None:
    """Why no sample can be read, or ``None`` when ``sample`` is fresh."""
    if sample is None:
        return f"no sample of {slug}"
    if now - sample.sampled_at >= timedelta(seconds=FRESH_SAMPLE_INTERVALS * interval_seconds):
        return f"no fresh sample of {slug} since {sample.sampled_at:%H:%M}Z"
    return None


def _not_reported(label: str | None) -> str:
    return f"window {label} not reported" if label is not None else "no window reported"


def _turned_over(label: str) -> str:
    return f"window {label} has reset since its sample"


def _paced_window(settings: PaceSettings, sample: ExternalUsageSampleSummary) -> ExternalSubscriptionUsageWindow | None:
    if settings.window is not None:
        return next((w for w in sample.windows if w.window == settings.window), None)
    return max(sample.windows, key=lambda w: w.window_seconds, default=None)


def _pace_setting(settings: PaceSettings) -> str:
    words = [settings.window if settings.window is not None else "longest window", f"lead {settings.lead_pct:g} points"]
    words.extend(f"{step.lead_pct:g} above {step.from_pct:g}%" for step in settings.taper)
    return ", ".join(words)


def _utilization_setting(settings: UtilizationSettings) -> str:
    windows = "every window" if settings.windows is None else ", ".join(settings.windows)
    return f"line {settings.below_pct:g}%, {windows}"


def _schedule_setting(settings: ScheduleSettings, zone_name: str) -> str:
    hours = ", ".join(_span_words(s) for s in settings.spans) if settings.spans else "all day"
    return f"{_day_words(settings.days)} {hours} {zone_name}".rstrip()


def _span_words(span: Span) -> str:
    return f"{_clock_words(span.start_minute)}{_DASH}{_clock_words(span.end_minute)}"


def _clock_words(minute: int) -> str:
    return f"{minute // 60 % 24:02d}:{minute % 60:02d}"


def _day_words(days: Sequence[int]) -> str:
    """A run of three or more consecutive days as ``first-last`` (en dash), else the names; ``every day`` for all."""
    ordered = sorted(set(days))
    if len(ordered) == len(DAY_NAMES):
        return "every day"
    runs: list[list[int]] = []
    for day in ordered:
        if runs and runs[-1][-1] == day - 1:
            runs[-1].append(day)
        else:
            runs.append([day])
    words: list[str] = []
    for run in runs:
        if len(run) >= 3:
            words.append(f"{DAY_NAMES[run[0]]}{_DASH}{DAY_NAMES[run[-1]]}")
        else:
            words.extend(DAY_NAMES[d] for d in run)
    return ",".join(words)


def _span_instants(day: date, span: Span, zone: tzinfo) -> tuple[datetime, datetime]:
    """The instants a span starting on ``day`` opens and closes, its wall-clock times read in ``zone``."""
    midnight = datetime(day.year, day.month, day.day)
    end_day = 1 if span.end_minute <= span.start_minute else 0
    start = midnight + timedelta(minutes=span.start_minute)
    end = midnight + timedelta(days=end_day, minutes=span.end_minute)
    return _wall_instant(start, zone), _wall_instant(end, zone)


def _wall_instant(wall: datetime, zone: tzinfo) -> datetime:
    """The instant ``wall`` (naive) is on the clock in ``zone``: a time that occurs twice is its first occurrence, and
    one skipped by a spring-forward change is the first minute that does exist."""
    first = wall.replace(tzinfo=zone, fold=0).astimezone(UTC)
    if first.astimezone(zone).replace(tzinfo=None) == wall:
        return first
    # A skipped wall time: start from the instant under the offset after the change, before the change, and step to
    # the first minute whose wall clock has reached ``wall``.
    candidate = wall.replace(tzinfo=zone, fold=1).astimezone(UTC)
    while candidate.astimezone(zone).replace(tzinfo=None) < wall:
        candidate += timedelta(minutes=1)
    return candidate
