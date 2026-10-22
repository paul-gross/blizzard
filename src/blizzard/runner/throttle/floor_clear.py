"""Opening a floor by hand: a subscription's, shared by every runner spending it, or one runner's own when it spends
none. A clear is a fact, recorded at the clock's now; the floor reads it on the next ask."""

from __future__ import annotations

from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.runner.throttle.floor import refuse_runner_floor_clear
from blizzard.runner.throttle.usage_limits import FloorClear

__all__ = [
    "IRecordRunnerFloorClear",
    "IRecordSubscriptionFloorClear",
    "RunnerFloorClearService",
    "SubscriptionFloorClearService",
]


class IRecordSubscriptionFloorClear(Protocol):
    def record_floor_clear(self, slug: str, clear: FloorClear) -> None: ...


class IRecordRunnerFloorClear(Protocol):
    def record_floor_clear(self, clear: FloorClear) -> None: ...


class SubscriptionFloorClearService:
    """Composition-root-wired: the host's usage-limit store and the clock."""

    def __init__(self, store: IRecordSubscriptionFloorClear, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def clear(self, slug: str, *, by: str) -> FloorClear:
        """Open ``slug``'s floor for every runner spending it."""
        clear = FloorClear(cleared_at=self._clock.now(), by=by)
        self._store.record_floor_clear(slug, clear)
        return clear


class RunnerFloorClearService:
    """Composition-root-wired: the runner's own usage-limit store, the subscription it spends (``None`` for none),
    and the clock."""

    def __init__(self, store: IRecordRunnerFloorClear, subscription: str | None, clock: IClock) -> None:
        self._store = store
        self._subscription = subscription
        self._clock = clock

    def clear(self, *, by: str) -> FloorClear:
        """Open this runner's own floor; refused with
        :class:`~blizzard.runner.throttle.floor.RunnerFloorClearRefused` when it spends a subscription."""
        refuse_runner_floor_clear(self._subscription)
        clear = FloorClear(cleared_at=self._clock.now(), by=by)
        self._store.record_floor_clear(clear)
        return clear
