"""Per-IP throttling for the provider-login authorize/callback routes.

A small in-memory token bucket keyed by client IP, driven entirely off the injected
clock (``bzh:injected-clock``) — deterministic under test. Rate-limiting is a liveness
control, not a durable invariant, so a restart legitimately resets it."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TypeVar

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model

#: Requests a single IP may make before it starts being throttled.
DEFAULT_CAPACITY = 10
#: Seconds of clock time that refill one token.
DEFAULT_REFILL_SECONDS = 6
#: Tokens replenished per second of elapsed clock time.
DEFAULT_REFILL_PER_SECOND = 1 / DEFAULT_REFILL_SECONDS


@domain_model
@dataclass
class TokenBucket:
    tokens: float
    last_refill_seconds: float

    def admit(self, now_seconds: float, *, capacity: int, refill_per_second: float) -> bool:
        """Refill for the time elapsed since the last check, then consume one token; ``False``, consuming nothing,
        when fewer than one is left."""
        elapsed = max(0.0, now_seconds - self.last_refill_seconds)
        self.tokens = min(capacity, self.tokens + elapsed * refill_per_second)
        self.last_refill_seconds = now_seconds
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


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
        self._buckets: dict[str, TokenBucket] = {}

    def allow(self, ip: str) -> bool:
        now_seconds = self._clock.now().timestamp()
        bucket = self._buckets.get(ip)
        if bucket is None:
            bucket = TokenBucket(tokens=float(self._capacity), last_refill_seconds=now_seconds)
            self._buckets[ip] = bucket
        return bucket.admit(now_seconds, capacity=self._capacity, refill_per_second=self._refill_per_second)


T = TypeVar("T")


@dataclass(eq=False)
class _Generation:
    settings: tuple[bool, int, float]
    throttle: IpThrottle | None
    users: int = 0


class LiveIpThrottle:
    """Keep retired buckets only while captured requests can still use them."""

    def __init__(self, *, clock: IClock) -> None:
        # Protects the process-local token buckets, not a store decision.
        # ast-grep-ignore: bzh:store-exclusive-write
        from threading import Lock

        self._clock = clock
        self._lock = Lock()
        self._current: _Generation | None = None
        self._retired: list[_Generation] = []

    @contextmanager
    def capture(self, read: Callable[[], tuple[T, tuple[bool, int, float]]]) -> Generator[tuple[T, _Generation]]:
        # Reading and registering are one operation: a request cannot pause between them
        # while another request retires and discards its generation.
        with self._lock:
            value, settings = read()
            if self._current is None or self._current.settings != settings:
                if self._current is not None and self._current.users:
                    self._retired.append(self._current)
                enabled, capacity, refill_seconds = settings
                bucket = (
                    IpThrottle(clock=self._clock, capacity=capacity, refill_per_second=1 / refill_seconds)
                    if enabled
                    else None
                )
                self._current = _Generation(settings, bucket)
            generation = self._current
            generation.users += 1
        try:
            yield value, generation
        finally:
            with self._lock:
                generation.users -= 1
                if generation.users == 0 and generation is not self._current:
                    self._retired.remove(generation)

    def allow(self, ip: str, generation: _Generation) -> bool:
        with self._lock:
            return generation.throttle is None or generation.throttle.allow(ip)
