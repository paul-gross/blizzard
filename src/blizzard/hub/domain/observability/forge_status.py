"""The forge-status projection — a periodic, best-effort sweep, gated by an in-memory probe.

The hub is truth; the forge carries a one-way reflection as labels, writing only diffs against the
forge's discovered markers. The store remembers which sources the sweep annotates, so a departing
source has its labels cleared once; a fresh process runs full, and the floor bounds forge-side drift."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository, WorkRefsSignature
from blizzard.hub.work_sources.annotator import IWorkAnnotator, WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_log = get_logger("blizzard.hub.forge_status")

MarkerAction = Literal["set", "clear", "skip"]

#: The full-pass floor, in sweep intervals — how long forge-side drift may outlive a skipped pass.
FULL_PASS_FLOOR_INTERVALS = 5


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
class AnnotationProbe:
    """Everything that can move what the sweep writes without a floor elapsing: the hub's live
    work-ref inputs and the set of sources annotating now (read from config on every call)."""

    work_refs: WorkRefsSignature
    annotating: frozenset[str]


def annotation_due(
    last_probe: AnnotationProbe | None,
    probe: AnnotationProbe,
    last_full_pass_at: datetime | None,
    now: datetime,
    floor: timedelta,
) -> bool:
    """A pass runs when the probe changed since the last converged pass, or ``floor`` has elapsed
    since it — always for a process that has converged none."""
    if last_full_pass_at is None or now - last_full_pass_at >= floor:
        return True
    return probe != last_probe


@domain_model
@dataclass(frozen=True)
class AnnotationMemoryChange:
    """One write to the remembered annotating set. A source entering is remembered before its first
    label write; a departed source is forgotten only once its clear finishes — the two halves are
    separate changes, made at the points the sweep writes them."""

    entered: tuple[str, ...] = ()
    left: tuple[str, ...] = ()

    @classmethod
    def on_entry(cls, remembered: Iterable[str], annotating: Iterable[str]) -> AnnotationMemoryChange:
        return cls(entered=tuple(sorted(set(annotating) - set(remembered))))

    @classmethod
    def on_departure(cls, leaving: Iterable[str], unfinished: Iterable[str]) -> AnnotationMemoryChange:
        return cls(left=tuple(sorted(set(leaving) - set(unfinished))))


class IReadAnnotatedSources(Protocol):
    """The store's read of the work sources this hub annotates."""

    def annotated_sources(self) -> frozenset[str]:
        """Every source this hub counts as annotated — its labels owed a clear on departure."""
        ...


class IWriteAnnotatedSources(IReadAnnotatedSources, Protocol):
    """The store's memory of the work sources this hub annotates, read and written."""

    def record_annotated_sources(self, *, entered: Sequence[str], left: Sequence[str], at: datetime) -> None:
        """Remember ``entered`` as annotated and forget ``left``, as of ``at``."""
        ...


@domain_model
@dataclass
class _Tally:
    written: int = 0
    cleared: int = 0
    failed: int = 0
    considered: int = 0


class AnnotationReconciler:
    """Per opted-in work source: desired-vs-actual marker diff, writes only the gap. Holds, in
    memory only, the probe and instant of its last converged pass; ``full_pass_floor`` bounds how
    stale a pass the probe skipped can leave the forge."""

    def __init__(
        self,
        *,
        work_refs: IReadChunkWorkRefsRepository,
        work_sources: IWorkSourceRegistry,
        memory: IWriteAnnotatedSources,
        clock: IClock,
        full_pass_floor: timedelta,
    ) -> None:
        self._work_refs = work_refs
        self._work_sources = work_sources
        self._memory = memory
        self._clock = clock
        self._full_pass_floor = full_pass_floor
        self._last_probe: AnnotationProbe | None = None
        self._last_full_pass_at: datetime | None = None

    def sweep(self) -> None:
        """One reconciliation pass over every opted-in source and the one-shot clear of each departed
        one — or a skip while the probe is unchanged and the floor not due. Failures are counted, not
        raised; only a converged pass records its probe, so anything less is retried next pass."""
        annotating = self._work_sources.annotating_names()
        probe = AnnotationProbe(work_refs=self._work_refs.live_work_refs_signature(), annotating=frozenset(annotating))
        now = self._clock.now()
        if not annotation_due(self._last_probe, probe, self._last_full_pass_at, now, self._full_pass_floor):
            _log.info("forge-status sweep skipped", reason="probe unchanged")
            return
        remembered = self._memory.annotated_sources()
        leaving = departed(remembered, annotating)
        if not annotating and not leaving:
            self._converged(probe, now)
            return
        self._remember(AnnotationMemoryChange.on_entry(remembered, annotating))
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
        self._remember(AnnotationMemoryChange.on_departure(leaving, unfinished))
        if not sources_skipped and not tally.failed and not unfinished:
            self._converged(probe, now)
        _log.info(
            "forge-status sweep completed",
            written=tally.written,
            cleared=tally.cleared,
            skipped=tally.considered - tally.written - tally.cleared - tally.failed,
            failed=tally.failed,
            sources_skipped=sources_skipped,
        )

    def _converged(self, probe: AnnotationProbe, now: datetime) -> None:
        self._last_probe = probe
        self._last_full_pass_at = now

    def _remember(self, change: AnnotationMemoryChange) -> None:
        if change.entered or change.left:
            self._memory.record_annotated_sources(entered=change.entered, left=change.left, at=self._clock.now())

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
