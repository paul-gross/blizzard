"""The pure decisions that move a daemon's trace export cursor — every instant arrives as an argument.
Contract: ``blizzard-product:/plans/tracing/fleet-spans/spec/emission.md`` §The cursor and §Delivery semantics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, Self

#: The ceiling a failing export's retry delay doubles up to.
BACKOFF_CAP = timedelta(minutes=10)
_MAX_DOUBLINGS = 32


class CursorPosition(Protocol):
    @property
    def at(self) -> datetime: ...

    @classmethod
    def opening(cls, at: datetime) -> Self: ...


class JumpReason(StrEnum):
    START = "start"
    ENABLE_AFTER_GAP = "enable-after-gap"
    LAG_CAP = "lag-cap"


@dataclass(frozen=True)
class CursorJump[K: CursorPosition]:
    """A cursor move that tells nothing. ``skipped_from`` opens the window ``[skipped_from, to)``
    left for replay; a start skips nothing."""

    reason: JumpReason
    to: K
    skipped_from: K | None = None


def first_pass_jump[K: CursorPosition](
    newest: K | None, now: datetime, max_lag: timedelta, *, key: type[K]
) -> CursorJump[K] | None:
    """A process's first pass: no cursor starts at ``now``; one older than ``max_lag`` jumps to ``now``."""
    if newest is None:
        return CursorJump(JumpReason.START, key.opening(now))
    if newest.at < now - max_lag:
        return CursorJump(JumpReason.ENABLE_AFTER_GAP, key.opening(now), skipped_from=newest)
    return None


def lag_cap_jump[K: CursorPosition](
    cursor: K, oldest_unsent: datetime | None, now: datetime, max_lag: timedelta
) -> CursorJump[K] | None:
    """A later pass jumps to the lag boundary only when the oldest unsent closed unit is older than it —
    a cursor merely stale because the fleet was idle has nothing unsent and never jumps."""
    boundary = now - max_lag
    if oldest_unsent is None or oldest_unsent >= boundary:
        return None
    return CursorJump(JumpReason.LAG_CAP, type(cursor).opening(boundary), skipped_from=cursor)


def backoff_delay(consecutive_failures: int, sweep_every: timedelta) -> timedelta:
    """How long after a failed export the next attempt waits: ``sweep_every``, doubling per
    consecutive failure, never past :data:`BACKOFF_CAP`."""
    if consecutive_failures < 1:
        return sweep_every
    # Doubling stops long before timedelta's own range, which a long outage would otherwise reach.
    doublings = min(consecutive_failures - 1, _MAX_DOUBLINGS)
    return min(sweep_every * 2**doublings, BACKOFF_CAP)
