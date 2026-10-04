"""The chunk-movement repository seam — a chunk's graph-driven transitions,
cross-graph migrations, restarts, and requeues."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.foundation.migration_source import MigrationSource
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.proposals import StampedWorkItemProposal


class IReadChunkMovementRepository(Protocol):
    """Read-only chunk-movement access."""

    def accepted_transition_target(self, chunk_id: str, *, from_node_id: str, epoch: int) -> str | None:
        """The ``to_node_id`` of an already-accepted transition out of ``from_node_id`` at
        ``epoch`` — the idempotency probe for a re-applied completion, or None."""
        ...

    def accepted_migration(self, chunk_id: str, *, from_node_id: str, epoch: int) -> bool:
        """True iff a cross-graph migration is already recorded for ``(chunk_id,
        from_node_id, epoch)`` — the replay probe for a re-applied cross-graph
        completion. A migration writes no transition, so :meth:`accepted_transition_target`
        never sees it; this is its counterpart."""
        ...


class IWriteChunkMovementRepository(IReadChunkMovementRepository, Protocol):
    """Read-write chunk-movement access."""

    def record_transition(
        self,
        *,
        transition_id: str,
        chunk_id: str,
        from_node_id: str | None,
        to_node_id: str,
        choice_name: str | None,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        runner_id: str,
        at: datetime,
        artifacts: list[StoredArtifact],
        proposals: list[StampedWorkItemProposal],
        decision_id: str | None = None,
    ) -> FenceRefusal | None:
        """One node-step's transition and its artifacts and proposals, written atomically
        behind the write fence (``bzh:epoch-fencing``) — a refusal writes nothing and is
        returned. ``decision_id`` is set only on a gate-resolving transition."""
        ...

    def record_migration(
        self,
        chunk_id: str,
        *,
        from_node_id: str | None,
        from_graph_id: str,
        to_graph_id: str,
        landed_node_id: str | None,
        choice_name: str | None,
        decision_id: str | None = None,
        model: str | None,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        at: datetime,
        artifacts: list[StoredArtifact],
        proposals: list[StampedWorkItemProposal],
        source: MigrationSource,
        release_route: bool = True,
        clear_intent: bool = False,
        migration_id: str | None = None,
    ) -> str | FenceRefusal | None:
        """Record a cross-graph migration atomically and idempotently, behind the write fence
        (``bzh:epoch-fencing``): the ``chunk_migrations`` fact, the ``chunks.graph_id`` re-pin,
        the route release (unless ``release_route`` is ``False``), the step's ``artifacts`` and
        ``proposals``, and — when ``clear_intent`` — the intent clear. Returns the
        ``migration_id``, ``None`` on replay, or the :class:`FenceRefusal` that wrote nothing."""
        ...

    def record_restart_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        from_node_id: str | None,
        to_node_id: str,
        by: str,
        at: datetime,
        decision_id: str | None = None,
        answered_question_ids: Sequence[str] = (),
        answer: str = "",
        to_graph_id: str | None = None,
    ) -> int:
        """Record a ``chunk.restarted`` fact — an operator forced the chunk onto ``to_node_id``,
        at a fence epoch this call derives one above the chunk's newest — on ``handle``'s
        already-locked connection (``bzh:store-exclusive-write``). One write with the
        answers it writes, the ``decision_id`` it names and — when ``to_graph_id`` is set —
        the migration fact re-pinning the chunk there and the standing intent that clears
        with it, so no crash leaves the move half-applied. Returns the ``chunk_restarts.id``."""
        ...

    def record_requeue_locked(self, handle: ILockedChunkRead, chunk_id: str, *, at: datetime) -> int:
        """Record a ``requeue.recorded`` fact — supersedes an open escalation — on
        ``handle``'s already-locked connection (``bzh:store-exclusive-write``), atomically
        with the route release it always accompanies.

        Returns the freshly-written ``requeues.id`` (the activity-feed's key)."""
        ...
