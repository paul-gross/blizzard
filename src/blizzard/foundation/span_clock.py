"""The span clock — stdlib only, so a command's root can build one without loading the span emitter."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from blizzard.foundation.roles import collaborator


@collaborator
@dataclass(frozen=True)
class Clock:
    """Epoch nanoseconds from one wall-clock anchor plus monotonic deltas, so a span's duration
    never goes negative when the wall clock steps. Both sources are injectable."""

    wall_ns: Callable[[], int] = time.time_ns
    monotonic_ns: Callable[[], int] = time.monotonic_ns
    _anchor: tuple[int, int] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_anchor", (self.wall_ns(), self.monotonic_ns()))

    def now_ns(self) -> int:
        wall, monotonic = self._anchor
        return wall + (self.monotonic_ns() - monotonic)
