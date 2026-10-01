"""The trace export cursor — its ordering key and the pure decisions that move it.

Contract: ``blizzard-product:/plans/tracing/fleet-spans/spec/emission.md`` §The cursor and
§Delivery semantics. Pure: no store, no exporter, no clock — every instant arrives as an argument."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from blizzard.hub.domain.tracing.steps import NodeStep

#: The ceiling a failing export's retry delay doubles up to.
BACKOFF_CAP = timedelta(minutes=10)
_MAX_DOUBLINGS = 32


@dataclass(frozen=True, order=True)
class CursorKey:
    """A position in the total order of closed steps: closing time, chunk, epoch, decision.

    ``decision_id`` is empty for runner and hub steps. :meth:`opening` is the position just before
    every step that closes at an instant, which is where a start or a jump puts the cursor."""

    at: datetime
    chunk_id: str = ""
    epoch: int = 0
    decision_id: str = ""

    @classmethod
    def of(cls, step: NodeStep) -> CursorKey:
        if step.close is None:
            raise ValueError(f"step {step.key.text()} is open and has no cursor position")
        return cls(step.close.at, step.key.chunk_id, step.epoch, step.decision_id or "")

    @classmethod
    def opening(cls, at: datetime) -> CursorKey:
        return cls(at)


class JumpReason(StrEnum):
    START = "start"
    ENABLE_AFTER_GAP = "enable-after-gap"
    LAG_CAP = "lag-cap"


@dataclass(frozen=True)
class CursorJump:
    """A cursor move that tells nothing. ``skipped_from`` opens the window ``[skipped_from, to)``
    left for replay; a start skips nothing."""

    reason: JumpReason
    to: CursorKey
    skipped_from: CursorKey | None = None


def first_pass_jump(newest: CursorKey | None, now: datetime, max_lag: timedelta) -> CursorJump | None:
    """A process's first pass: no cursor starts at ``now``; one older than ``max_lag`` jumps to ``now``."""
    if newest is None:
        return CursorJump(JumpReason.START, CursorKey.opening(now))
    if newest.at < now - max_lag:
        return CursorJump(JumpReason.ENABLE_AFTER_GAP, CursorKey.opening(now), skipped_from=newest)
    return None


def lag_cap_jump(
    cursor: CursorKey, oldest_unsent: datetime | None, now: datetime, max_lag: timedelta
) -> CursorJump | None:
    """A later pass jumps to the lag boundary only when the oldest unsent closed step is older than it —
    a cursor merely stale because the fleet was idle has no unsent step and never jumps."""
    boundary = now - max_lag
    if oldest_unsent is None or oldest_unsent >= boundary:
        return None
    return CursorJump(JumpReason.LAG_CAP, CursorKey.opening(boundary), skipped_from=cursor)


def backoff_delay(consecutive_failures: int, sweep_every: timedelta) -> timedelta:
    """How long after a failed export the next attempt waits: ``sweep_every``, doubling per
    consecutive failure, never past :data:`BACKOFF_CAP`."""
    if consecutive_failures < 1:
        return sweep_every
    # Doubling stops long before timedelta's own range, which a long outage would otherwise reach.
    doublings = min(consecutive_failures - 1, _MAX_DOUBLINGS)
    return min(sweep_every * 2**doublings, BACKOFF_CAP)
