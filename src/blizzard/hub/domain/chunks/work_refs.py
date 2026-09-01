"""The chunk-work-refs repository seam (blizzard#411) — the wrapped work items a chunk
holds, and merge-group survivorship over them."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.work import WorkRef


class IReadChunkWorkRefsRepository(Protocol):
    """Read-only chunk-work-refs access."""

    def find_live_holder(self, pointer: WorkRef) -> str | None:
        """The chunk_id of a live (non-terminal) chunk holding ``pointer``, or None."""
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
