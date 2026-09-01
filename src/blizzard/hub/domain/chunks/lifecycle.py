"""The chunk-lifecycle repository seam — the terminal and paused states an
operator or the fleet itself drives the chunk to outside its graph's own transitions."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class IReadChunkLifecycleRepository(Protocol):
    """Read-only chunk-lifecycle access — empty: the concept's one read, ``get``, is the
    same query as ``record``'s, so callers needing it depend on
    :class:`~blizzard.hub.domain.chunks.record.IReadChunkRecordRepository` instead rather
    than standing up a second adapter for byte-identical SQL."""


class IWriteChunkLifecycleRepository(IReadChunkLifecycleRepository, Protocol):
    """Read-write chunk-lifecycle access."""

    def record_pause(self, chunk_id: str, *, paused: bool, by: str, at: datetime) -> int:
        """Append a ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins (issue #46).

        Always writes a fresh row (never a no-op — "newest fact wins" reads, it does not
        skip writes), so the ``chunk_pause_facts.id`` comes back unconditionally."""
        ...

    def record_stop(self, chunk_id: str, *, by: str, at: datetime) -> int:
        """Append the ``chunk.stopped`` fact — terminal operator abandonment (issue #118) —
        and, atomically in the same store transaction, release any live route and any held
        fleet-wide hub-exec slot. Returns the freshly-written ``chunk_stopped.id``, not the
        ``route_released.id`` this same transaction may also write."""
        ...

    def record_completion(self, chunk_id: str, *, by: str, at: datetime) -> int:
        """Append the ``chunk.completed`` fact — an operator's manual completion, including from
        ``stopped`` (issue #294) — and, atomically in the same store transaction, release any
        live route and any held fleet-wide hub-exec slot, mirroring :meth:`record_stop`. The
        caller has already checked the chunk is not already ``done``. Returns the freshly-written
        ``chunk_completed.id``."""
        ...

    def record_grouped(self, chunk_id: str, *, grouped_into: str, at: datetime) -> int:
        """Record ``chunk.grouped`` — the merged-away chunk becomes ephemeral.

        Returns the freshly-written ``chunk_grouped.id`` (issue #213's activity-feed key)."""
        ...
