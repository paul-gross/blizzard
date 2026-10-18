"""SQLAlchemy adapter for the trace export seam (package-private).

Reads the cursor and its latch, and appends the cursor's fact rows (``bzh:facts-not-status``). The
closing-fact candidates and whole-chunk :class:`StepFacts` span every tenant, so they come from the
sweep corpus (:class:`IReadStepCorpus`); this store completes each chunk's facts in the chunk's own
tenant — its work refs rendered, the graphs, runner names and project they name read, and the tenant's
name beside them."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import datetime

from sqlalchemy import insert, select

from blizzard.foundation.event_log import HubEventKind
from blizzard.hub.domain.chunk.model import WorkRef, WorkRefLabel
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.observability.hub_events import IReadHubEventLog
from blizzard.hub.domain.observability.tracing.cursor import CursorKey
from blizzard.hub.domain.observability.tracing.facts import StepFacts
from blizzard.hub.domain.observability.tracing.repository import (
    ClosingCandidates,
    IReadProjectLabels,
    IReadStepCorpus,
    IReadTraceStatus,
    IWriteTraceCursor,
    ProjectLabel,
    StepHydration,
    TraceCheckpoint,
    TraceExportFailure,
)
from blizzard.hub.domain.projects.model import IReadProjectRepository, ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.tenancy import TenantClosed, TenantId, UnknownTenant

_FAILED: HubEventKind = "trace-export-failed"
_LATCH_KINDS: tuple[HubEventKind, ...] = (_FAILED, "trace-export-recovered")


class TraceStore:
    """The trace export's reads and its cursor's appends. The cursor is hub-level, and so are the export's failure and
    recovery events, read from ``hub_events``, the hub's own event log its sweep records them in. ``hydration`` opens a
    tenant's reads a chunk's facts are completed through, and ``tenant_name`` names an open tenant."""

    def __init__(
        self,
        store: HubStoreConnections,
        *,
        hub_events: IReadHubEventLog,
        corpus: IReadStepCorpus,
        hydration: Callable[[TenantId], StepHydration],
        tenant_name: Callable[[TenantId], str | None],
    ) -> None:
        self._store = store
        self._hub_events = hub_events
        self._corpus = corpus
        self._hydration = hydration
        self._tenant_name = tenant_name

    # --- candidates and hydration ---------------------------------------------------

    def closing_candidates(self, since: datetime, until: datetime, limit: int) -> ClosingCandidates:
        return self._corpus.closing_candidates(since, until, limit)

    def step_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, StepFacts]:
        """The corpus's step facts, each completed in its chunk's own tenant: its work refs rendered by that
        tenant's label, its work sources by their current slugs, the graphs, runner names and project its facts
        reference read through that tenant's reads, and the tenant's current name beside them. A chunk of a tenant
        closed since the corpus read is left out: a window or page holding it exports none of its rows, and the cursor
        still passes its closing facts."""
        opened = _Opened(self._hydration)
        corpus = self._corpus.step_facts_for(chunk_ids, opened.label)
        by_tenant: dict[TenantId, dict[str, StepFacts]] = {}
        for chunk_id, facts in corpus.items():
            if facts.tenant is not None:
                by_tenant.setdefault(facts.tenant, {})[chunk_id] = facts
        built: dict[str, StepFacts] = {}
        for tenant, tenant_facts in by_tenant.items():
            reads = opened.reads(tenant)
            if reads is None:
                continue
            graphs = reads.graphs.get_many(sorted({g for facts in tenant_facts.values() for g in _graph_ids(facts)}))
            names = reads.names.names_for(sorted({r for facts in tenant_facts.values() for r in _runner_ids(facts)}))
            projects = reads.projects.labels_for(sorted({f.project for f in tenant_facts.values() if f.project}))
            name = self._tenant_name(tenant)
            built.update(
                (chunk_id, _with_reads(facts, graphs, names, projects, name, reads.source_label))
                for chunk_id, facts in tenant_facts.items()
            )
        return {chunk_id: built[chunk_id] for chunk_id in corpus if chunk_id in built}

    # --- cursor and latch -----------------------------------------------------------

    def newest_cursor(self) -> TraceCheckpoint | None:
        return self._newest_cursor("newest_cursor")

    def newest_export_cursor(self) -> TraceCheckpoint | None:
        """The newest row that told spans — rows of zero are jumps and idle advances."""
        return self._newest_cursor("newest_export_cursor", s.trace_cursor.c.span_count > 0)

    def _newest_cursor(self, operation: str, *where) -> TraceCheckpoint | None:  # type: ignore[no-untyped-def]
        c = s.trace_cursor.c
        with self._store.read(operation) as conn:
            row = conn.execute(
                select(s.trace_cursor).where(*where).order_by(c.recorded_at.desc(), c.id.desc()).limit(1)
            ).first()
        if row is None:
            return None
        return TraceCheckpoint(
            CursorKey(row.position_at, row.chunk_id, row.epoch, row.decision_id), row.span_count, row.recorded_at
        )

    def newest_export_failure(self) -> TraceExportFailure | None:
        """The newest failure event — filtered in the query, so a later recovery never hides it."""
        event = self._hub_events.newest((_FAILED,))
        return TraceExportFailure(event.at, event.message) if event is not None else None

    def newest_export_latch(self) -> HubEventKind | None:
        """The newer of the two latch kinds' newest events."""
        event = self._hub_events.newest(_LATCH_KINDS)
        return next((k for k in _LATCH_KINDS if event is not None and k == event.kind), None)

    def append_cursor(self, record: TraceCheckpoint) -> None:
        position = record.position
        with self._store.write("append_cursor") as conn:
            conn.execute(
                insert(s.trace_cursor).values(
                    position_at=position.at,
                    chunk_id=position.chunk_id,
                    epoch=position.epoch,
                    decision_id=position.decision_id,
                    span_count=record.span_count,
                    recorded_at=record.recorded_at,
                )
            )


def _graph_ids(facts: StepFacts) -> set[str]:
    """Every graph a chunk's facts name — its pin and each movement fact's graphs."""
    ids = {facts.pin_graph_id} if facts.pin_graph_id is not None else set()
    ids |= {t.graph_id for t in facts.transitions}
    ids |= {m.from_graph_id for m in facts.migrations} | {m.to_graph_id for m in facts.migrations}
    ids |= {r.graph_id for r in facts.restarts}
    ids |= {r.from_graph_id for r in facts.restarts if r.from_graph_id is not None}
    return ids


def _runner_ids(facts: StepFacts) -> set[str]:
    """Every runner a chunk's facts name — each epoch's owner and each gate's imposing runner."""
    ids = {o.runner_id for o in facts.epoch_owners if o.runner_id is not None}
    return ids | {d.imposed_by_runner_id for d in facts.decisions if d.imposed_by_runner_id is not None}


def _with_reads(
    facts: StepFacts,
    graphs: dict[str, Graph],
    names: dict[str, str],
    projects: dict[str, ProjectLabel],
    tenant_name: str | None,
    source_label: Callable[[str], str],
) -> StepFacts:
    project = projects.get(facts.project) if facts.project is not None else None
    return replace(
        facts,
        work_sources=tuple(dict.fromkeys(source_label(source) for source in facts.work_sources)),
        graphs={g: graphs[g] for g in _graph_ids(facts) if g in graphs},
        runner_names={r: names[r] for r in _runner_ids(facts) if r in names},
        tenant_name=tenant_name,
        project_slug=project.slug if project is not None else None,
        project_name=project.name if project is not None else None,
    )


class ProjectLabels:
    """A tenant's projects as the labels its step facts are completed with."""

    def __init__(self, projects: IReadProjectRepository) -> None:
        self._projects = projects

    def labels_for(self, project_ids: Iterable[str]) -> dict[str, ProjectLabel]:
        found = self._projects.get_many([ProjectId(p) for p in project_ids])
        return {p.project_id: ProjectLabel(p.slug, p.name) for p in found.values()}


class _Opened:
    """One read's tenants' hydration reads, each opened once; ``None`` for a tenant closed since the corpus read."""

    def __init__(self, hydration: Callable[[TenantId], StepHydration]) -> None:
        self._hydration = hydration
        self._opened: dict[TenantId, StepHydration | None] = {}

    def reads(self, tenant: TenantId) -> StepHydration | None:
        if tenant not in self._opened:
            try:
                self._opened[tenant] = self._hydration(tenant)
            except (TenantClosed, UnknownTenant):
                self._opened[tenant] = None
        return self._opened[tenant]

    def label(self, tenant: TenantId) -> WorkRefLabel:
        reads = self.reads(tenant)
        return reads.label if reads is not None else _unrendered


def _unrendered(ref: WorkRef) -> str | None:
    """A closed tenant's label: renders nothing — its chunks are left out anyway."""
    return None


def _conforms_trace_store(x: TraceStore) -> tuple[IWriteTraceCursor, IReadTraceStatus]:
    return x, x


def _conforms_project_labels(x: ProjectLabels) -> IReadProjectLabels:
    return x
