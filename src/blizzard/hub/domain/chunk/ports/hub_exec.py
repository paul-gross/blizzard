"""The chunk-hub-exec repository seam — the generic hub command node's
transition/poll bookkeeping."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission


class IReadChunkHubExecRepository(Protocol):
    """Read-only chunk-hub-exec access."""


class IWriteChunkHubExecRepository(IReadChunkHubExecRepository, Protocol):
    """Read-write chunk-hub-exec access."""

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
