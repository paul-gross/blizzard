"""What bounds the span receiver: a per-lease span-rate token bucket and the process's received/dropped tally.

Both are in-memory and process-lifetime — observations surfaced through status, not facts a restart must keep
(precedent: the hub's IP throttle). The clock is injected."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock

__all__ = ["BUCKET_CAPACITY", "BUCKET_REFILL_PER_SECOND", "ReceiverCount", "ReceiverCounter", "SpanRateLimiter"]

#: Spans a lease may send in one burst.
BUCKET_CAPACITY = 1000
#: Spans per second a lease's bucket refills.
BUCKET_REFILL_PER_SECOND = 50.0


@dataclass(frozen=True)
class ReceiverCount:
    """Spans accepted and dropped since the runner started."""

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
        self._buckets: dict[str, tuple[float, datetime]] = {}

    def take(self, lease_id: str, spans: int) -> bool:
        """Whether ``spans`` fit this lease's bucket now, taking them if so. A request larger than the bucket
        holds never fits."""
        now = self._clock.now()
        with self._lock:
            self._evict_idle(now)
            level, at = self._buckets.get(lease_id, (float(self._capacity), now))
            level = min(float(self._capacity), level + (now - at).total_seconds() * self._refill)
            if spans > level:
                self._buckets[lease_id] = (level, now)
                return False
            self._buckets[lease_id] = (level - spans, now)
            return True

    def _evict_idle(self, now: datetime) -> None:
        full_refill = self._capacity / self._refill
        idle = [key for key, (_, at) in self._buckets.items() if (now - at).total_seconds() >= full_refill]
        for key in idle:
            del self._buckets[key]
