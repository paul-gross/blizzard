"""The forge-status projection — a periodic, best-effort sweep, gated by an in-memory probe.

The hub is truth; the forge carries a one-way reflection as labels, writing only diffs against the
forge's discovered markers. The store remembers which sources the sweep annotates, so a departing
source has its labels cleared once; a fresh process runs full, and the floor bounds forge-side drift."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
from typing import Literal, Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.work_refs import (
    IReadWorkRefsCorpus,
    TenantWorkRef,
    WorkRefsSignature,
    live_work_ref_statuses,
)
from blizzard.hub.tenancy import TenantClosed, TenantId, UnknownTenant
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
    work-ref inputs and the sources annotating now, each beside its tenant (read from each tenant's
    config on every call)."""

    work_refs: WorkRefsSignature
    annotating: frozenset[tuple[TenantId, str]]


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
class AnnotationTally:
    """One forge-status pass's counts, across every tenant it acted in."""

    written: int = 0
    cleared: int = 0
    failed: int = 0
    considered: int = 0
    sources_skipped: list[str] = field(default_factory=list)


class TenantAnnotation:
    """One tenant's half of the forge-status pass, per opted-in work source of that tenant: the
    desired-vs-actual marker diff, writing only the gap, and the one-shot clear of each source that
    left its configuration. ``facts``, ``work_sources`` and ``memory`` all serve that one tenant."""

    def __init__(
        self,
        *,
        facts: IReadChunkFactsRepository,
        work_sources: IWorkSourceRegistry,
        memory: IWriteAnnotatedSources,
        clock: IClock,
    ) -> None:
        self._facts = facts
        self._work_sources = work_sources
        self._memory = memory
        self._clock = clock

    def annotating(self) -> Sequence[str]:
        """The tenant's sources annotating now, read from its configuration."""
        return self._work_sources.annotating_names()

    def reconcile(
        self,
        tenant: TenantId,
        annotating: Sequence[str],
        rows: Callable[[], Sequence[TenantWorkRef]],
        tally: AnnotationTally,
    ) -> bool:
        """Reconcile each of ``annotating`` — the tenant's sources annotating now — against ``rows``,
        ``tenant``'s live work refs, read only when the tenant has a source to reconcile, and clear
        each departed one, counting into ``tally``; whether the tenant converged."""
        remembered = self._memory.annotated_sources()
        leaving = departed(remembered, annotating)
        if not annotating and not leaving:
            return True
        self._remember(AnnotationMemoryChange.on_entry(remembered, annotating))
        desired = live_work_ref_statuses(rows(), {tenant: self._facts.load_live_statuses()})
        skipped_before = len(tally.sources_skipped)
        failed_before = tally.failed
        for name in annotating:
            annotator = self._work_sources.annotator(name)
            if annotator is None:  # pragma: no cover - annotating_names() only names built ones
                continue
            if _reconcile(name, annotator, desired, tally) is None:
                tally.sources_skipped.append(name)
        unfinished: list[str] = []
        for name in leaving:
            clearer = self._work_sources.label_clearer(name)
            if clearer is None:
                _log.warning("forge-status source left config; its labels are not cleared", source=name)
                continue
            failed = _reconcile(name, clearer, {}, tally)
            if failed is None:
                tally.sources_skipped.append(name)
            if failed != 0:
                unfinished.append(name)
        self._remember(AnnotationMemoryChange.on_departure(leaving, unfinished))
        return len(tally.sources_skipped) == skipped_before and tally.failed == failed_before and not unfinished

    def _remember(self, change: AnnotationMemoryChange) -> None:
        if change.entered or change.left:
            self._memory.record_annotated_sources(entered=change.entered, left=change.left, at=self._clock.now())


class AnnotationReconciler:
    """The forge-status pass, one per hub. Holds, in memory only, the probe and instant of its last
    converged pass; ``full_pass_floor`` bounds how stale a pass the probe skipped can leave the
    forge. ``tenants`` names the open tenants, ``tenant`` opens one's :class:`TenantAnnotation`, and
    ``corpus`` names every open tenant's work refs, each beside its tenant — a tenant reconciles its
    own rows alone, against its own sources."""

    def __init__(
        self,
        *,
        corpus: IReadWorkRefsCorpus,
        tenants: Callable[[], Iterable[TenantId]],
        tenant: Callable[[TenantId], TenantAnnotation],
        clock: IClock,
        full_pass_floor: timedelta,
    ) -> None:
        self._corpus = corpus
        self._tenants = tenants
        self._tenant = tenant
        self._clock = clock
        self._full_pass_floor = full_pass_floor
        self._last_probe: AnnotationProbe | None = None
        self._last_full_pass_at: datetime | None = None

    def sweep(self) -> None:
        """One reconciliation pass over every open tenant's opted-in sources and the one-shot clear
        of each departed one — or a skip while the hub-wide probe is unchanged and the floor not due.
        The corpus is read once, only when some tenant has a source to reconcile. Failures are
        counted, not raised; a tenant closed since the pass began is skipped; only a converged pass
        records its probe, so anything less is retried next pass."""
        opened: dict[TenantId, TenantAnnotation] = {}
        for tenant in sorted(self._tenants()):
            try:
                opened[tenant] = self._tenant(tenant)
            except (TenantClosed, UnknownTenant):
                continue
        annotating = {tenant: unit.annotating() for tenant, unit in opened.items()}
        probe = AnnotationProbe(
            work_refs=self._corpus.live_work_refs_signature(),
            annotating=frozenset((tenant, name) for tenant, names in annotating.items() for name in names),
        )
        now = self._clock.now()
        if not annotation_due(self._last_probe, probe, self._last_full_pass_at, now, self._full_pass_floor):
            _log.info("forge-status sweep skipped", reason="probe unchanged")
            return
        corpus = _PassCorpus(self._corpus)
        tally = AnnotationTally()
        converged = True
        for tenant, unit in opened.items():
            try:
                converged &= unit.reconcile(tenant, annotating[tenant], partial(corpus.rows_of, tenant), tally)
            except (TenantClosed, UnknownTenant):
                _log.info("forge-status sweep skipped a closed tenant", tenant=tenant)
        if converged:
            self._last_probe = probe
            self._last_full_pass_at = now
        _log.info(
            "forge-status sweep completed",
            written=tally.written,
            cleared=tally.cleared,
            skipped=tally.considered - tally.written - tally.cleared - tally.failed,
            failed=tally.failed,
            sources_skipped=tally.sources_skipped,
        )


class _PassCorpus:
    """One pass's read of the corpus, made once, on first use, and handed out per tenant."""

    def __init__(self, corpus: IReadWorkRefsCorpus) -> None:
        self._corpus = corpus
        self._by_tenant: dict[TenantId, list[TenantWorkRef]] | None = None

    def rows_of(self, tenant: TenantId) -> list[TenantWorkRef]:
        if self._by_tenant is None:
            self._by_tenant = {}
            for row in self._corpus.live_work_refs():
                self._by_tenant.setdefault(row.tenant, []).append(row)
        return self._by_tenant.get(tenant, [])


def _reconcile(
    name: str, annotator: IWorkAnnotator, desired: Mapping[WorkRef, ChunkStatus], tally: AnnotationTally
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
