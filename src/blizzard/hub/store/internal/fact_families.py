"""The one registry of a chunk's fact families (package-private; ``bzh:fact-families-declared``).

Each chunk-keyed fact table is declared once here, with the family it belongs to, the columns a projection orders
and pages by, and the projections that carry it. Every projection over a chunk's facts derives its table list from
this declaration — ``ChunkFacts`` load, the status probe, ``StepFacts`` for the trace and egress sweeps, closing
candidates, and the activity event feed — and keeps its own per-entry mapper. A table carried by no projection is
named in :data:`EXCLUDED` with its reason; ``tests/test_fact_families.py`` fails any chunk-keyed table that is
neither registered nor excluded.

Membership records what each projection carries today: a family one projection omits is omitted by its entry, not
by a list in that projection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import Column, Table

from blizzard.hub.store import schema as s


class Projection(StrEnum):
    """A reading of a chunk's fact tables."""

    FACTS = "facts"  # ChunkFacts load
    STATUS = "status"  # the tables ChunkFacts.status() reaches, and the change probe over them
    TRACE = "trace"  # StepFacts, for the trace and egress sweeps
    CLOSING = "closing"  # the sweeps' closing-candidate pages
    EVENTS = "events"  # the activity event feed


@dataclass(frozen=True)
class FactTable:
    """One fact table's place in every projection.

    ``family`` is the ``ChunkFacts`` family name, shared by the tables one family owns; ``None`` for a table
    ``ChunkFacts`` does not carry. ``chunk_key`` names the column holding the chunk id, and is ``None`` for a table
    that reaches its chunk through another (an answer through its question). ``row_id`` and ``fact_time`` are the
    columns closing candidates and events page by; the trace orders by ``row_id`` unless ``trace_by_time``.
    ``ephemeral`` marks a table whose chunk ids are gone from every read."""

    table: Table
    family: str | None
    chunk_key: Column[Any] | None
    row_id: Column[Any]
    fact_time: Column[Any] | None
    projections: frozenset[Projection]
    trace_by_time: bool = False
    ephemeral: bool = False


def trace_order(entry: FactTable) -> Column[Any]:
    """The column the trace orders ``entry``'s rows by: its fact time when the registry says so, else its row id."""
    if entry.trace_by_time:
        assert entry.fact_time is not None
        return entry.fact_time
    return entry.row_id


_F, _S, _T, _C, _E = Projection.FACTS, Projection.STATUS, Projection.TRACE, Projection.CLOSING, Projection.EVENTS


def _entry(
    table: Table,
    family: str | None,
    *projections: Projection,
    key: str | None = "chunk_id",
    row_id: str = "id",
    time: str | None,
    trace_by_time: bool = False,
    ephemeral: bool = False,
) -> FactTable:
    return FactTable(
        table=table,
        family=family,
        chunk_key=table.c[key] if key is not None else None,
        row_id=table.c[row_id],
        fact_time=table.c[time] if time is not None else None,
        projections=frozenset(projections),
        trace_by_time=trace_by_time,
        ephemeral=ephemeral,
    )


#: Every fact table, in the order closing candidates read them.
FACT_TABLES: tuple[FactTable, ...] = (
    _entry(
        s.transitions, "transitions", _F, _S, _T, _C, _E, row_id="transition_id", time="recorded_at", trace_by_time=True
    ),
    _entry(s.decisions, "decisions", _F, _S, _T, _C, _E, row_id="decision_id", time="submitted_at", trace_by_time=True),
    _entry(s.decision_resolutions, "decisions", _F, _S, _T, _E, key=None, row_id="decision_id", time="resolved_at"),
    _entry(
        s.chunk_migrations,
        "migrations",
        _F,
        _S,
        _T,
        _C,
        _E,
        row_id="migration_id",
        time="recorded_at",
        trace_by_time=True,
    ),
    _entry(s.escalations, "escalations", _F, _S, _T, _C, _E, time="recorded_at"),
    _entry(s.chunk_restarts, "restarts", _F, _S, _T, _C, _E, time="recorded_at"),
    _entry(s.route_released, "routes_released", _F, _S, _T, _C, _E, time="released_at"),
    _entry(s.chunk_stopped, "stopped", _F, _S, _T, _C, _E, time="stopped_at"),
    _entry(s.chunk_completed, "operator_completed", _F, _S, _T, _C, _E, time="completed_at"),
    _entry(s.epoch_owners, "epoch_owners", _F, _T, _C, time="recorded_at"),
    _entry(s.chunk_promoted, "promoted", _F, _S, _T, _E, time="promoted_at"),
    _entry(s.chunk_demoted, "promoted", _F, _S, _T, _E, time="demoted_at"),
    _entry(s.lease_facts, "leases", _F, _S, _T, time="minted_at"),
    _entry(s.route_created, "routes_created", _F, _S, _T, _E, row_id="route_id", time="created_at", trace_by_time=True),
    _entry(s.route_token_minted, "route_tokens_minted", _F, time="minted_at"),
    _entry(s.questions, "questions", _F, _S, _T, _E, row_id="question_id", time="asked_at"),
    _entry(s.question_answers, "questions", _F, _S, _E, key=None, row_id="question_id", time="answered_at"),
    _entry(s.requeues, "requeues", _F, _S, _T, _E, time="requeued_at"),
    _entry(s.chunk_pause_facts, "pauses", _F, _S, _T, _E, time="set_at"),
    _entry(s.usage_facts, "usage", _F, _T, time="recorded_at"),
    _entry(s.chunk_bounces, "bounces", _F, _T, time="recorded_at"),
    _entry(s.hub_node_poll, "hub_node_polls", _F, _T, time="polled_at"),
    _entry(s.delivery_landed, "delivery_landed", _F, time="landed_at"),
    _entry(s.delivery_repo_landed, "landed_repos", _F, time="landed_at"),
    _entry(s.chunk_work_refs, None, _T, time=None),
    _entry(s.hub_exec_slot, None, _T, key="holder_chunk_id", row_id="slot_id", time="acquired_at"),
    _entry(s.chunk_dependencies, None, _T, key="dependent_chunk_id", row_id="dependency_id", time="declared_at"),
    _entry(s.flow_submissions, None, _T, row_id="submission_id", time="submitted_at"),
    _entry(s.chunk_grouped, None, _E, time="grouped_at", ephemeral=True),
    _entry(s.chunk_deleted, None, _E, time="deleted_at", ephemeral=True),
)

#: Chunk-keyed tables no projection reads, each with the reason it is out.
EXCLUDED: dict[str, str] = {
    "artifacts": "an asset a node produced; not a fact about the chunk's progress",
    "finding_sets": "review findings; read by the review seams, not a projection",
    "findings": "review findings; read by the review seams, not a projection",
    "work_item_closures": "the work-item close record; read by the close drain",
    "close_intents": "a close drain's queue; read by the close drain",
    "close_intent_attempts": "a close drain's attempt log",
    "answer_deliveries": "delivery bookkeeping for an answer, read by the answer seam",
    "queue_positions": "a derived queue ordering, rebuilt on read",
    "flow_facts": "the flow driver's own facts",
    "event_log": "the operational log, read by its own feed",
    "transcript_segments": "transcript storage; read by the event derivation",
    "transcript_events": "transcript storage; read by the event derivation",
    "transcript_event_drops": "transcript storage; read by the event derivation",
    "trace_cursor": "an export cursor",
    "egress_cursor": "an export cursor",
}


def entries(projection: Projection) -> tuple[FactTable, ...]:
    """The entries ``projection`` carries, in declaration order."""
    return tuple(e for e in FACT_TABLES if projection in e.projections)


def families(projection: Projection) -> frozenset[str]:
    """The ``ChunkFacts`` family names ``projection`` carries."""
    return frozenset(e.family for e in entries(projection) if e.family is not None)


def family_tables(projection: Projection) -> dict[str, tuple[Table, ...]]:
    """Each family ``projection`` carries, with the tables of it that it reads."""
    grouped: dict[str, list[Table]] = {}
    for e in entries(projection):
        if e.family is not None:
            grouped.setdefault(e.family, []).append(e.table)
    return {family: tuple(tables) for family, tables in grouped.items()}


def tables(projection: Projection) -> tuple[Table, ...]:
    """The tables ``projection`` reads, in declaration order."""
    return tuple(e.table for e in entries(projection))


def ephemeral_tables() -> tuple[Table, ...]:
    """The tables whose chunk ids are gone from every read."""
    return tuple(e.table for e in FACT_TABLES if e.ephemeral)


def entry_of(table: Table) -> FactTable:
    return next(e for e in FACT_TABLES if e.table is table)
