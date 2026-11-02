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


class ChunkDeleteRefusal(StrEnum):
    """Why a delete of a chunk would be refused — the status refusal outranks the dependents one."""

    #: The chunk's status is outside the pre-claim window — not yet claimed, at ``not_ready`` or ``ready``.
    STATUS = "status"
    #: A standing dependency edge names the chunk as its prerequisite.
    DEPENDENTS = "dependents"
