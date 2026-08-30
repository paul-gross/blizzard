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


TERMINAL_STATUSES = frozenset({ChunkStatus.STOPPED, ChunkStatus.DONE})
