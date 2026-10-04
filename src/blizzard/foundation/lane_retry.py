"""A lane's backoff formula and outage latch; every instant arrives as an argument."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

BACKOFF_CAP = timedelta(minutes=10)
_MAX_DOUBLINGS = 32


def backoff_delay(consecutive_failures: int, base: timedelta, cap: timedelta) -> timedelta:
    if consecutive_failures < 1:
        return base
    doublings = min(consecutive_failures - 1, _MAX_DOUBLINGS)
    return min(base * 2**doublings, cap)


class OutageLatch:
    def __init__(self, base: timedelta, read_failing: Callable[[], bool], cap: timedelta = BACKOFF_CAP) -> None:
        self._base = base
        self._cap = cap
        self._read_failing = read_failing
        self._seeded = False
        self._failing = False
        self._failures = 0
        self._next_due: datetime | None = None

    @property
    def failures(self) -> int:
        return self._failures

    @property
    def retry_in(self) -> timedelta:
        return backoff_delay(self._failures, self._base, self._cap)

    def is_due(self, now: datetime) -> bool:
        self._seed()
        return self._next_due is None or now >= self._next_due

    def failed(self, now: datetime) -> bool:
        self._seed()
        self._failures += 1
        self._next_due = now + self.retry_in
        opens = not self._failing
        self._failing = True
        return opens

    def succeeded(self) -> bool:
        self._seed()
        self._failures = 0
        self._next_due = None
        closes = self._failing
        self._failing = False
        return closes

    def _seed(self) -> None:
        if not self._seeded:
            self._failing = self._read_failing()
            self._seeded = True
