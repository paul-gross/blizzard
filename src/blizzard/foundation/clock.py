"""The injected clock (``bzh:injected-clock``).

All time flows through an ``IClock`` wired at the composition root — never a direct
``datetime.now()`` / ``time.time()``, and never a SQLAlchemy column default, so that
a bound ``FixedClock`` makes every duration deterministic."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol


class IClock(Protocol):
    """The time seam. Every timestamp comes from ``now()``."""

    def now(self) -> datetime: ...


class IMonotonicClock(Protocol):
    """The elapsed-time seam: a monotonic reading and the sleep that spends it. One seam,
    not two callables, so a bounded loop's delay and its clock cannot drift apart."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Production clock — the real wall clock, in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class SystemMonotonicClock:
    """Production monotonic clock — ``time.monotonic`` and ``time.sleep``."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass
class ManualMonotonicClock:
    """Test monotonic clock — ``sleep`` advances its own reading and records the delay,
    so a loop bounded by elapsed time terminates without really waiting."""

    reading: float = 0.0
    delays: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.reading

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.reading += seconds

    def advance(self, seconds: float) -> None:
        self.reading += seconds


@dataclass
class FixedClock:
    """Test clock — returns a controllable instant that ``advance`` moves."""

    instant: datetime

    def now(self) -> datetime:
        return self.instant

    def advance(self, delta: timedelta) -> None:
        self.instant += delta


def _conforms_system_clock(x: SystemClock) -> IClock:
    return x


def _conforms_fixed_clock(x: FixedClock) -> IClock:
    return x


def _conforms_system_monotonic_clock(x: SystemMonotonicClock) -> IMonotonicClock:
    return x


def _conforms_manual_monotonic_clock(x: ManualMonotonicClock) -> IMonotonicClock:
    return x
