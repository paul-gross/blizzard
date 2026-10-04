"""The retry state a lane keeps around its body: the backoff formula and the outage latch.
Pure — every instant arrives as an argument (``bzh:injected-clock``)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

#: The ceiling an outage-latch lane's retry delay doubles up to.
BACKOFF_CAP = timedelta(minutes=10)
_MAX_DOUBLINGS = 32


def backoff_delay(consecutive_failures: int, base: timedelta, cap: timedelta) -> timedelta:
    """How long after a failure the next attempt waits: ``base``, doubling per consecutive
    failure, never past ``cap``."""
    if consecutive_failures < 1:
        return base
    # Stops long before timedelta's own range.
    doublings = min(consecutive_failures - 1, _MAX_DOUBLINGS)
    return min(base * 2**doublings, cap)


class OutageLatch:
    """A lane's whole-pass backoff and its one-announcement-per-outage latch, seeded lazily, once, from
    ``read_failing`` so a restart mid-outage does not announce the outage again. The caller keeps the
    events and what counts as a success."""

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
        """Whether a pass may run at ``now``; the first call seeds the latch."""
        self._seed()
        return self._next_due is None or now >= self._next_due

    def failed(self, now: datetime) -> bool:
        """Records a failed pass and schedules the next attempt; true when this failure opens
        an outage the caller should announce."""
        self._seed()
        self._failures += 1
        self._next_due = now + self.retry_in
        opens = not self._failing
        self._failing = True
        return opens

    def succeeded(self) -> bool:
        """Records a successful pass; true when it closes an outage the caller should announce."""
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
