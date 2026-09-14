"""The chunk-record repository seam — the chunk row itself: mint and its
mutable configuration columns."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.work import Chunk, IntendedMigration


class IReadChunkRecordRepository(Protocol):
    """Read-only chunk-record access."""

    def get(self, chunk_id: str) -> Chunk | None: ...
    def get_many(self, chunk_ids: Sequence[str]) -> dict[str, Chunk]:
        """`get`'s batched sibling — every requested id's chunk, keyed by chunk id. An id
        that doesn't exist or is ephemeral is silently dropped, the same as `get`
        returning None for it."""
        ...

    def graph_id_of_many(self, chunk_ids: Sequence[str]) -> dict[str, str]:
        """Every requested id's graph pin, keyed by chunk id, ephemeral ids excluded —
        `get_many`'s narrower sibling for a caller that only needs the graph pin."""
        ...

    def list_ready(self, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        """The ready queue's own candidate set, bucketed by ``statuses`` — the caller's
        own already-derived fleet statuses (``load_all_statuses()``), trusted as given
        with no re-verification against a fresh facts read; a caller passing a stale map
        can see a chunk bucketed by a status it has since moved past."""
        ...

    def list_not_ready(self, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        """The backlog's own candidate set (``bzh:ranking-is-per-list``). See
        `list_ready` for ``statuses``."""
        ...

    def list_all(self) -> list[Chunk]: ...


class IWriteChunkRecordRepository(IReadChunkRecordRepository, Protocol):
    """Read-write chunk-record access."""

    def mint(self, chunk: Chunk) -> None: ...
    def set_graph(self, chunk_id: str, *, graph_id: str) -> None:
        """Repin a not-ready or ready-unclaimed chunk to a different workflow graph (issue #27, #120).

        A plain column overwrite, not an append-only fact: ``graph_id`` was already a
        mint-time column with no fact log behind it. The caller has already checked the
        chunk is still unclaimed, under the claim lock (issue #120)."""
        ...

    def set_defaults(self, chunk_id: str, *, default_model: list[str], default_effort: str | None) -> None:
        """Repin a not-ready or ready-unclaimed chunk's default model/effort (issue #144)
        — see :meth:`set_graph`. Both together in one write, never one at a time, so the
        pair cannot be left half-applied at a crash. An empty list / ``None`` is a real
        value — *express no preference*, the minted state — not "leave unchanged"."""
        ...

    def set_intended_migration(self, chunk_id: str, *, intended: IntendedMigration | None) -> None:
        """Set, overwrite, or clear a chunk's standing migration intent (issue #124).
        A plain column overwrite, not an append-only fact — the same shape :meth:`set_graph`
        carries. ``intended=None`` clears it; a non-``None`` value overwrites. Carries no
        timestamp — the column records no ``at``, unlike this repository's other writes."""
        ...
