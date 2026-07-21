"""Per-IP throttling for the provider-login authorize/callback routes (issue #92).

A small in-memory token bucket keyed by client IP, driven entirely off the injected
clock (``bzh:injected-clock``) rather than wall time — deterministic under test.
Rate-limiting is a liveness control, not a durable security invariant
(``bzh:facts-not-status`` governs durable state; a restart legitimately resets this),
so in-memory is the harness-consistent choice here, unlike the store-backed session/
identity state.
"""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock

#: Requests a single IP may make before it starts being throttled.
DEFAULT_CAPACITY = 10
#: Tokens replenished per second of elapsed clock time.
DEFAULT_REFILL_PER_SECOND = 10 / 60  # one token every 6 seconds


@dataclass
class _Bucket:
    tokens: float
    last_refill_seconds: float


class IpThrottle:
    """``allow(ip)`` consumes one token, refilling continuously since the bucket's last
    check; returns ``False`` once a bucket is empty."""

    def __init__(
        self,
        *,
        clock: IClock,
        capacity: int = DEFAULT_CAPACITY,
        refill_per_second: float = DEFAULT_REFILL_PER_SECOND,
    ) -> None:
        self._clock = clock
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, ip: str) -> bool:
        now_seconds = self._clock.now().timestamp()
        bucket = self._buckets.get(ip)
        if bucket is None:
            bucket = _Bucket(tokens=float(self._capacity), last_refill_seconds=now_seconds)
            self._buckets[ip] = bucket
        else:
            elapsed = max(0.0, now_seconds - bucket.last_refill_seconds)
            bucket.tokens = min(self._capacity, bucket.tokens + elapsed * self._refill_per_second)
            bucket.last_refill_seconds = now_seconds
        if bucket.tokens < 1:
            return False
        bucket.tokens -= 1
        return True
