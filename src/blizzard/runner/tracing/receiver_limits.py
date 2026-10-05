"""What bounds a telemetry receiver: a per-lease rate token bucket and the process's received/dropped tally.

Both are in-memory and process-lifetime — observations surfaced through status, not facts a restart must keep
(precedent: the hub's IP throttle). The clock is injected."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model

__all__ = [
    "BUCKET_CAPACITY",
    "BUCKET_REFILL_PER_SECOND",
    "Bucket",
    "ReceiverBounds",
    "ReceiverCount",
    "ReceiverCounter",
    "SpanRateLimiter",
]

#: Items (spans, data points or log records) a lease may send in one burst.
BUCKET_CAPACITY = 1000
#: Items per second a lease's bucket refills.
BUCKET_REFILL_PER_SECOND = 50.0


@domain_model
@dataclass(frozen=True)
class Bucket:
    """One lease's token bucket: its ``level`` as of ``at``. Refills at ``refill_per_second``
    up to ``capacity``; every instant is the caller's."""

    level: float
    at: datetime

    @classmethod
    def full(cls, capacity: int, now: datetime) -> Bucket:
        return cls(float(capacity), now)

    def refilled(self, now: datetime, *, capacity: int, refill_per_second: float) -> Bucket:
        return Bucket(min(float(capacity), self.level + (now - self.at).total_seconds() * refill_per_second), now)

    def take(self, n: int) -> Bucket | None:
        """The bucket after taking ``n`` items, or ``None`` when they do not fit — a request
        larger than the level never fits."""
        return None if n > self.level else Bucket(self.level - n, self.at)

    def idle(self, now: datetime, *, capacity: int, refill_per_second: float) -> bool:
        """Idle for a full refill: indistinguishable from a new, full bucket."""
        return (now - self.at).total_seconds() >= capacity / refill_per_second


@domain_model
@dataclass(frozen=True)
class ReceiverCount:
    """Items accepted and dropped since the runner started."""

    accepted: int
    dropped: int


class ReceiverCounter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._accepted = 0
        self._dropped = 0

    def record(self, *, accepted: int, dropped: int) -> None:
        with self._lock:
            self._accepted += accepted
            self._dropped += dropped

    def count(self) -> ReceiverCount:
        with self._lock:
            return ReceiverCount(self._accepted, self._dropped)


@dataclass(frozen=True)
class ReceiverBounds:
    """One signal's limiter and tally, counted in that signal's own unit."""

    limiter: SpanRateLimiter
    counter: ReceiverCounter

    @classmethod
    def fresh(cls, clock: IClock) -> ReceiverBounds:
        return cls(SpanRateLimiter(clock), ReceiverCounter())


class SpanRateLimiter:
    """One bucket per lease, full when first seen. A bucket idle for a full refill is indistinguishable from
    a new one, so it is evicted and the map cannot grow without bound."""

    def __init__(
        self,
        clock: IClock,
        *,
        capacity: int = BUCKET_CAPACITY,
        refill_per_second: float = BUCKET_REFILL_PER_SECOND,
    ) -> None:
        self._clock = clock
        self._capacity = capacity
        self._refill = refill_per_second
        self._lock = threading.Lock()
        self._buckets: dict[str, Bucket] = {}

    def take(self, lease_id: str, spans: int, *, now: datetime | None = None) -> bool:
        """Whether ``spans`` items fit this lease's bucket at ``now`` (the clock's, when omitted),
        taking them if so. A request larger than the bucket holds never fits."""
        when = now if now is not None else self._clock.now()
        with self._lock:
            self._evict_idle(when)
            bucket = self._buckets.get(lease_id, Bucket.full(self._capacity, when)).refilled(
                when, capacity=self._capacity, refill_per_second=self._refill
            )
            taken = bucket.take(spans)
            self._buckets[lease_id] = taken if taken is not None else bucket
            return taken is not None

    def _evict_idle(self, now: datetime) -> None:
        idle = [
            key
            for key, bucket in self._buckets.items()
            if bucket.idle(now, capacity=self._capacity, refill_per_second=self._refill)
        ]
        for key in idle:
            del self._buckets[key]
