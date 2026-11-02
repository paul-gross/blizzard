"""The statements and row mappers behind the trace and egress sweeps' corpus reads (package-private).

Every function here runs on a connection it is handed, marked hub-scoped, and never opens one.
Each statement reads across every tenant and leaves out every closed one by being built through
``OPEN_TENANTS`` (``src/blizzard/hub/store/internal/open_tenants.py``), and answers each row's ``tenant_id``
beside it. Chunk ids are hub-minted, so a filter on chunk ids needs no tenant beside it; a question or segment id is
runner-minted, so a statement matching one row to another by such an id matches the tenant too.
Hydration is a fixed set of batched statements per id batch, whatever the chunk count
(``bzh:bulk-reconstitution``)."""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy import Column, Connection, Row, Select, Table, and_, func, not_, or_

from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.chunk.model import UsageFact, WorkRef, WorkRefLabel
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.observability.analytics.events import (
    DerivationMarker,
    DropFact,
    SegmentProvenance,
    TranscriptEvent,
)
from blizzard.hub.domain.observability.egress.event_rows import EventDerivation
from blizzard.hub.domain.observability.egress.repository import EpochKey, EventsPosition, UsagePosition
from blizzard.hub.domain.observability.egress.rows import AttributedUsage
from blizzard.hub.domain.observability.tracing.facts import (
    StepFacts,
    TracedBounce,
    TracedChunkCompletion,
    TracedChunkStop,
    TracedDecision,
    TracedDecisionResolution,
    TracedDemotion,
    TracedEpochOwner,
    TracedEscalation,
    TracedHubExecSlot,
    TracedHubPoll,
    TracedHubTurn,
    TracedLease,
    TracedMigration,
    TracedPause,
    TracedPrerequisiteMet,
    TracedPromotion,
    TracedQuestion,
    TracedRequeue,
    TracedRestart,
    TracedRouteCreation,
    TracedRouteRelease,
    TracedTransition,
)
from blizzard.hub.domain.observability.tracing.repository import ClosingCandidates
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.fact_families import FactTable, Projection, entries, trace_order
from blizzard.hub.store.internal.open_tenants import OPEN_TENANTS
from blizzard.hub.tenancy import TenantId

TraceReader = Callable[[Connection, FactTable, Sequence[str]], dict[str, list[Any]]]


# --- closing candidates --------------------------------------------------------------


def closing_candidates(conn: Connection, since: datetime, until: datetime, limit: int) -> ClosingCandidates:
    """Every open tenant's chunks with a closing fact in ``[since, until]`` — a fact of a table the registry
    marks closing (``src/blizzard/hub/store/internal/fact_families.py``); a chunk's tenant is the one
    :func:`step_facts_for` tags its facts with."""
    chunk_ids: dict[str, None] = {}
    frontiers: list[datetime] = []
    newest: list[datetime] = []
    for entry in entries(Projection.CLOSING):
        assert entry.fact_time is not None
        rows, saturated = _closing_rows(conn, entry.table, entry.fact_time, entry.row_id, since, until, limit)
        chunk_ids.update((r.chunk_id, None) for r in rows)
        if rows:
            newest.append(rows[-1].at)
        if saturated:
            frontiers.append(rows[-1].at)
    return ClosingCandidates(tuple(chunk_ids), min(frontiers, default=None), max(newest, default=None))


def _closing_rows(
    conn: Connection, table: Table, at: Column[Any], row_id: Column[Any], since: datetime, until: datetime, limit: int
) -> tuple[list[Row[Any]], bool]:
    """One table's rows in ``(time, id)`` order. A full page that never left ``since`` pages on past
    it, so the frontier it sets is always later than ``since``."""
    columns = (table.c.tenant_id, table.c.chunk_id, at.label("at"), row_id.label("row_id"))
    window = OPEN_TENANTS.select(table, *columns).where(at <= until).order_by(at, row_id).limit(limit)
    page = conn.execute(window.where(at >= since)).all()
    rows = list(page)
    while len(page) == limit and rows[-1].at == since:
        last = rows[-1]
        page = conn.execute(window.where(at >= last.at, not_(and_(at == last.at, row_id <= last.row_id)))).all()
        rows += page
    return rows, len(page) == limit


# --- step facts ----------------------------------------------------------------------


def step_facts_for(conn: Connection, chunk_ids: Sequence[str]) -> dict[str, tuple[TenantId, StepFacts, list[WorkRef]]]:
    """Each existing chunk of an open tenant's :class:`StepFacts`, tagged with its tenant, beside that
    tenant and the work refs the chunk holds in ref order. ``graphs``, ``runner_names`` and the rendered
    work refs are left unread: a ref is rendered in its chunk's tenant (:func:`with_work_refs`), outside
    the read."""
    built: dict[str, tuple[TenantId, StepFacts, list[WorkRef]]] = {}
    for batch in id_batches(list(chunk_ids)):
        built.update(_hydrate(conn, batch))
    return built


def with_work_refs(facts: StepFacts, refs: Sequence[WorkRef], label: WorkRefLabel) -> StepFacts:
    """``facts`` with ``refs`` rendered through ``label`` — the chunk's tenant's — and the sources behind the
    refs it renders."""
    labelled = [(ref, label(ref)) for ref in refs]
    return replace(
        facts,
        work_refs=tuple(rendered for _, rendered in labelled if rendered is not None),
        work_sources=tuple(dict.fromkeys(ref.source for ref, rendered in labelled if rendered is not None)),
    )


def _by_chunk(conn: Connection, table: Table, batch: Sequence[str], *order: Column[Any]) -> dict[str, list[Row[Any]]]:
    grouped: dict[str, list[Row[Any]]] = defaultdict(list)
    stmt = OPEN_TENANTS.select(table).where(OPEN_TENANTS.within_chunks(table, table.c.chunk_id, batch)).order_by(*order)
    for row in conn.execute(stmt).all():
        grouped[row.chunk_id].append(row)
    return grouped


def _hydrate(conn: Connection, batch: Sequence[str]) -> dict[str, tuple[TenantId, StepFacts, list[WorkRef]]]:
    chunk_stmt = OPEN_TENANTS.select(s.chunks).where(s.chunks.c.chunk_id.in_(batch))
    chunk_rows = {r.chunk_id: r for r in conn.execute(chunk_stmt)}
    if not chunk_rows:
        return {}
    ids = list(chunk_rows)
    read: dict[str, dict[str, list[Any]]] = {}
    for entry in entries(Projection.TRACE):
        field, reader = TRACE_READERS[entry.table]
        read[field] = reader(conn, entry, ids)
    work_refs = read.pop("work_refs")

    def fields_of(chunk_id: str) -> dict[str, Any]:
        return {field: tuple(by_chunk.get(chunk_id, ())) for field, by_chunk in read.items()}

    return {
        chunk_id: (
            TenantId(chunk.tenant_id),
            StepFacts(
                chunk_id=chunk_id,
                tenant=TenantId(chunk.tenant_id),
                project=chunk.project_id,
                pin_graph_id=chunk.graph_id,
                minted_at=chunk.minted_at,
                **fields_of(chunk_id),
            ),
            list(work_refs.get(chunk_id, ())),
        )
        for chunk_id, chunk in chunk_rows.items()
    }


def _mapped(field: str, mapper: Callable[[Row[Any]], Any]) -> tuple[str, TraceReader]:
    """A trace table read once for the batch and mapped row by row, ordered as the registry says."""

    def read(conn: Connection, entry: FactTable, ids: Sequence[str]) -> dict[str, list[Any]]:
        rows = _by_chunk(conn, entry.table, ids, trace_order(entry))
        return {chunk_id: [mapper(r) for r in group] for chunk_id, group in rows.items()}

    return field, read


def _bespoke(field: str, read: Callable[[Connection, Sequence[str]], dict[str, list[Any]]]) -> tuple[str, TraceReader]:
    """A trace table whose read joins or reaches the chunk through another table."""
    return field, lambda conn, _entry, ids: read(conn, ids)


#: Each trace-carried table's :class:`StepFacts` field and the read that fills it. ``chunk_work_refs`` fills the refs
#: beside the facts rather than a field. The keys equal the registry's trace narrowing
#: (``tests/test_fact_families.py``).
TRACE_READERS: dict[Table, tuple[str, TraceReader]] = {
    s.chunk_work_refs: _mapped("work_refs", lambda r: WorkRef(source=r.source, ref=r.ref)),
    s.lease_facts: _mapped("lease_facts", lambda r: TracedLease(r.epoch, r.minted_at)),
    s.epoch_owners: _mapped("epoch_owners", lambda r: TracedEpochOwner(r.epoch, r.runner_id, r.recorded_at)),
    s.transitions: _mapped("transitions", lambda r: _transition(r)),
    s.chunk_migrations: _mapped("migrations", lambda r: _migration(r)),
    s.chunk_restarts: _mapped("restarts", lambda r: _restart(r)),
    s.escalations: _mapped("escalations", lambda r: TracedEscalation(r.epoch, r.recorded_at, r.decision_id)),
    s.decisions: _mapped(
        "decisions",
        lambda r: TracedDecision(r.decision_id, r.node_id, r.epoch, r.submitted_at, r.imposed_by_runner_id),
    ),
    s.decision_resolutions: _bespoke("decision_resolutions", lambda conn, ids: _resolutions(conn, ids)),
    s.requeues: _mapped("requeues", lambda r: TracedRequeue(r.requeued_at)),
    s.route_released: _mapped("route_released", lambda r: TracedRouteRelease(r.released_at)),
    s.chunk_stopped: _mapped("chunk_stopped", lambda r: TracedChunkStop(r.stopped_at)),
    s.chunk_completed: _mapped("chunk_completed", lambda r: TracedChunkCompletion(r.completed_at)),
    s.questions: _bespoke("questions", lambda conn, ids: _questions(conn, ids)),
    s.chunk_pause_facts: _mapped("pauses", lambda r: TracedPause(str(r.id), r.paused, r.set_at)),
    s.flow_submissions: _bespoke("hub_turns", lambda conn, ids: _turns(conn, ids)),
    s.hub_exec_slot: _bespoke("hub_exec_slots", lambda conn, ids: _slots(conn, ids)),
    s.hub_node_poll: _mapped("hub_polls", lambda r: TracedHubPoll(str(r.id), r.node_id, r.epoch, r.polled_at)),
    s.chunk_bounces: _mapped("bounces", lambda r: TracedBounce(r.epoch, r.cause, r.recorded_at)),
    s.route_created: _mapped("routes_created", lambda r: TracedRouteCreation(r.created_at)),
    s.chunk_promoted: _mapped("promotions", lambda r: TracedPromotion(r.promoted_at)),
    s.chunk_demoted: _mapped("demotions", lambda r: TracedDemotion(r.demoted_at)),
    s.chunk_dependencies: _bespoke("prerequisites_met", lambda conn, ids: _prerequisites_met(conn, ids)),
    s.usage_facts: _mapped("usage", lambda r: _usage(r)),
}


def _resolutions(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedDecisionResolution]]:
    decided = OPEN_TENANTS.join(
        s.decision_resolutions, s.decisions, s.decisions.c.decision_id == s.decision_resolutions.c.decision_id
    )
    stmt = OPEN_TENANTS.select(decided, s.decisions.c.chunk_id, s.decision_resolutions).where(
        OPEN_TENANTS.within_chunks(s.decisions, s.decisions.c.chunk_id, ids)
    )
    grouped: dict[str, list[TracedDecisionResolution]] = defaultdict(list)
    for r in conn.execute(stmt).all():
        grouped[r.chunk_id].append(TracedDecisionResolution(r.decision_id, r.resolved_at, r.choice))
    return grouped


def _questions(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedQuestion]]:
    question, answer = s.questions, s.question_answers
    answered = OPEN_TENANTS.join(
        question,
        answer,
        and_(answer.c.question_id == question.c.question_id, answer.c.tenant_id == question.c.tenant_id),
        isouter=True,
    )
    stmt = (
        OPEN_TENANTS.select(answered, question, answer.c.answered_at)
        .where(OPEN_TENANTS.within_chunks(question, question.c.chunk_id, ids))
        .order_by(question.c.asked_at)
    )
    grouped: dict[str, list[TracedQuestion]] = defaultdict(list)
    for r in conn.execute(stmt).all():
        grouped[r.chunk_id].append(TracedQuestion(r.question_id, r.epoch, r.asked_at, r.answered_at))
    return grouped


def _turns(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedHubTurn]]:
    """Each chunk's turns, read through its submissions so both reads seek a tenant-led index."""
    submission, turn = s.flow_submissions.c, s.hub_turns.c
    visits = {
        r.submission_id: (r.chunk_id, r.node_id)
        for r in conn.execute(
            OPEN_TENANTS.select(
                s.flow_submissions, submission.submission_id, submission.chunk_id, submission.node_id
            ).where(OPEN_TENANTS.within_chunks(s.flow_submissions, submission.chunk_id, ids))
        ).all()
    }
    grouped: dict[str, list[TracedHubTurn]] = defaultdict(list)
    chunk_tenants = OPEN_TENANTS.select(s.chunks, s.chunks.c.tenant_id).where(s.chunks.c.chunk_id.in_(ids))
    for batch in id_batches(sorted(visits)):
        rows = conn.execute(
            OPEN_TENANTS.select(s.hub_turns)
            .where(turn.tenant_id.in_(chunk_tenants), turn.submission_id.in_(batch))
            .order_by(turn.started_at)
        ).all()
        for r in rows:
            chunk_id, node_id = visits[r.submission_id]
            grouped[chunk_id].append(TracedHubTurn(r.turn_id, node_id, r.started_at, r.ended_at))
    return grouped


def _slots(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedHubExecSlot]]:
    slot = s.hub_exec_slot
    stmt = OPEN_TENANTS.select(slot).where(slot.c.holder_chunk_id.in_(ids)).order_by(slot.c.acquired_at)
    grouped: dict[str, list[TracedHubExecSlot]] = defaultdict(list)
    for r in conn.execute(stmt).all():
        grouped[r.holder_chunk_id].append(TracedHubExecSlot(r.slot_id, r.node_id, r.acquired_at, r.released_at))
    return grouped


def _prerequisites_met(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedPrerequisiteMet]]:
    """When each of a chunk's prerequisites first derived ``done``: its first transition into the
    terminal, or its first operator completion, whichever came first."""
    edge = s.chunk_dependencies.c
    edges = conn.execute(
        OPEN_TENANTS.select(s.chunk_dependencies, edge.dependent_chunk_id, edge.prerequisite_chunk_id).where(
            OPEN_TENANTS.within_chunks(s.chunk_dependencies, edge.dependent_chunk_id, ids)
        )
    ).all()
    prerequisites = sorted({e.prerequisite_chunk_id for e in edges})
    done_at: dict[str, datetime] = {}
    transition, completion = s.transitions.c, s.chunk_completed.c
    for batch in id_batches(prerequisites):
        terminal = OPEN_TENANTS.select(
            s.transitions, transition.chunk_id, func.min(transition.recorded_at).label("at")
        ).where(
            OPEN_TENANTS.within_chunks(s.transitions, transition.chunk_id, batch),
            transition.to_node_id == RESERVED_TERMINAL,
        )
        completed = OPEN_TENANTS.select(
            s.chunk_completed, completion.chunk_id, func.min(completion.completed_at).label("at")
        ).where(OPEN_TENANTS.within_chunks(s.chunk_completed, completion.chunk_id, batch))
        for stmt in (terminal.group_by(transition.chunk_id), completed.group_by(completion.chunk_id)):
            for r in conn.execute(stmt).all():
                done_at[r.chunk_id] = min(r.at, done_at.get(r.chunk_id, r.at))
    grouped: dict[str, list[TracedPrerequisiteMet]] = defaultdict(list)
    for e in edges:
        if e.prerequisite_chunk_id in done_at:
            grouped[e.dependent_chunk_id].append(TracedPrerequisiteMet(done_at[e.prerequisite_chunk_id]))
    return grouped


def _transition(r: Row[Any]) -> TracedTransition:
    return TracedTransition(
        epoch=r.epoch,
        recorded_at=r.recorded_at,
        graph_id=r.graph_id,
        to_node_id=r.to_node_id,
        from_node_id=r.from_node_id,
        decision_id=r.decision_id,
        choice_name=r.choice_name,
    )


def _migration(r: Row[Any]) -> TracedMigration:
    return TracedMigration(
        epoch=r.epoch,
        recorded_at=r.recorded_at,
        from_graph_id=r.from_graph_id,
        to_graph_id=r.to_graph_id,
        from_node_id=r.from_node_id,
        landed_node_id=r.landed_node_id,
        # Null on a row predating the discriminator — read as unrecorded, never guessed at.
        source=MigrationSource(r.source) if r.source else None,
        decision_id=r.decision_id,
        choice_name=r.choice_name,
    )


def _restart(r: Row[Any]) -> TracedRestart:
    return TracedRestart(
        epoch=r.epoch,
        recorded_at=r.recorded_at,
        graph_id=r.graph_id,
        to_node_id=r.to_node_id,
        from_graph_id=r.from_graph_id,
        from_node_id=r.from_node_id,
        decision_id=r.decision_id,
    )


def _usage(u: Row[Any]) -> UsageFact:
    return UsageFact(
        node_id=u.node_id,
        epoch=u.epoch,
        kind=u.kind,
        model=u.model,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=u.cache_read_tokens,
        cache_create_tokens=u.cache_create_tokens,
        cost_usd=u.cost_usd,
        recorded_at=u.recorded_at,
        harness_id=u.harness_id,
        harness_version=u.harness_version,
        estimated_cost_usd=u.estimated_cost_usd,
    )


# --- egress usage --------------------------------------------------------------------


def usage_after(conn: Connection, position: UsagePosition, until: datetime, limit: int) -> list[AttributedUsage]:
    """Up to ``limit`` of every open tenant's usage facts past ``position``, in position order."""
    u = s.usage_facts.c
    past = or_(
        u.recorded_at > position.recorded_at, and_(u.recorded_at == position.recorded_at, u.id > position.usage_id)
    )
    stmt = (
        OPEN_TENANTS.select(s.usage_facts)
        .where(past, u.recorded_at <= until)
        .order_by(u.recorded_at, u.id)
        .limit(limit)
    )
    return [
        AttributedUsage(r.id, r.chunk_id, r.runner_id, _usage(r), tenant=TenantId(r.tenant_id))
        for r in conn.execute(stmt).all()
    ]


# --- egress events -------------------------------------------------------------------

_d = s.transcript_event_derivations.c
_e = s.transcript_events.c
_x = s.transcript_event_drops.c
_g = s.transcript_segments.c
_l = s.lease_facts.c


def markers_after(
    conn: Connection, position: EventsPosition, until: datetime, limit: int, extractor_version: str | None
) -> list[DerivationMarker]:
    """Up to ``limit`` of every open tenant's markers past ``position``, in the events order: ``derived_at``,
    segment, extractor version, tenant."""
    later = or_(
        _d.derived_at > position.at,
        and_(
            _d.derived_at == position.at,
            or_(
                _d.segment_id > position.segment_id,
                and_(_d.segment_id == position.segment_id, _d.extractor_version > position.extractor_version),
            ),
        ),
    )
    past = later
    if position.tenant is not None:
        beside = and_(
            _d.derived_at == position.at,
            _d.segment_id == position.segment_id,
            _d.extractor_version == position.extractor_version,
            _d.tenant_id > position.tenant,
        )
        past = or_(later, beside)
    stmt = OPEN_TENANTS.select(s.transcript_event_derivations).where(past, _d.derived_at <= until)
    if extractor_version is not None:
        stmt = stmt.where(_d.extractor_version == extractor_version)
    order = (_d.derived_at, _d.segment_id, _d.extractor_version, _d.tenant_id)
    rows = conn.execute(stmt.order_by(*order).limit(limit)).all()
    return [_marker(row) for row in rows]


def drops_after(conn: Connection, position: EventsPosition, until: datetime, limit: int) -> list[DropFact]:
    """Up to ``limit`` of every open tenant's drop facts past ``position``, in the events order: ``dropped_at``,
    segment, then tenant — a drop's version is empty, so it is beside a position only when the position's is."""
    past = or_(_x.dropped_at > position.at, and_(_x.dropped_at == position.at, _x.segment_id > position.segment_id))
    if position.tenant is not None and not position.extractor_version:
        beside = and_(
            _x.dropped_at == position.at, _x.segment_id == position.segment_id, _x.tenant_id > position.tenant
        )
        past = or_(past, beside)
    stmt = (
        OPEN_TENANTS.select(s.transcript_event_drops)
        .where(past, _x.dropped_at <= until)
        .order_by(_x.dropped_at, _x.segment_id, _x.tenant_id, _x.id)
        .limit(limit)
    )
    return [_drop(row) for row in conn.execute(stmt).all()]


def derivations(conn: Connection, markers: Sequence[DerivationMarker]) -> list[EventDerivation]:
    """Each marker's whole derivation, read in the marker's tenant, in ``markers``' order; a marker whose
    stored ``derived_at`` moved once its events are loaded is omitted."""
    result: list[EventDerivation] = []
    for batch in id_batches(list(markers)):
        segment_ids = sorted({marker.segment_id for marker in batch})
        versions = sorted({marker.extractor_version for marker in batch})
        events: dict[tuple[str | None, str, str], list[Row[Any]]] = defaultdict(list)
        for row in conn.execute(_events_stmt(segment_ids, versions)):
            events[(row.tenant_id, row.segment_id, row.extractor_version)].append(row)
        records = {
            key: list(group)
            for key, group in itertools.groupby(
                conn.execute(_segment_contexts_stmt(segment_ids)).all(), key=lambda row: (row.tenant_id, row.segment_id)
            )
        }
        # Read after the events: a re-derive or drop committed since the marker was selected shows here.
        standing = {
            (row.tenant_id, row.segment_id, row.extractor_version): row.derived_at
            for row in conn.execute(_marker_times_stmt(segment_ids))
        }
        for marker in batch:
            key = (marker.tenant, marker.segment_id, marker.extractor_version)
            segment = records.get((marker.tenant, marker.segment_id))
            if standing.get(key) != marker.derived_at or not segment:
                continue
            held = events.get(key, [])
            first = segment[0]
            result.append(
                EventDerivation(
                    marker=marker,
                    provenance=_provenance(held, segment),
                    events=tuple(_event(row) for row in held),
                    chunk_id=first.chunk_id,
                    epoch=first.epoch,
                    spawn_generation=first.spawn_generation,
                    spawn_cwd=first.spawn_cwd,
                )
            )
    return result


def _events_stmt(segment_ids: Sequence[str], versions: Sequence[str]) -> Select[Any]:
    return (
        OPEN_TENANTS.select(s.transcript_events)
        .where(_e.segment_id.in_(segment_ids), _e.extractor_version.in_(versions))
        .order_by(_e.tenant_id, _e.segment_id, _e.extractor_version, _e.id)
    )


def _segment_contexts_stmt(segment_ids: Sequence[str]) -> Select[Any]:
    """The columns a derivation's identity needs, never ``content``; ordered so a segment's first record leads."""
    return (
        OPEN_TENANTS.select(
            s.transcript_segments,
            _g.tenant_id,
            _g.segment_id,
            _g.chunk_id,
            _g.epoch,
            _g.spawn_generation,
            _g.spawn_cwd,
            _g.harness_id,
            _g.harness_version,
            _g.model,
            _g.effort,
        )
        .where(_g.segment_id.in_(segment_ids))
        .order_by(_g.tenant_id, _g.segment_id, _g.turn_range_start)
    )


def _marker_times_stmt(segment_ids: Sequence[str]) -> Select[Any]:
    return OPEN_TENANTS.select(
        s.transcript_event_derivations, _d.tenant_id, _d.segment_id, _d.extractor_version, _d.derived_at
    ).where(_d.segment_id.in_(segment_ids))


def epochs_minted_between(
    conn: Connection, since: datetime, until: datetime, after: EpochKey | None, limit: int
) -> list[EpochKey]:
    stmt = OPEN_TENANTS.select(s.lease_facts, _l.tenant_id, _l.chunk_id, _l.epoch).where(
        _l.minted_at >= since, _l.minted_at < until
    )
    if after is not None:
        stmt = stmt.where(
            or_(_l.chunk_id > after.chunk_id, and_(_l.chunk_id == after.chunk_id, _l.epoch > after.epoch))
        )
    rows = conn.execute(stmt.distinct().order_by(_l.chunk_id, _l.epoch).limit(limit)).all()
    return [EpochKey(row.chunk_id, row.epoch, tenant=TenantId(row.tenant_id)) for row in rows]


def epoch_markers(
    conn: Connection, epochs: Sequence[EpochKey], extractor_version: str | None
) -> list[DerivationMarker]:
    wanted = set(epochs)
    markers: dict[tuple[str, str, str], DerivationMarker] = {}
    for batch in id_batches(sorted({epoch.chunk_id for epoch in epochs})):
        for row in conn.execute(_epoch_markers_stmt(batch, extractor_version)):
            if EpochKey(row.chunk_id, row.epoch) in wanted:
                markers[(row.segment_id, row.extractor_version, row.tenant_id)] = _marker(row)
    return [markers[key] for key in sorted(markers)]


def _epoch_markers_stmt(chunk_ids: Sequence[str], extractor_version: str | None) -> Select[Any]:
    segment = s.transcript_segments
    marked = OPEN_TENANTS.join(
        s.transcript_event_derivations,
        segment,
        and_(_g.segment_id == _d.segment_id, _g.tenant_id == _d.tenant_id),
    )
    stmt = OPEN_TENANTS.select(marked, s.transcript_event_derivations, _g.chunk_id, _g.epoch).where(
        OPEN_TENANTS.within_chunks(segment, _g.chunk_id, chunk_ids)
    )
    if extractor_version is not None:
        stmt = stmt.where(_d.extractor_version == extractor_version)
    return stmt.distinct().order_by(_d.segment_id, _d.extractor_version)


def epoch_drops(conn: Connection, epochs: Sequence[EpochKey]) -> list[DropFact]:
    wanted = set(epochs)
    drops: list[DropFact] = []
    for batch in id_batches(sorted({epoch.chunk_id for epoch in epochs})):
        stmt = (
            OPEN_TENANTS.select(s.transcript_event_drops)
            .where(OPEN_TENANTS.within_chunks(s.transcript_event_drops, _x.chunk_id, batch))
            .order_by(_x.dropped_at, _x.segment_id, _x.id)
        )
        drops.extend(_drop(row) for row in conn.execute(stmt) if EpochKey(row.chunk_id, row.epoch) in wanted)
    return sorted(drops, key=lambda drop: (drop.dropped_at, drop.segment_id))


def _marker(row: Row[Any]) -> DerivationMarker:
    return DerivationMarker(
        segment_id=row.segment_id,
        extractor_version=row.extractor_version,
        content_fingerprint=row.content_fingerprint,
        derived_at=row.derived_at,
        event_count=row.event_count,
        complete=row.complete,
        tenant=TenantId(row.tenant_id),
    )


def _drop(row: Row[Any]) -> DropFact:
    return DropFact(
        segment_id=row.segment_id,
        chunk_id=row.chunk_id,
        epoch=row.epoch,
        spawn_generation=row.spawn_generation,
        dropped_at=row.dropped_at,
        tenant=TenantId(row.tenant_id),
    )


def _event(row: Row[Any]) -> TranscriptEvent:
    return TranscriptEvent(
        kind=row.kind,
        turn_path=row.turn_path,
        occurrence=row.occurrence,
        payload=row.payload,
        subject=row.subject,
        tool=row.tool,
        chunk_id=row.chunk_id,
        node_id=row.node_id,
        epoch=row.epoch,
        spawn_generation=row.spawn_generation,
        graph_id=row.graph_id,
        depth=row.depth,
        agent_type=row.agent_type,
        occurred_at=row.occurred_at,
    )


def _provenance(events: Sequence[Row[Any]], records: Sequence[Row[Any]]) -> SegmentProvenance:
    """As derived: stamped on the events when there are any; otherwise the segment's own,
    as ``transcript_event_store._provenance`` derives it."""
    if events:
        first = events[0]
        return SegmentProvenance(first.harness_id, first.harness_version, first.model, first.effort)
    first = records[0]
    versions = [record.harness_version for record in records if record.harness_version is not None]
    return SegmentProvenance(first.harness_id, versions[-1] if versions else None, first.model, first.effort)
