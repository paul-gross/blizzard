"""The chunk-queue repository seam — the ready/not_ready lists' ordering and
a chunk's promotion between them."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction

#: The keyed lock (``bzh:transaction-graduation``) every unit deciding the ``ready`` queue's order
#: declares — the authority :meth:`IWriteChunkQueueRepository.record_queue_positions` and
#: :meth:`~IWriteChunkQueueRepository.record_promote_with_position` require.
READY_RANKING_LOCK: tuple[str, str] = ("queue_ranking", "ready")
#: The ``not_ready`` list's own ranking lock (``bzh:ranking-is-per-list``) — the authority
#: :meth:`IWriteChunkQueueRepository.record_backlog_positions` and
#: :meth:`~IWriteChunkQueueRepository.record_demote_with_position` require.
BACKLOG_RANKING_LOCK: tuple[str, str] = ("queue_ranking", "not_ready")


class IReadChunkQueueRepository(Protocol):
    """Read-only chunk-queue access. Each read joins ``transaction`` when one is given, else opens
    its own scoped read (``bzh:transaction-participation``)."""

    def queue_positions(self, chunk_ids: Sequence[str], *, transaction: Transaction | None = None) -> dict[str, float]:
        """The newest explicit position per chunk among ``chunk_ids``, across both the
        ``ready`` queue and the ``not_ready`` list — the order each list's peek honours.
        Bounded by the candidates the caller ranks, never by the store's whole history
        (``bzh:live-set-read``)."""
        ...

    def promoted_ats(self, chunk_ids: Sequence[str], *, transaction: Transaction | None = None) -> dict[str, datetime]:
        """Each currently promoted chunk's newest ``chunk_promoted.promoted_at`` among ``chunk_ids`` — the
        ready-queue's fallback sort instant once a chunk has never had an explicit
        position stamped, superseding a not-currently-promoted chunk's own ``minted_at``."""
        ...


class IWriteChunkQueueRepository(IReadChunkQueueRepository, Protocol):
    """Read-write chunk-queue access."""

    def record_promote(self, chunk_id: str, *, at: datetime) -> int | None:
        """Record a ``chunk.promoted`` fact — flips ``not_ready`` to ``ready``.

        Idempotent while promoted: a chunk currently promoted keeps its row, so a re-promote
        writes nothing; one demoted since promotes again. Returns the freshly-written
        ``chunk_promoted.id``, or ``None`` on that no-op replay — there is no fresh row to name."""
        ...

    def record_promote_with_position(
        self, chunk_id: str, *, position: float, at: datetime, transaction: LockedTransaction
    ) -> int:
        """Record ``chunk.promoted`` and its resolved queue position (the tail or an anchor's slot) in
        ``transaction``, which locks the chunk and :data:`READY_RANKING_LOCK` — a crash lands both
        facts or neither, never one without the other. Not idempotent: the caller judged, in the
        same unit, that the chunk is not currently promoted. Returns the freshly-written
        ``chunk_promoted.id``."""
        ...

    def record_demote_with_position(
        self, chunk_id: str, *, position: float, at: datetime, transaction: LockedTransaction
    ) -> int:
        """Record ``chunk.demoted`` and its resolved backlog position in ``transaction``, which locks
        the chunk and :data:`BACKLOG_RANKING_LOCK` — a crash lands both facts or neither, never a
        demoted chunk ranked by its stale ready-queue position. Not idempotent: the caller judged,
        in the same unit, that the chunk is currently promoted. Returns the freshly-written
        ``chunk_demoted.id``."""
        ...

    def record_queue_positions(
        self, positions: Sequence[tuple[str, float]], *, at: datetime, transaction: LockedTransaction
    ) -> None:
        """Append every ``(chunk_id, position)`` pair's new queue position in ``transaction``, which
        locks :data:`READY_RANKING_LOCK` — a ranking decision is correct only in the unit that read
        the order it ranks. A whole-order replace of N chunks costs one write, not N; order derives."""
        ...

    def record_backlog_positions(
        self, positions: Sequence[tuple[str, float]], *, at: datetime, transaction: LockedTransaction
    ) -> None:
        """Same shape as :meth:`record_queue_positions`, in a unit locking
        :data:`BACKLOG_RANKING_LOCK`, except that a pair naming a currently promoted chunk is dropped —
        a store-side filter kept as recorded debt, read in one bulk query, not one per pair; the
        ranking decision itself is the caller's."""
        ...
