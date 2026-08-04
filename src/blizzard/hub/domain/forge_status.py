"""The forge-status projection — a periodic, best-effort sweep (issue #179).

The hub is truth; the forge carries a coarse, one-way reflection of it as labels.
Per annotate-capable, opted-in work source, :class:`AnnotationReconciler` derives the
desired marker for every work ref a live chunk holds (:func:`derive_marker` over the
chunk's own **derived** status, ``bzh:facts-not-status`` — no status column, nothing
new is stored), discovers the forge's actual markers statelessly
(:meth:`~blizzard.hub.work_sources.annotator.IWorkAnnotator.marked_refs`, never a
hub-side record of a past write), and writes only the differences.

Dependency-free (``bzh:domain-core``): both collaborators are injected Protocols
(:class:`~blizzard.hub.domain.work.IReadChunkRepository`,
:class:`~blizzard.hub.work_sources.source.IWorkSourceRegistry`), so :meth:`sweep` is
a single, complete, directly-callable step (``bzh:steppable-loop``) — the background
driver (``blizzard.hub.app``) is a thin sleep-and-call wrapper around it. There is no
hub-side annotation state, which is what makes a mid-sweep crash self-healing: the
next sweep re-diffs from scratch and re-converges
(``blizzard-context:/architecture/crash-correctness.md``'s scope note; pinned by
tests/test_forge_status.py::test_sweep_reconverges_after_a_simulated_mid_sweep_crash).
"""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import ChunkStatus, IReadChunkRepository, WorkRef
from blizzard.hub.work_sources.annotator import WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_log = get_logger("blizzard.hub.forge_status")

# Precedence mirrors `derive_chunk_status`'s own first-match-wins buckets, restated
# here as a lookup exhaustive over `ChunkStatus` (`test_derive_marker_is_exhaustive`
# fails the moment a new member goes unmapped) so a future status can't silently fall
# through to "no marker" by omission.
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


def derive_marker(status: ChunkStatus) -> WorkStatusMarker | None:
    """The forge marker a live chunk's derived ``status`` projects, or ``None``
    for a terminal status / no live holder at all."""
    return _MARKER_BY_STATUS[status]


class AnnotationReconciler:
    """Per opted-in work source: desired-vs-actual marker diff, writes only the gap."""

    def __init__(self, *, chunks: IReadChunkRepository, work_sources: IWorkSourceRegistry) -> None:
        self._chunks = chunks
        self._work_sources = work_sources

    def sweep(self) -> None:
        """One complete reconciliation pass over every opted-in source.

        Desired state is computed once (``live_work_refs``), then filtered per
        source so an annotator only ever sees its own refs; a source with no
        live refs still gets its ``marked_refs()`` diffed against an empty
        desired set, clearing anything stale. A per-item or per-source annotator
        failure is caught and counted rather than raised — the adapter already
        logged it once at its own wrap site (``bzh:structlog-logging``), so this
        does not re-log per item; it only emits the one aggregate INFO summary
        below, win or lose."""
        desired = self._chunks.live_work_refs()
        written = cleared = failed = 0
        considered = 0
        sources_skipped: list[str] = []
        for name in self._work_sources.annotating_names():
            annotator = self._work_sources.annotator(name)
            if annotator is None:  # pragma: no cover - annotating_names() only names built ones
                continue
            try:
                actual = annotator.marked_refs()
            except WorkAnnotateError:
                sources_skipped.append(name)
                continue
            source_refs: set[WorkRef] = {ref for ref in set(desired) | set(actual) if ref.source == name}
            considered += len(source_refs)
            for ref in source_refs:
                desired_marker = derive_marker(desired[ref]) if ref in desired else None
                actual_markers = actual.get(ref, frozenset())
                try:
                    if desired_marker is None:
                        if actual_markers:
                            annotator.clear_status(ref)
                            cleared += 1
                    elif actual_markers != {desired_marker}:
                        annotator.set_status(ref, desired_marker)
                        written += 1
                except WorkAnnotateError:
                    failed += 1
        skipped = considered - written - cleared - failed
        _log.info(
            "forge-status sweep completed",
            written=written,
            cleared=cleared,
            skipped=skipped,
            failed=failed,
            sources_skipped=sources_skipped,
        )
