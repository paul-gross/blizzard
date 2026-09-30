"""The chunk-record repository seam — the chunk row itself: mint and its
mutable configuration columns."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunks.exclusive import ILockedChunkRead
from blizzard.hub.domain.work import Chunk, IntendedMigration


@dataclass(frozen=True)
class ChunkPage:
    """A bounded, keyset-paginated page of :meth:`IReadChunkRecordRepository.list_page`
    — ``next_cursor`` is ``None`` exactly when this page is the last one."""

    chunks: list[Chunk]
    next_cursor: str | None


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
        own already-derived live fleet statuses (``load_live_statuses()``), trusted as given
        with no re-verification against a fresh facts read; a caller passing a stale map
        can see a chunk bucketed by a status it has since moved past."""
        ...

    def list_not_ready(self, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        """The backlog's own candidate set (``bzh:ranking-is-per-list``). See
        `list_ready` for ``statuses``."""
        ...

    def list_all(self) -> list[Chunk]: ...

    def list_page(self, *, cursor: str | None = None, limit: int, done_since: datetime | None = None) -> ChunkPage:
        """``list_all``'s bounded sibling: newest-minted first, ``chunk_id`` breaking a
        same-instant tie. ``cursor`` is a prior
        :attr:`ChunkPage.next_cursor`; any other raises :class:`~blizzard.hub.domain.pagination.MalformedCursor`.
        ``done_since`` drops chunks settled ``done`` before it — sound, not exact: a tie stays in."""
        ...


class IWriteChunkRecordRepository(IReadChunkRecordRepository, Protocol):
    """Read-write chunk-record access."""

    def mint(self, chunk: Chunk) -> None: ...
    def set_graph_locked(self, handle: ILockedChunkRead, chunk_id: str, *, graph_id: str) -> None:
        """Repin a not-ready or ready-unclaimed chunk to a different workflow graph, on
        ``handle``'s already-locked connection (``bzh:store-exclusive-write``).

        A plain column overwrite, not an append-only fact: ``graph_id`` was already a
        mint-time column with no fact log behind it."""
        ...

    def set_defaults_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        default_model: list[str],
        default_effort: str | None,
        default_harnesses: list[str],
    ) -> None:
        """Repin a not-ready or ready-unclaimed chunk's default model/effort/harnesses, on
        ``handle``'s already-locked connection (``bzh:store-exclusive-write``).

        All three together in one write, never one at a time, so the trio cannot be left
        half-applied at a crash. An empty list / ``None`` is a real value — *express no
        preference*, the minted state — not "leave unchanged"."""
        ...

    def set_intended_migration_locked(
        self, handle: ILockedChunkRead, chunk_id: str, *, intended: IntendedMigration | None
    ) -> None:
        """Set, overwrite, or clear a chunk's standing migration intent, on ``handle``'s
        already-locked connection (``bzh:store-exclusive-write``).

        A plain column overwrite, not an append-only fact — the same shape
        :meth:`set_graph_locked` carries. ``intended=None`` clears it; a non-``None`` value
        overwrites. Carries no timestamp — the column records no ``at``, unlike this
        repository's other writes."""
        ...
