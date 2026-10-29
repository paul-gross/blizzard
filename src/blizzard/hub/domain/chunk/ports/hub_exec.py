"""The chunk-hub-exec repository seam — the generic hub command node's
per-tenant serialization slot and its transition/poll bookkeeping."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission


class IReadChunkHubExecRepository(Protocol):
    """Read-only chunk-hub-exec access."""

    def count_live_hub_exec_slots(self) -> int:
        """The number of this tenant's currently-live hub-execution slots — never more than 1
        (``hub:one-live-exec-slot``)."""
        ...


class IWriteChunkHubExecRepository(IReadChunkHubExecRepository, Protocol):
    """Read-write chunk-hub-exec access."""

    def acquire_hub_exec_slot(self, chunk_id: str, *, node_id: str, at: datetime, stale_after: timedelta) -> str | None:
        """Acquire this tenant's hub-execution serialization slot, or ``None`` if busy.
        A FACT-based lease (``bzh:facts-not-status``), not an in-process lock: insert-if-
        none-live in one transaction. Not reentrant: a live slot defers every request, the
        holder chunk's own included, unless older than ``stale_after``, when it is reclaimed."""
        ...

    def release_hub_exec_slot(self, slot_id: str, *, at: datetime) -> None:
        """Release the hub-execution slot ``slot_id`` — idempotent, and never another run's."""
        ...

    def release_live_hub_exec_slots(self, *, at: datetime) -> int:
        """Release every live hub-execution slot of this tenant and return how many — sound only while one
        hub process serves the store."""
        ...

    def record_hub_step_transition_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        admission: EpochAdmission,
        runner_id: str,
        transition_id: str,
        at: datetime,
        artifacts: list[StoredArtifact],
        release_route: bool,
    ) -> bool:
        """Record a hub command node's exit transition on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``) — a turn's verdict, committed with the turn's end. ``release_route`` is True
        only when ``to_node_id`` is the reserved terminal. Returns False, writing nothing, on a replay — the
        transition's existence at ``(chunk_id, from_node_id, epoch)`` — or when the write fence refuses
        (``bzh:epoch-fencing``)."""
        ...

    def record_hub_node_poll_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        node_id: str,
        epoch: int,
        at: datetime,
        next_poll_at: datetime | None = None,
    ) -> None:
        """Append one pending-poll-attempt fact on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``) — a turn's wait, committed with the turn's end, never a transition.
        ``next_poll_at`` is the earliest moment the waiting pass asked to be polled again. Append-only, with no
        idempotency guard: a poll attempt recorded twice only widens the gating's read."""
        ...
