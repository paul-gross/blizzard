"""The write-half work-source seam — status markers projected onto the forge."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunk.model import WorkRef


class WorkStatusMarker(StrEnum):
    """The bucket a live chunk's derived status projects onto the forge.

    Domain vocabulary; an adapter owns rendering it into a vendor-shaped label."""

    INGESTED = "ingested"
    IN_PROGRESS = "in_progress"

    @classmethod
    def of(cls, status: ChunkStatus) -> WorkStatusMarker | None:
        """The marker ``status`` projects, ``None`` for a terminal one — no live holder to show.

        Pinned by ``tests/test_forge_status.py::test_marker_of_is_exhaustive_over_chunk_status``."""
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


class WorkAnnotateError(Exception):
    """The forge write/read for annotation failed — an unreachable forge, a
    rate limit, or an insufficient-scope token."""

    def __init__(self, message: str = "", *, retry_after: datetime | None = None) -> None:
        super().__init__(message)
        #: Set only when the forge's rate budget turned the call away or the forge refused it for rate: when to retry.
        self.retry_after = retry_after


class IWorkAnnotator(Protocol):
    """One configured, credentialed work-source binding's write half."""

    def set_status(self, pointer: WorkRef, marker: WorkStatusMarker) -> None:
        """Set ``pointer``'s marker, exclusively and idempotently — the other
        marker is removed if present."""
        ...

    def clear_status(self, pointer: WorkRef) -> None:
        """Remove every marker from ``pointer``."""
        ...

    def marked_refs(self) -> dict[WorkRef, frozenset[WorkStatusMarker]]:
        """Every ref this binding's forge currently carries a marker on, with the
        *set* of markers each carries, more than one included."""
        ...
