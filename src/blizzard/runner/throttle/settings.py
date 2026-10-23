"""The operator-configured throttles of one runner, as a pure value.

The config edge parses and validates ``[runner.throttle]`` into these records; nothing here reads a file, a clock, or
a store. A lever whose record is ``None`` does not exist: it is not asked and shows nothing."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model

__all__ = [
    "DAY_NAMES",
    "DEFAULT_PACE_LEAD_PCT",
    "MINUTES_PER_DAY",
    "PaceSettings",
    "ScheduleSettings",
    "Span",
    "TaperStep",
    "ThrottleSettings",
    "UtilizationSettings",
]

#: How many points of the window a pace's spend may run ahead of its elapsed share when no ``lead_pct`` is set.
DEFAULT_PACE_LEAD_PCT = 5.0

#: Lower-case day names in weekday order, Monday first — the index is :meth:`datetime.date.weekday`.
DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

MINUTES_PER_DAY = 24 * 60


@domain_model
@dataclass(frozen=True)
class TaperStep:
    """From a spent share of ``from_pct`` up to the next step, the pace's lead is ``lead_pct``."""

    from_pct: float
    lead_pct: float


@domain_model
@dataclass(frozen=True)
class PaceSettings:
    """The pacing lever: the window paced against (``None`` — the longest the sample reports), its base lead, and the
    taper steps that narrow the lead as the window fills, strictly ascending by ``from_pct``."""

    window: str | None = None
    lead_pct: float = DEFAULT_PACE_LEAD_PCT
    taper: tuple[TaperStep, ...] = ()


@domain_model
@dataclass(frozen=True)
class UtilizationSettings:
    """The utilization line: work only while every named window (``None`` — every window reported) is under
    ``below_pct``."""

    below_pct: float
    windows: tuple[str, ...] | None = None


@domain_model
@dataclass(frozen=True)
class Span:
    """One span of a day in minutes since midnight; an ``end_minute`` not after ``start_minute`` runs past midnight."""

    start_minute: int
    end_minute: int


@domain_model
@dataclass(frozen=True)
class ScheduleSettings:
    """When the runner is willing to start work: the weekdays (Monday ``0``) a span may start on, and the spans, each
    belonging to the day it starts on. No spans means the whole of each listed day."""

    days: tuple[int, ...] = tuple(range(len(DAY_NAMES)))
    spans: tuple[Span, ...] = ()


@domain_model
@dataclass(frozen=True)
class ThrottleSettings:
    """The levers a runner configures, each ``None`` when absent."""

    pace: PaceSettings | None = None
    utilization: UtilizationSettings | None = None
    schedule: ScheduleSettings | None = None
