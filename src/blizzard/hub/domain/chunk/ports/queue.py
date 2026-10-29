"""The chunk-queue repository seam — the ready/not_ready lists' ordering and
a chunk's promotion between them."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead


class IReadChunkQueueRepository(Protocol):
    """Read-only chunk-queue access."""

    def queue_positions(self, chunk_ids: Sequence[str]) -> dict[str, float]:
        """The newest explicit position per chunk among ``chunk_ids``, across both the
        ``ready`` queue and the ``not_ready`` list — the order each list's peek honours.
        Bounded by the candidates the caller ranks, never by the store's whole history
        (``bzh:live-set-read``)."""
        ...

    def promoted_ats(self, chunk_ids: Sequence[str]) -> dict[str, datetime]:
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

    def record_promote_with_position_locked(
        self, handle: ILockedChunkRead, chunk_id: str, *, position: float, at: datetime
    ) -> int:
        """Record ``chunk.promoted`` and its resolved queue position (the tail or an anchor's slot) on
        ``handle``'s already-locked connection (``bzh:store-exclusive-write``) — a crash
        lands both facts or neither, never one without the other. Not idempotent: the caller
        judged, under the same lock, that the chunk is not currently promoted. Returns the freshly-written
        ``chunk_promoted.id``."""
        ...

    def record_demote_with_position_locked(
        self, handle: ILockedChunkRead, chunk_id: str, *, position: float, at: datetime
    ) -> int:
        """Record ``chunk.demoted`` and its resolved backlog position on ``handle``'s already-locked
        connection (``bzh:store-exclusive-write``) — a crash lands both
        facts or neither, never a demoted chunk ranked by its stale ready-queue position. Not idempotent:
        the caller judged, under the same lock, that the chunk is currently promoted. Returns the
        freshly-written ``chunk_demoted.id``."""
        ...

    def record_queue_positions(self, positions: Sequence[tuple[str, float]], *, at: datetime) -> None:
        """Append every ``(chunk_id, position)`` pair's new queue position in one write
        transaction — a whole-order replace of N chunks costs one
        write, not N; order derives."""
        ...

    def record_backlog_positions(self, positions: Sequence[tuple[str, float]], *, at: datetime) -> None:
        """Same shape as :meth:`record_queue_positions`, but each pair is guarded against
        a chunk promoted since the caller resolved its backlog candidates — a promote's
        fresh tail stamp must never be overridden by a reorder that raced it; the
        guard itself is one bulk read, not one per pair."""
        ...
