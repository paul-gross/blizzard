"""The chunk-queue repository seam — the ready/not_ready lists' ordering and
a chunk's promotion between them."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class IReadChunkQueueRepository(Protocol):
    """Read-only chunk-queue access."""

    def queue_positions(self) -> dict[str, float]:
        """The newest explicit position per chunk, across both the ``ready`` queue and
        the ``not_ready`` list — the order each list's peek honours."""
        ...

    def promoted_ats(self) -> dict[str, datetime]:
        """Each promoted chunk's ``chunk_promoted.promoted_at`` — the ready-queue's
        fallback sort instant (issue #137) once a chunk has never had an explicit
        position stamped, superseding a never-promoted chunk's own ``minted_at``."""
        ...


class IWriteChunkQueueRepository(IReadChunkQueueRepository, Protocol):
    """Read-write chunk-queue access."""

    def record_promote(self, chunk_id: str, *, at: datetime) -> int | None:
        """Record a ``chunk.promoted`` fact — flips ``not_ready`` to ``ready``.

        Idempotent: a chunk already promoted keeps its first row, so a re-promote writes
        nothing. Returns the freshly-written ``chunk_promoted.id``, or ``None`` on that
        no-op replay — there is no fresh row to name."""
        ...

    def record_promote_with_tail_position(self, chunk_id: str, *, position: float, at: datetime) -> int | None:
        """Record ``chunk.promoted`` and its tail queue position in one transaction
        (:class:`~blizzard.hub.domain.promote.PromoteService`'s only write) — a crash
        lands both facts or neither, never one without the other. Idempotent the same
        way as :meth:`record_promote`: returns ``None`` on an already-promoted chunk."""
        ...

    def record_queue_position(self, chunk_id: str, *, position: float, at: datetime) -> None:
        """Append a ready chunk's new queue position; order derives."""
        ...

    def record_backlog_position(self, chunk_id: str, *, position: float, at: datetime) -> None:
        """Append a ``not_ready`` chunk's new backlog position; order derives.

        A no-op if ``chunk_id`` was promoted since the caller resolved its backlog
        candidates — a promote's fresh tail stamp must never be overridden by a
        reorder that raced it (issue #137's backlog follow-up)."""
        ...
