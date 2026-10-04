"""The forge-status projection — a periodic, best-effort sweep.

The hub is truth; the forge carries a one-way reflection as labels, writing only diffs against the
forge's statelessly-discovered markers so a mid-sweep crash self-heals. The store remembers which
sources the sweep annotates, so a source leaving the annotating set across a restart has its labels
cleared once through its binding; a source removed from config has no binding and is forgotten."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
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
    """The sources remembered as annotated that are no longer annotating, in name order."""
    return sorted(set(previous) - set(annotating))


@domain_model
@dataclass(frozen=True)
class AnnotationMemoryChange:
    """How the remembered annotating set moves after one pass: every source annotating now is
    remembered, and a departed source stays remembered until its clear finishes."""

    entered: tuple[str, ...]
    left: tuple[str, ...]

    @classmethod
    def of(
        cls, remembered: Iterable[str], annotating: Iterable[str], unfinished: Iterable[str]
    ) -> AnnotationMemoryChange:
        before = set(remembered)
        after = set(annotating) | set(unfinished)
        return cls(entered=tuple(sorted(after - before)), left=tuple(sorted(before - after)))


class IAnnotatedSources(Protocol):
    """The store's memory of the work sources this hub annotates."""

    def annotated_sources(self) -> frozenset[str]:
        """Every source this hub counts as annotated — its labels owed a clear on departure."""
        ...

    def record_annotated_sources(self, *, entered: Sequence[str], left: Sequence[str], at: datetime) -> None:
        """Remember ``entered`` as annotated and forget ``left``, as of ``at``."""
        ...


class _Tally:
    def __init__(self) -> None:
        self.written = self.cleared = self.failed = self.considered = 0


class AnnotationReconciler:
    """Per opted-in work source: desired-vs-actual marker diff, writes only the gap."""

    def __init__(
        self,
        *,
        work_refs: IReadChunkWorkRefsRepository,
        work_sources: IWorkSourceRegistry,
        memory: IAnnotatedSources,
        clock: IClock,
    ) -> None:
        self._work_refs = work_refs
        self._work_sources = work_sources
        self._memory = memory
        self._clock = clock

    def sweep(self) -> None:
        """One complete reconciliation pass over every opted-in source, then the one-shot clear of
        every remembered source that left the annotating set.

        A source with no live refs still has its ``marked_refs()`` diffed against an empty desired set.
        Failures are counted, not raised; a departed source whose clear did not finish is retried."""
        remembered = self._memory.annotated_sources()
        annotating = self._work_sources.annotating_names()
        leaving = departed(remembered, annotating)
        if not annotating and not leaving:
            return
        desired = self._work_refs.live_work_refs()
        tally = _Tally()
        sources_skipped: list[str] = []
        for name in annotating:
            annotator = self._work_sources.annotator(name)
            if annotator is None:  # pragma: no cover - annotating_names() only names built ones
                continue
            if self._reconcile(name, annotator, desired, tally) is None:
                sources_skipped.append(name)
        unfinished: list[str] = []
        for name in leaving:
            clearer = self._work_sources.label_clearer(name)
            if clearer is None:
                _log.warning("forge-status source left config; its labels are not cleared", source=name)
                continue
            failed = self._reconcile(name, clearer, {}, tally)
            if failed is None:
                sources_skipped.append(name)
            if failed != 0:
                unfinished.append(name)
        change = AnnotationMemoryChange.of(remembered, annotating, unfinished)
        self._memory.record_annotated_sources(entered=change.entered, left=change.left, at=self._clock.now())
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
