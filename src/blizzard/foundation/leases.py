"""The lease vocabulary the wire carries — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

#: A lease's derived state — computed at read time and never stored
#: (``bzh:facts-not-status``).
LeaseState = Literal["running", "stale", "parked", "backing-off", "spawning", "exited", "closed"]


class LeaseClosureReason(StrEnum):
    """The published ``lease_closures.reason`` values; the store-only mint reasons are not among them."""

    TRANSITIONED = "transitioned"
    REAPED = "reaped"
    FAILED = "failed"
    ESCALATED = "escalated"
    #: A runner-config gate: the node-step completed, the chunk parks on a decision.
    PARKED = "parked"
    #: The chunk was found reassigned, detached or unknown — abandoned, never requeued.
    RELEASED = "released"
    #: An operator restart re-aimed the chunk; its environments and route are kept.
    PREEMPTED = "preempted"
