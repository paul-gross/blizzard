"""The forge-status projection — a periodic, best-effort sweep (issue #179).

The hub is truth; the forge carries a one-way reflection of it as labels: only diffs
between a live chunk's derived status and the forge's statelessly-discovered markers are
written. No hub-side state, so a mid-sweep crash self-heals (``tests/test_forge_status.py``).
"""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import IReadChunkRepository, WorkRef
from blizzard.hub.work_sources.annotator import WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_log = get_logger("blizzard.hub.forge_status")


class AnnotationReconciler:
    """Per opted-in work source: desired-vs-actual marker diff, writes only the gap."""

    def __init__(self, *, chunks: IReadChunkRepository, work_sources: IWorkSourceRegistry) -> None:
        self._chunks = chunks
        self._work_sources = work_sources

    def sweep(self) -> None:
        """One complete reconciliation pass over every opted-in source.

        Desired state is computed once, then filtered per source; a source with no live
        refs still gets its ``marked_refs()`` diffed against an empty desired set,
        clearing anything stale. A per-item or per-source failure is counted, not raised."""
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
                desired_marker = WorkStatusMarker.of(desired[ref]) if ref in desired else None
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
