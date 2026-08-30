"""The write-half work-source seam — status markers projected onto the forge.

A sibling seam to the read-only ``IWorkSource`` rather than optional methods on it, so
"this source may not annotate" is a *presence* question the registry answers. Only a
per-source, opted-in binding builds one (``bzh:dependency-injection``), which makes
"never written to" a property of the object graph rather than a remembered branch."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.work import WorkRef


class WorkStatusMarker(StrEnum):
    """The bucket a live chunk's derived status projects onto the forge.

    Domain vocabulary; an adapter owns rendering it into a vendor-shaped label."""

    INGESTED = "ingested"
    IN_PROGRESS = "in_progress"

    @classmethod
    def of(cls, status: ChunkStatus) -> WorkStatusMarker | None:
        """The marker ``status`` projects, ``None`` for a terminal one — no live holder to show.

        Precedence mirrors ``derive_chunk_status``'s first-match-wins buckets, restated as a lookup
        exhaustive over :class:`ChunkStatus` (``test_marker_of_is_exhaustive``)."""
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
    rate limit, or an insufficient-scope token. Degrades to a logged skip; never
    raised past the reconciler's sweep."""


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
        *set* of markers each carries — a ref with more than one marker is visible
        to the reconciler's diff as wrong, not as already-correct."""
        ...
