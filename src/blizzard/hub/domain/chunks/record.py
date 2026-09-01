"""The chunk-record repository seam (blizzard#411) — the chunk row itself: mint and its
mutable configuration columns."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.work import Chunk, IntendedMigration


class IReadChunkRecordRepository(Protocol):
    """Read-only chunk-record access."""

    def get(self, chunk_id: str) -> Chunk | None: ...
    def list_ready(self) -> list[Chunk]: ...
    def list_not_ready(self) -> list[Chunk]:
        """The backlog's own candidate set (``bzh:ranking-is-per-list``)."""
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
