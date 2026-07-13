"""The injected clock (``bzh:injected-clock``).

All time in loop, store, and domain code flows through an ``IClock`` wired at the
composition root — never a direct ``datetime.now()`` / ``time.time()``, and never
a SQLAlchemy column default. Tests bind ``FixedClock`` so lease TTLs, reap
thresholds, and "overnight" waits pass in milliseconds and deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


class IClock(Protocol):
    """The time seam. Every timestamp comes from ``now()``."""

    def now(self) -> datetime: ...


class SystemClock:
    """Production clock — the real wall clock, in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


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
