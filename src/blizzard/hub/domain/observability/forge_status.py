"""The forge-status projection — a periodic, best-effort sweep.

The hub is truth; the forge carries a one-way reflection of it as labels: only diffs
between a live chunk's derived status and the forge's statelessly-discovered markers are
written, so a mid-sweep crash self-heals (``tests/test_forge_status.py``). The one memory
the sweep keeps is the write half of every source it annotated last pass: a source that
leaves the annotating set — annotation turned off, or retired — has every label it carries
cleared once through it, so the forge never keeps showing a status the hub stopped
reflecting. That memory lives in the process; a source that leaves while the hub is down
keeps its labels.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.work_sources.annotator import IWorkAnnotator, WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_log = get_logger("blizzard.hub.forge_status")

MarkerAction = Literal["set", "clear", "skip"]


def marker_action(desired: WorkStatusMarker | None, actual: frozenset[WorkStatusMarker]) -> MarkerAction:
    """What a ref's labels need: cleared when the hub shows nothing for it but the forge carries a
    marker, set when the forge carries anything but exactly the desired marker, else nothing."""
    if desired is None:
        return "clear" if actual else "skip"
    return "skip" if actual == {desired} else "set"


def departed(previous: Iterable[str], annotating: Iterable[str]) -> list[str]:
    """The sources annotated last pass that are no longer annotating, in name order."""
    return sorted(set(previous) - set(annotating))


class _Tally:
    def __init__(self) -> None:
        self.written = self.cleared = self.failed = self.considered = 0


class AnnotationReconciler:
    """Per opted-in work source: desired-vs-actual marker diff, writes only the gap."""

    def __init__(self, *, work_refs: IReadChunkWorkRefsRepository, work_sources: IWorkSourceRegistry) -> None:
        self._work_refs = work_refs
        self._work_sources = work_sources
        self._annotated: dict[str, IWorkAnnotator] = {}

    def sweep(self) -> None:
        """One complete reconciliation pass over every opted-in source, then the one-shot clear of
        every source that left the annotating set.

        Desired state is computed once, then filtered per source; a source with no live
        refs still gets its ``marked_refs()`` diffed against an empty desired set,
        clearing anything stale. A per-item or per-source failure is counted, not raised;
        a departed source whose clear did not finish is tried again next pass."""
        desired = self._work_refs.live_work_refs()
        tally = _Tally()
        sources_skipped: list[str] = []
        annotating: dict[str, IWorkAnnotator] = {}
        for name in self._work_sources.annotating_names():
            annotator = self._work_sources.annotator(name)
            if annotator is None:  # pragma: no cover - annotating_names() only names built ones
                continue
            annotating[name] = annotator
            if self._reconcile(name, annotator, desired, tally) is None:
                sources_skipped.append(name)
        unfinished: dict[str, IWorkAnnotator] = {}
        for name in departed(self._annotated, annotating):
            failed = self._reconcile(name, self._annotated[name], {}, tally)
            if failed is None:
                sources_skipped.append(name)
            if failed != 0:
                unfinished[name] = self._annotated[name]
        self._annotated = {**unfinished, **annotating}
        _log.info(
            "forge-status sweep completed",
            written=tally.written,
            cleared=tally.cleared,
            skipped=tally.considered - tally.written - tally.cleared - tally.failed,
            failed=tally.failed,
            sources_skipped=sources_skipped,
        )

    def _reconcile(
        self, name: str, annotator: IWorkAnnotator, desired: Mapping[WorkRef, ChunkStatus], tally: _Tally
    ) -> int | None:
        """Write the gap between ``desired`` and what ``name``'s forge carries; how many writes
        failed, or ``None`` when its markers could not be read."""
        try:
            actual = annotator.marked_refs()
        except WorkAnnotateError:
            return None
        failed = 0
        source_refs: set[WorkRef] = {ref for ref in set(desired) | set(actual) if ref.source == name}
        tally.considered += len(source_refs)
        for ref in source_refs:
            desired_marker = WorkStatusMarker.of(desired[ref]) if ref in desired else None
            try:
                match marker_action(desired_marker, actual.get(ref, frozenset())):
                    case "clear":
                        annotator.clear_status(ref)
                        tally.cleared += 1
                    case "set":
                        assert desired_marker is not None
                        annotator.set_status(ref, desired_marker)
                        tally.written += 1
                    case "skip":
                        pass
            except WorkAnnotateError:
                failed += 1
        tally.failed += failed
        return failed
