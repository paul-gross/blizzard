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

from structlog.contextvars import bound_contextvars

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.event_log import EventLogKind
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.work_refs import (
    IReadWorkRefsCorpus,
    TenantWorkRef,
    WorkRefsSignature,
    live_work_ref_statuses,
)
from blizzard.hub.domain.kernel.tenant_fault import TenantFault
from blizzard.hub.tenancy import TenantClosed, TenantId, UnknownTenant
from blizzard.hub.work_sources.annotator import IWorkAnnotator, WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.source import IWorkSourceRegistry

_log = get_logger("blizzard.hub.forge_status")

MarkerAction = Literal["set", "clear", "skip"]

_SOURCE_SKIPPED: EventLogKind = "forge-status-source-skipped"

#: The full-pass floor, in sweep intervals — how long forge-side drift may outlive a skipped pass.
FULL_PASS_FLOOR_INTERVALS = 5


def marker_action(desired: WorkStatusMarker | None, actual: frozenset[WorkStatusMarker]) -> MarkerAction:
    """What a ref's labels need: cleared when the hub shows nothing for it but the forge carries a
    marker, set when the forge carries anything but exactly the desired marker, else nothing."""
    if desired is None:
        return "clear" if actual else "skip"
    return "skip" if actual == frozenset({desired}) else "set"


def departed(previous: Iterable[str], annotating: Iterable[str]) -> list[str]:
    """The sources remembered as annotated that are no longer annotating, in name order."""
    return sorted(set(previous) - set(annotating))


@domain_model
@dataclass(frozen=True)
class AnnotationProbe:
    """Everything that can move what the sweep writes for one tenant without a floor elapsing: the
    tenant's live work-ref inputs and the sources annotating now (read from its config on every
    call)."""

    work_refs: WorkRefsSignature
    annotating: frozenset[str]


#: A tenant holding none of the rows the work-refs probe counts.
_NO_ROWS = WorkRefsSignature(marks=())


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
    sources_skipped: list[str] = field(default_factory=list[str])


class TenantAnnotation:
    """One tenant's half of the forge-status pass, per opted-in work source of that tenant: the
    desired-vs-actual marker diff, writing only the gap, and the one-shot clear of each source that
    left its configuration. ``facts``, ``work_sources``, ``memory`` and ``events`` all serve that one tenant."""

    def __init__(
        self,
        *,
        facts: IReadChunkFactsRepository,
        work_sources: IWorkSourceRegistry,
        memory: IWriteAnnotatedSources,
        events: EventLogService,
        clock: IClock,
    ) -> None:
        self._facts = facts
        self._work_sources = work_sources
        self._memory = memory
        self._events = events
        self._clock = clock

    def annotating(self) -> Sequence[str]:
        """The tenant's sources annotating now, read from its configuration."""
        return self._work_sources.annotating_ids()

    def reconcile(
        self,
        tenant: TenantId,
        annotating: Sequence[str],
        rows: Callable[[], Sequence[TenantWorkRef]],
        tally: AnnotationTally,
        outages: set[str],
    ) -> bool:
        """Reconcile each of ``annotating`` — the tenant's sources annotating now — against ``rows``,
        ``tenant``'s live work refs, read only when the tenant has a source to reconcile, and clear
        each departed one, counting into ``tally``; whether the tenant converged. ``outages`` is the
        tenant's sources whose skip is already announced: a skip outside it is announced and joins it,
        and a source read again leaves it."""
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
            if annotator is None:  # pragma: no cover - annotating_ids() only names built ones
                continue
            if _reconcile(name, annotator, desired, tally) is None:
                self._skipped(name, outages, tally)
            else:
                outages.discard(name)
        unfinished: list[str] = []
        for name in leaving:
            clearer = self._work_sources.label_clearer(name)
            if clearer is None:
                _log.warning("forge-status source left config; its labels are not cleared", source=name)
                continue
            failed = _reconcile(name, clearer, {}, tally)
            if failed is None:
                self._skipped(name, outages, tally)
            else:
                outages.discard(name)
            if failed != 0:
                unfinished.append(name)
        self._remember(AnnotationMemoryChange.on_departure(leaving, unfinished))
        return len(tally.sources_skipped) == skipped_before and tally.failed == failed_before and not unfinished

    def _skipped(self, name: str, outages: set[str], tally: AnnotationTally) -> None:
        tally.sources_skipped.append(name)
        if name not in outages:
            self._announce_skipped(name)
            outages.add(name)

    def _announce_skipped(self, name: str) -> None:
        """Record the first skip of an outage of the source keyed ``name`` in this tenant's event log."""
        label = self._work_sources.source_label(name)
        self._events.record(
            kind=_SOURCE_SKIPPED,
            runner_id=None,
            chunk_id=None,
            project_id=None,
            lease_id=None,
            node_name=None,
            message=f"forge-status labels skipped for work source {label}: its forge could not be read",
            detail={"source": label, "work_source_id": name},
            at=self._clock.now(),
        )

    def _remember(self, change: AnnotationMemoryChange) -> None:
        if change.entered or change.left:
            self._memory.record_annotated_sources(entered=change.entered, left=change.left, at=self._clock.now())


class AnnotationReconciler:
    """The forge-status pass, one per hub. Holds, in memory only, each tenant's probe and last converged
    instant, so a tenant is due on its own state alone (``bzh:pass-isolates-tenants``);
    ``full_pass_floor`` bounds how stale a skipped pass can leave the forge. A tenant reconciles its own
    rows alone, against its own sources. It also holds each tenant's sources in a read outage, so a skip is
    announced once per outage, not once per pass; a restart forgets them and announces a standing outage again."""

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
        self._converged: dict[TenantId, tuple[AnnotationProbe, datetime]] = {}
        self._outages: dict[TenantId, set[str]] = {}

    def sweep(self) -> None:
        """One reconciliation pass over every due tenant (never converged, floor elapsed, or probe
        changed); the corpus is read once, only when some tenant is due. A closed tenant is skipped and
        a fault in one tenant's half is caught at that tenant; only a converged tenant records its
        probe, so any other is retried alone next pass."""
        opened: dict[TenantId, _Opened] = {}
        unconverged: list[TenantId] = []
        for tenant in sorted(self._tenants()):
            with bound_contextvars(tenant_id=tenant):
                try:
                    half = self._open(tenant)
                except (TenantClosed, UnknownTenant):
                    continue
                if isinstance(half, TenantFault):
                    unconverged.append(tenant)
                else:
                    opened[tenant] = half
        self._converged = {tenant: state for tenant, state in self._converged.items() if tenant in opened}
        known = {*opened, *unconverged}
        self._outages = {tenant: names for tenant, names in self._outages.items() if tenant in known}
        signatures = self._corpus.live_work_refs_signature()
        probes = {tenant: _probe(tenant, opened[tenant], signatures) for tenant in opened}
        now = self._clock.now()
        due = [tenant for tenant in opened if self._is_due(tenant, probes[tenant], now)]
        if not due:
            _log.info(
                "forge-status sweep skipped",
                reason="probe unchanged",
                tenants_skipped=len(opened),
                tenants_unconverged=unconverged,
            )
            return
        corpus = _PassCorpus(self._corpus)
        tally = AnnotationTally()
        reconciled = 0
        for tenant in due:
            with bound_contextvars(tenant_id=tenant):
                try:
                    converged = self._converge(tenant, opened[tenant], probes[tenant], now, corpus, tally)
                except (TenantClosed, UnknownTenant):
                    _log.info("forge-status sweep skipped a closed tenant")
                    continue
                if converged is True:
                    reconciled += 1
                else:
                    unconverged.append(tenant)
        _log.info(
            "forge-status sweep completed",
            written=tally.written,
            cleared=tally.cleared,
            skipped=tally.considered - tally.written - tally.cleared - tally.failed,
            failed=tally.failed,
            sources_skipped=tally.sources_skipped,
            tenants_reconciled=reconciled,
            tenants_skipped=len(opened) - len(due),
            tenants_unconverged=sorted(unconverged),
        )

    def sweep_tenant(self, tenant: TenantId) -> TenantFault | None:
        """:meth:`sweep` narrowed to ``tenant``: the same probe and corpus reads, ``tenant``'s share alone,
        and only ``tenant``'s convergence entry touched; a tenant not due is skipped. Its fault is
        returned; a closed tenant's refusal propagates."""
        with bound_contextvars(tenant_id=tenant):
            half = self._open(tenant)
            if isinstance(half, TenantFault):
                return half
            probe = _probe(tenant, half, self._corpus.live_work_refs_signature())
            now = self._clock.now()
            if not self._is_due(tenant, probe, now):
                _log.info("forge-status sweep skipped", reason="probe unchanged", tenants_skipped=1)
                return None
            tally = AnnotationTally()
            converged = self._converge(tenant, half, probe, now, _PassCorpus(self._corpus), tally)
            _log.info(
                "forge-status sweep completed",
                written=tally.written,
                cleared=tally.cleared,
                skipped=tally.considered - tally.written - tally.cleared - tally.failed,
                failed=tally.failed,
                sources_skipped=tally.sources_skipped,
                converged=converged is True,
            )
            return converged if isinstance(converged, TenantFault) else None

    def _open(self, tenant: TenantId) -> _Opened | TenantFault:
        """Open ``tenant``'s half and read its annotating sources; a closed tenant's refusal propagates."""
        try:
            half = self._tenant(tenant)
            return half, half.annotating()
        except (TenantClosed, UnknownTenant):
            raise
        except Exception as exc:
            _log.warning("forge-status sweep could not open a tenant", fault=repr(exc))
            return TenantFault.of(exc)

    def _converge(
        self,
        tenant: TenantId,
        opened: _Opened,
        probe: AnnotationProbe,
        now: datetime,
        corpus: _PassCorpus,
        tally: AnnotationTally,
    ) -> bool | TenantFault:
        """Reconcile ``tenant`` alone and record its probe when it converged; its fault, or whether it
        converged. A closed tenant's refusal propagates."""
        try:
            half, annotating = opened
            outages = self._outages.setdefault(tenant, set())
            converged = half.reconcile(tenant, annotating, partial(corpus.rows_of, tenant), tally, outages)
        except (TenantClosed, UnknownTenant):
            raise
        except Exception as exc:
            _log.warning("forge-status sweep failed for a tenant", fault=repr(exc))
            return TenantFault.of(exc)
        if converged:
            self._converged[tenant] = (probe, now)
        return converged

    def _is_due(self, tenant: TenantId, probe: AnnotationProbe, now: datetime) -> bool:
        last_probe, last_full_pass_at = self._converged.get(tenant, (None, None))
        return annotation_due(last_probe, probe, last_full_pass_at, now, self._full_pass_floor)


#: One tenant's opened half and the sources it annotates now.
_Opened = tuple[TenantAnnotation, Sequence[str]]


def _probe(tenant: TenantId, opened: _Opened, signatures: Mapping[TenantId, WorkRefsSignature]) -> AnnotationProbe:
    """``tenant``'s probe, from a pass's hub-wide signature read."""
    return AnnotationProbe(work_refs=signatures.get(tenant, _NO_ROWS), annotating=frozenset(opened[1]))


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
