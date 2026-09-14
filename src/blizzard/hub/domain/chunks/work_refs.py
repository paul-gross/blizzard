"""The chunk-work-refs repository seam — the wrapped work items a chunk
holds, and merge-group survivorship over them."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Protocol

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.hub.domain.work import WorkRef


def resolve_live_holder(chunk_ids: Iterable[str], statuses: Mapping[str, ChunkStatus]) -> str | None:
    """The live-holder tie-break every liveness read shares (``bzh:domain-takes-objects``):
    the lowest id among ``chunk_ids`` whose status is not terminal — more than one live
    holder shouldn't happen, so the lowest id picks deterministically. ``chunk_ids`` is
    assumed already stripped of ephemeral holders; a chunk id absent from ``statuses``
    reads as :class:`ChunkStatus.READY` (unrecorded facts), the same fallback a per-chunk
    status read used before this rule had its own function."""
    live = sorted(c for c in chunk_ids if statuses.get(c, ChunkStatus.READY) not in TERMINAL_STATUSES)
    return live[0] if live else None


class IReadChunkWorkRefsRepository(Protocol):
    """Read-only chunk-work-refs access."""

    def find_live_holder(self, pointer: WorkRef) -> str | None:
        """The chunk_id of a live (non-terminal) chunk holding ``pointer``, or None."""
        ...

    def live_holders(self, pointers: Iterable[WorkRef]) -> dict[WorkRef, str]:
        """`find_live_holder`'s batched sibling — every pointer with a live (non-terminal)
        chunk holding it, keyed by pointer; a pointer with no live holder has no entry
        at all, never mapped to None."""
        ...

    def live_work_refs(self) -> dict[WorkRef, ChunkStatus]:
        """Every work ref held by a live (non-terminal) chunk, with that chunk's
        derived status — the inverse of :meth:`find_live_holder`, for the
        forge-status reconciler's desired-state sweep (issue #179)."""
        ...


class IWriteChunkWorkRefsRepository(IReadChunkWorkRefsRepository, Protocol):
    """Read-write chunk-work-refs access."""

    def add_work_refs(self, chunk_id: str, pointers: list[WorkRef], *, at: datetime) -> None:
        """Fold work refs into a group survivor, de-duped by (source, ref)."""
        ...
