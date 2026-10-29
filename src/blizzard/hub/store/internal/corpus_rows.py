"""The statements and row mappers behind the trace and egress sweeps' corpus reads (package-private).

Every function here runs on a connection it is handed, marked hub-scoped, and never opens one.
Each statement reads across every tenant and leaves out every closed one through
:func:`open_tenant`, the one closed-tenant filter every corpus read applies, and answers each
row's ``tenant_id`` beside it. Chunk ids are
hub-minted, so a filter on chunk ids needs no tenant beside it; a question or segment id is
runner-minted, so a statement matching one row to another by such an id matches the tenant too.
Hydration is a fixed set of batched statements per id batch, whatever the chunk count
(``bzh:bulk-reconstitution``)."""

from __future__ import annotations

import itertools
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy import Column, Connection, Row, Select, Table, and_, func, not_, or_, select
from sqlalchemy.sql import ColumnElement

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
from blizzard.hub.tenancy import TenantId


def open_tenant(table: Table) -> ColumnElement[bool]:
    """``table``'s rows that belong to an open tenant — one with no ``tenant_deletions`` row, whether
    a closed tenant's drain is still running or done. A row naming no tenant belongs to none, so it
    is left out whatever ``tenant_deletions`` holds: ``NOT IN`` alone would admit it while that
    table is empty."""
    tenant = table.c.tenant_id
    return and_(tenant.is_not(None), tenant.not_in(select(s.tenant_deletions.c.tenant_id)))


def of_chunks(table: Table, chunk_id: Column[Any], chunk_ids: Sequence[str]) -> ColumnElement[bool]:
    """``table``'s open-tenant rows naming one of ``chunk_ids`` by ``chunk_id``, qualified by those chunks' own
    tenants: a chunk's rows share its tenant, and naming it lets the read seek the table's tenant-led chunk index
    rather than walk the table."""
    chunk_tenants = select(s.chunks.c.tenant_id).where(s.chunks.c.chunk_id.in_(chunk_ids))
    return and_(open_tenant(table), table.c.tenant_id.in_(chunk_tenants), chunk_id.in_(chunk_ids))


# --- closing candidates --------------------------------------------------------------

#: Closing-fact tables, (time, id)-indexed (blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md §Closing).
_CLOSING_TABLES: tuple[tuple[Table, Column[Any], Column[Any]], ...] = (
    (s.transitions, s.transitions.c.recorded_at, s.transitions.c.transition_id),
    (s.decisions, s.decisions.c.submitted_at, s.decisions.c.decision_id),
    (s.chunk_migrations, s.chunk_migrations.c.recorded_at, s.chunk_migrations.c.migration_id),
    (s.escalations, s.escalations.c.recorded_at, s.escalations.c.id),
    (s.chunk_restarts, s.chunk_restarts.c.recorded_at, s.chunk_restarts.c.id),
    (s.route_released, s.route_released.c.released_at, s.route_released.c.id),
    (s.chunk_stopped, s.chunk_stopped.c.stopped_at, s.chunk_stopped.c.id),
    (s.chunk_completed, s.chunk_completed.c.completed_at, s.chunk_completed.c.id),
    (s.epoch_owners, s.epoch_owners.c.recorded_at, s.epoch_owners.c.id),
)


def closing_candidates(conn: Connection, since: datetime, until: datetime, limit: int) -> ClosingCandidates:
    """Every open tenant's chunks with a closing fact in ``[since, until]``; a chunk's tenant is the one
    :func:`step_facts_for` tags its facts with."""
    chunk_ids: dict[str, None] = {}
    frontiers: list[datetime] = []
    newest: list[datetime] = []
    for table, at, row_id in _CLOSING_TABLES:
        rows, saturated = _closing_rows(conn, table, at, row_id, since, until, limit)
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
    window = select(*columns).where(open_tenant(table), at <= until).order_by(at, row_id).limit(limit)
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
    stmt = select(table).where(of_chunks(table, table.c.chunk_id, batch)).order_by(*order)
    for row in conn.execute(stmt).all():
        grouped[row.chunk_id].append(row)
    return grouped


def _hydrate(conn: Connection, batch: Sequence[str]) -> dict[str, tuple[TenantId, StepFacts, list[WorkRef]]]:
    chunk_stmt = select(s.chunks).where(open_tenant(s.chunks), s.chunks.c.chunk_id.in_(batch))
    chunk_rows = {r.chunk_id: r for r in conn.execute(chunk_stmt)}
    if not chunk_rows:
        return {}
    ids = list(chunk_rows)
    work_refs = _by_chunk(conn, s.chunk_work_refs, ids, s.chunk_work_refs.c.id)
    leases = _by_chunk(conn, s.lease_facts, ids, s.lease_facts.c.id)
    owners = _by_chunk(conn, s.epoch_owners, ids, s.epoch_owners.c.id)
    transitions = _by_chunk(conn, s.transitions, ids, s.transitions.c.recorded_at)
    migrations = _by_chunk(conn, s.chunk_migrations, ids, s.chunk_migrations.c.recorded_at)
    restarts = _by_chunk(conn, s.chunk_restarts, ids, s.chunk_restarts.c.id)
    escalations = _by_chunk(conn, s.escalations, ids, s.escalations.c.id)
    decisions = _by_chunk(conn, s.decisions, ids, s.decisions.c.submitted_at)
    resolutions = _resolutions(conn, ids)
    requeues = _by_chunk(conn, s.requeues, ids, s.requeues.c.id)
    released = _by_chunk(conn, s.route_released, ids, s.route_released.c.id)
    stopped = _by_chunk(conn, s.chunk_stopped, ids, s.chunk_stopped.c.id)
    completed = _by_chunk(conn, s.chunk_completed, ids, s.chunk_completed.c.id)
    questions = _questions(conn, ids)
    pauses = _by_chunk(conn, s.chunk_pause_facts, ids, s.chunk_pause_facts.c.id)
    turns = _turns(conn, ids)
    slots = _slots(conn, ids)
    polls = _by_chunk(conn, s.hub_node_poll, ids, s.hub_node_poll.c.id)
    bounces = _by_chunk(conn, s.chunk_bounces, ids, s.chunk_bounces.c.id)
    created = _by_chunk(conn, s.route_created, ids, s.route_created.c.created_at)
    promoted = _by_chunk(conn, s.chunk_promoted, ids, s.chunk_promoted.c.id)
    demoted = _by_chunk(conn, s.chunk_demoted, ids, s.chunk_demoted.c.id)
    usage = _by_chunk(conn, s.usage_facts, ids, s.usage_facts.c.id)
    met = _prerequisites_met(conn, ids)
    return {
        chunk_id: (
            TenantId(chunk.tenant_id),
            StepFacts(
                chunk_id=chunk_id,
                tenant=TenantId(chunk.tenant_id),
                project=chunk.project_id,
                pin_graph_id=chunk.graph_id,
                minted_at=chunk.minted_at,
                lease_facts=tuple(TracedLease(r.epoch, r.minted_at) for r in leases[chunk_id]),
                epoch_owners=tuple(TracedEpochOwner(r.epoch, r.runner_id, r.recorded_at) for r in owners[chunk_id]),
                transitions=tuple(_transition(r) for r in transitions[chunk_id]),
                migrations=tuple(_migration(r) for r in migrations[chunk_id]),
                restarts=tuple(_restart(r) for r in restarts[chunk_id]),
                escalations=tuple(
                    TracedEscalation(r.epoch, r.recorded_at, r.decision_id) for r in escalations[chunk_id]
                ),
                decisions=tuple(
                    TracedDecision(r.decision_id, r.node_id, r.epoch, r.submitted_at, r.imposed_by_runner_id)
                    for r in decisions[chunk_id]
                ),
                decision_resolutions=tuple(resolutions[chunk_id]),
                requeues=tuple(TracedRequeue(r.requeued_at) for r in requeues[chunk_id]),
                route_released=tuple(TracedRouteRelease(r.released_at) for r in released[chunk_id]),
                chunk_stopped=tuple(TracedChunkStop(r.stopped_at) for r in stopped[chunk_id]),
                chunk_completed=tuple(TracedChunkCompletion(r.completed_at) for r in completed[chunk_id]),
                questions=tuple(questions[chunk_id]),
                pauses=tuple(TracedPause(str(r.id), r.paused, r.set_at) for r in pauses[chunk_id]),
                hub_turns=tuple(turns[chunk_id]),
                hub_exec_slots=tuple(slots[chunk_id]),
                hub_polls=tuple(TracedHubPoll(str(r.id), r.node_id, r.epoch, r.polled_at) for r in polls[chunk_id]),
                bounces=tuple(TracedBounce(r.epoch, r.cause, r.recorded_at) for r in bounces[chunk_id]),
                routes_created=tuple(TracedRouteCreation(r.created_at) for r in created[chunk_id]),
                promotions=tuple(TracedPromotion(r.promoted_at) for r in promoted[chunk_id]),
                demotions=tuple(TracedDemotion(r.demoted_at) for r in demoted[chunk_id]),
                prerequisites_met=tuple(met[chunk_id]),
                usage=tuple(_usage(r) for r in usage[chunk_id]),
            ),
            [WorkRef(source=r.source, ref=r.ref) for r in work_refs[chunk_id]],
        )
        for chunk_id, chunk in chunk_rows.items()
    }


def _resolutions(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedDecisionResolution]]:
    stmt = (
        select(s.decisions.c.chunk_id, s.decision_resolutions)
        .join(s.decisions, s.decisions.c.decision_id == s.decision_resolutions.c.decision_id)
        .where(open_tenant(s.decision_resolutions), of_chunks(s.decisions, s.decisions.c.chunk_id, ids))
    )
    grouped: dict[str, list[TracedDecisionResolution]] = defaultdict(list)
    for r in conn.execute(stmt).all():
        grouped[r.chunk_id].append(TracedDecisionResolution(r.decision_id, r.resolved_at, r.choice))
    return grouped


def _questions(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedQuestion]]:
    question, answer = s.questions, s.question_answers
    stmt = (
        select(question, answer.c.answered_at)
        .outerjoin(
            answer,
            and_(answer.c.question_id == question.c.question_id, answer.c.tenant_id == question.c.tenant_id),
        )
        .where(of_chunks(question, question.c.chunk_id, ids))
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
            select(submission.submission_id, submission.chunk_id, submission.node_id).where(
                of_chunks(s.flow_submissions, submission.chunk_id, ids)
            )
        ).all()
    }
    grouped: dict[str, list[TracedHubTurn]] = defaultdict(list)
    chunk_tenants = select(s.chunks.c.tenant_id).where(s.chunks.c.chunk_id.in_(ids))
    for batch in id_batches(sorted(visits)):
        rows = conn.execute(
            select(s.hub_turns)
            .where(open_tenant(s.hub_turns), turn.tenant_id.in_(chunk_tenants), turn.submission_id.in_(batch))
            .order_by(turn.started_at)
        ).all()
        for r in rows:
            chunk_id, node_id = visits[r.submission_id]
            grouped[chunk_id].append(TracedHubTurn(r.turn_id, node_id, r.started_at, r.ended_at))
    return grouped


def _slots(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedHubExecSlot]]:
    slot = s.hub_exec_slot
    stmt = select(slot).where(open_tenant(slot), slot.c.holder_chunk_id.in_(ids)).order_by(slot.c.acquired_at)
    grouped: dict[str, list[TracedHubExecSlot]] = defaultdict(list)
    for r in conn.execute(stmt).all():
        grouped[r.holder_chunk_id].append(TracedHubExecSlot(r.slot_id, r.node_id, r.acquired_at, r.released_at))
    return grouped


def _prerequisites_met(conn: Connection, ids: Sequence[str]) -> dict[str, list[TracedPrerequisiteMet]]:
    """When each of a chunk's prerequisites first derived ``done``: its first transition into the
    terminal, or its first operator completion, whichever came first."""
    edge = s.chunk_dependencies.c
    edges = conn.execute(
        select(edge.dependent_chunk_id, edge.prerequisite_chunk_id).where(
            of_chunks(s.chunk_dependencies, edge.dependent_chunk_id, ids)
        )
    ).all()
    prerequisites = sorted({e.prerequisite_chunk_id for e in edges})
    done_at: dict[str, datetime] = {}
    transition, completion = s.transitions.c, s.chunk_completed.c
    for batch in id_batches(prerequisites):
        terminal = select(transition.chunk_id, func.min(transition.recorded_at).label("at")).where(
            of_chunks(s.transitions, transition.chunk_id, batch), transition.to_node_id == RESERVED_TERMINAL
        )
        completed = select(completion.chunk_id, func.min(completion.completed_at).label("at")).where(
            of_chunks(s.chunk_completed, completion.chunk_id, batch)
        )
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
        select(s.usage_facts)
        .where(open_tenant(s.usage_facts), past, u.recorded_at <= until)
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
    stmt = select(s.transcript_event_derivations).where(
        open_tenant(s.transcript_event_derivations), past, _d.derived_at <= until
    )
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
        select(s.transcript_event_drops)
        .where(open_tenant(s.transcript_event_drops), past, _x.dropped_at <= until)
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
        select(s.transcript_events)
        .where(open_tenant(s.transcript_events), _e.segment_id.in_(segment_ids), _e.extractor_version.in_(versions))
        .order_by(_e.tenant_id, _e.segment_id, _e.extractor_version, _e.id)
    )


def _segment_contexts_stmt(segment_ids: Sequence[str]) -> Select[Any]:
    """The columns a derivation's identity needs, never ``content``; ordered so a segment's first record leads."""
    return (
        select(
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
        .where(open_tenant(s.transcript_segments), _g.segment_id.in_(segment_ids))
        .order_by(_g.tenant_id, _g.segment_id, _g.turn_range_start)
    )


def _marker_times_stmt(segment_ids: Sequence[str]) -> Select[Any]:
    return select(_d.tenant_id, _d.segment_id, _d.extractor_version, _d.derived_at).where(
        open_tenant(s.transcript_event_derivations), _d.segment_id.in_(segment_ids)
    )


def epochs_minted_between(
    conn: Connection, since: datetime, until: datetime, after: EpochKey | None, limit: int
) -> list[EpochKey]:
    stmt = select(_l.tenant_id, _l.chunk_id, _l.epoch).where(
        open_tenant(s.lease_facts), _l.minted_at >= since, _l.minted_at < until
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
    stmt = (
        select(s.transcript_event_derivations, _g.chunk_id, _g.epoch)
        .join(segment, and_(_g.segment_id == _d.segment_id, _g.tenant_id == _d.tenant_id))
        .where(open_tenant(s.transcript_event_derivations), of_chunks(segment, _g.chunk_id, chunk_ids))
    )
    if extractor_version is not None:
        stmt = stmt.where(_d.extractor_version == extractor_version)
    return stmt.distinct().order_by(_d.segment_id, _d.extractor_version)


def epoch_drops(conn: Connection, epochs: Sequence[EpochKey]) -> list[DropFact]:
    wanted = set(epochs)
    drops: list[DropFact] = []
    for batch in id_batches(sorted({epoch.chunk_id for epoch in epochs})):
        stmt = (
            select(s.transcript_event_drops)
            .where(of_chunks(s.transcript_event_drops, _x.chunk_id, batch))
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
