"""The chunk-movement repository seam — a chunk's graph-driven transitions,
cross-graph migrations, restarts, and requeues."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.work import MigrationSource


class IReadChunkMovementRepository(Protocol):
    """Read-only chunk-movement access."""

    def accepted_transition_target(self, chunk_id: str, *, from_node_id: str, epoch: int) -> str | None:
        """The ``to_node_id`` of an already-accepted transition out of ``from_node_id`` at
        ``epoch`` — the idempotency probe for a re-applied completion, or None."""
        ...

    def accepted_migration(self, chunk_id: str, *, from_node_id: str, epoch: int) -> bool:
        """True iff a cross-graph migration is already recorded for ``(chunk_id,
        from_node_id, epoch)`` (issue #90) — the replay probe for a re-applied cross-graph
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
        runner_id: str,
        at: datetime,
        artifacts: list[ArtifactRow],
        proposals: list[WorkItemProposalRow],
        decision_id: str | None = None,
    ) -> None:
        """One node-step's transition and its artifacts and proposals, written atomically.

        ``decision_id`` is set only on a gate-resolving transition — the Decision this
        transition resolves; ordinary transitions leave it ``None``."""
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
        at: datetime,
        artifacts: list[ArtifactRow],
        proposals: list[WorkItemProposalRow],
        source: MigrationSource,
        release_route: bool = True,
        clear_intent: bool = False,
        migration_id: str | None = None,
    ) -> str | None:
        """Record a cross-graph migration atomically and idempotently (issue #90). One
        transaction: the ``chunk_migrations`` fact, the ``chunks.graph_id`` re-pin, the route
        release (unless ``release_route`` is ``False``), the submitting step's ``artifacts``
        and ``proposals``, and — when ``clear_intent`` — the intent clear. Returns the
        ``migration_id``, ``None`` on replay."""
        ...

    def record_restart(
        self,
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
        """Record a ``chunk.restarted`` fact — an operator forced the chunk onto ``to_node_id``
        (#370), at a fence epoch this call derives one above the chunk's newest. One transaction
        with the answers it writes, the ``decision_id`` it names and — when ``to_graph_id`` is set
        (#371) — the migration fact re-pinning the chunk there and the standing intent that clears
        with it, so no crash leaves the move half-applied. Returns the ``chunk_restarts.id``."""
        ...

    def record_requeue(self, chunk_id: str, *, at: datetime) -> int:
        """Record a ``requeue.recorded`` fact — supersedes an open escalation.

        Returns the freshly-written ``requeues.id`` (issue #213's activity-feed key)."""
        ...
