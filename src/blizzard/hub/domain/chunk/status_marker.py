"""The work-status marker: the forge-visible bucket a live chunk's derived status projects onto."""

from __future__ import annotations

from enum import StrEnum

from blizzard.foundation.chunk_status import ChunkStatus

__all__ = ["WorkStatusMarker"]


class WorkStatusMarker(StrEnum):
    """The bucket a live chunk's derived status projects onto the forge.

    Domain vocabulary; a work-source adapter owns rendering it into a vendor-shaped label."""

    INGESTED = "ingested"
    IN_PROGRESS = "in_progress"

    @classmethod
    def of(cls, status: ChunkStatus) -> WorkStatusMarker | None:
        """The marker ``status`` projects, ``None`` for a terminal one — no live holder to show.

        Pinned by
        ``tests/unit/hub/domain/chunk/test_status_marker.py::test_marker_of_is_exhaustive_over_chunk_status``."""
        return _MARKER_BY_STATUS[status]


_MARKER_BY_STATUS: dict[ChunkStatus, WorkStatusMarker | None] = {
    ChunkStatus.NOT_READY: WorkStatusMarker.INGESTED,
    ChunkStatus.READY: WorkStatusMarker.INGESTED,
    ChunkStatus.RUNNING: WorkStatusMarker.IN_PROGRESS,
    ChunkStatus.PAUSED: WorkStatusMarker.IN_PROGRESS,
    ChunkStatus.WAITING_ON_HUMAN: WorkStatusMarker.IN_PROGRESS,
    ChunkStatus.NEEDS_HUMAN: WorkStatusMarker.IN_PROGRESS,
    ChunkStatus.DELIVERING: WorkStatusMarker.IN_PROGRESS,
    ChunkStatus.STOPPED: None,
    ChunkStatus.DONE: None,
}
