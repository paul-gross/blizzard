"""The chunk-hub-exec repository seam — the generic hub command node's
transition/poll bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.wait_kinds import WaitKind
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.kernel.transactions import LockedTransaction


@domain_model
@dataclass(frozen=True)
class HubStepTransitionWrite:
    """What recording a hub step's transition did: whether it wrote, and the ids of the ``work-item-close-skipped``
    event rows it wrote."""

    wrote: bool
    close_skipped: tuple[int, ...] = ()


class IReadChunkHubExecRepository(Protocol):
    """Read-only chunk-hub-exec access."""


class IWriteChunkHubExecRepository(IReadChunkHubExecRepository, Protocol):
    """Read-write chunk-hub-exec access."""

    def record_hub_step_transition(
        self,
        chunk_id: str,
        *,
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        admission: EpochAdmission,
        terminal: bool,
        runner_id: str,
        transition_id: str,
        at: datetime,
        artifacts: list[StoredArtifact],
        release_route: bool,
        close_decision: CloseEnqueueDecision,
        transaction: LockedTransaction,
    ) -> HubStepTransitionWrite:
        """Record a hub command node's exit transition in ``transaction``, which locks the chunk's row
        (``bzh:transaction-participation``) — a turn's verdict, committed with the turn's end. ``release_route`` is True
        only when ``to_node_id`` is the reserved terminal. Answers ``wrote`` False, writing nothing, on a replay — the
        transition's existence at ``(chunk_id, from_node_id, epoch)`` — or when the write fence refuses
        (``bzh:epoch-fencing``)."""
        ...

    def record_hub_node_poll(
        self,
        chunk_id: str,
        *,
        node_id: str,
        epoch: int,
        kind: WaitKind,
        at: datetime,
        transaction: LockedTransaction,
        next_poll_at: datetime | None = None,
    ) -> None:
        """Append one pending-poll-attempt fact in ``transaction``, which locks the chunk's row
        (``bzh:transaction-participation``) — a turn's wait, committed with the turn's end, never a transition.
        ``kind`` is what the wait waits on; ``next_poll_at`` is the earliest moment the waiting pass asked to be polled
        again. Append-only, with no
        idempotency guard: a poll attempt recorded twice only widens the gating's read."""
        ...
