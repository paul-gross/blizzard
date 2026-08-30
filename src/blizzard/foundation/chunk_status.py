"""A chunk's derived status, read by both daemons — the derivation stays in `work.py`."""

from __future__ import annotations

from enum import StrEnum


class ChunkStatus(StrEnum):
    """The derived chunk statuses. Never stored — always a query result."""

    NOT_READY = "not_ready"
    READY = "ready"
    RUNNING = "running"
    DELIVERING = "delivering"
    WAITING_ON_HUMAN = "waiting_on_human"
    NEEDS_HUMAN = "needs_human"
    PAUSED = "paused"
    STOPPED = "stopped"
    DONE = "done"

    @property
    def holds_claim(self) -> bool:
        """Whether a chunk at this status still holds the route it may be carrying (issue #140).
        Terminal outranks route liveness: a terminal transition from a runner node stamps no
        ``route.released``, so the raw route fact outlives it."""
        return self not in TERMINAL_STATUSES


# The two statuses a chunk never leaves — the one owner of "this chunk is finished".
TERMINAL_STATUSES = frozenset({ChunkStatus.STOPPED, ChunkStatus.DONE})
