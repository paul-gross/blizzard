"""SQLAlchemy adapter for the analytics event query seam (package-private).

Reads ``transcript_events`` directly — the same table :mod:`transcript_event_store`
writes — rather than depending on that adapter: two ``internal/`` adapters sharing one
engine and schema module is established, not a coupling between them (see that module's
own docstring). All ``sqlalchemy`` usage stays confined here (``bzh:dependency-inversion``)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, func, select

from blizzard.hub.domain.analytics.events import KIND_FILE_READ, KIND_SKILL_INVOCATION
from blizzard.hub.domain.analytics.queries import (
    CountRow,
    EventPage,
    EventQueryCriteria,
    EventRecord,
    IReadAnalyticsEventQueries,
)
from blizzard.hub.domain.pagination import MalformedCursor, decode_cursor, encode_cursor
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections

#: ``events``' cursor is the shared codec over one part: the last row's id.
_CURSOR_ARITY = 1

# --- statements: nothing below executes a statement built elsewhere, so the unit tier
# compiles the real ones under both dialects (`bzh:sql-portable`).


def _filtered_stmt(base: Select[Any], criteria: EventQueryCriteria) -> Select[Any]:
    t = s.transcript_events
    stmt = base.where(t.c.extractor_version == criteria.extractor_version)
    if criteria.kind is not None:
        stmt = stmt.where(t.c.kind == criteria.kind)
    if criteria.tool is not None:
        stmt = stmt.where(t.c.tool == criteria.tool)
    if criteria.subject_prefix is not None:
        # `substr` + `=` compares literally and case-sensitively on both dialects; LIKE
        # would fold case on sqlite alone, making a count's value follow the backend.
        stmt = stmt.where(func.substr(t.c.subject, 1, len(criteria.subject_prefix)) == criteria.subject_prefix)
    if criteria.node_id is not None:
        stmt = stmt.where(t.c.node_id == criteria.node_id)
    if criteria.graph_id is not None:
        stmt = stmt.where(t.c.graph_id == criteria.graph_id)
    if criteria.source is not None:
        matching_chunks = select(s.chunk_work_refs.c.chunk_id).where(s.chunk_work_refs.c.source == criteria.source)
        stmt = stmt.where(t.c.chunk_id.in_(matching_chunks))
    if criteria.since is not None:
        stmt = stmt.where(t.c.occurred_at >= criteria.since)
    if criteria.until is not None:
        stmt = stmt.where(t.c.occurred_at < criteria.until)
    if criteria.harness_id is not None:
        stmt = stmt.where(t.c.harness_id == criteria.harness_id)
    if criteria.harness_version is not None:
        stmt = stmt.where(t.c.harness_version == criteria.harness_version)
    if criteria.model is not None:
        stmt = stmt.where(t.c.model == criteria.model)
    if criteria.effort is not None:
        stmt = stmt.where(t.c.effort == criteria.effort)
    return stmt


def _decode_cursor(cursor: str) -> int:
    parts = decode_cursor(cursor)
    if len(parts) != _CURSOR_ARITY or type(parts[0]) is not int or parts[0] < 0:
        raise MalformedCursor(cursor)
    return parts[0]


def _events_stmt(criteria: EventQueryCriteria, *, cursor: str | None, limit: int) -> Select[Any]:
    t = s.transcript_events
    stmt = _filtered_stmt(select(t), criteria)
    if cursor is not None:
        stmt = stmt.where(t.c.id > _decode_cursor(cursor))
    # `id` alone is already total (`bzh:sql-portable`), unlike the nullable `occurred_at`.
    return stmt.order_by(t.c.id).limit(limit + 1)


def _counts_stmt(criteria: EventQueryCriteria, *, group_col: Any, kind: str | None) -> Select[Any]:
    # Labeled "occurrences", never "count" — `Row` inherits `tuple.count`, so a same-named
    # label would shadow attribute access to the aggregate with a bound method.
    stmt = _filtered_stmt(select(group_col.label("key"), func.count().label("occurrences")), criteria)
    if kind is not None:
        # Intersected with `criteria.kind`, never substituted for it: a caller naming a
        # different kind asked for an empty scope and gets one.
        stmt = stmt.where(s.transcript_events.c.kind == kind)
    stmt = stmt.where(group_col.is_not(None)).group_by(group_col)
    return stmt.order_by(func.count().desc(), group_col.asc())


def _counts_by_node_stmt(criteria: EventQueryCriteria) -> Select[Any]:
    """Counts by node id, each naming its node and graph by scalar lookups on the primary
    keys — null where the id no longer resolves — so the names add no grouping column."""
    t, gn, g = s.transcript_events, s.graph_nodes, s.graphs
    graph_name = select(g.c.name).where(g.c.graph_id == gn.c.graph_id, gn.c.node_id == t.c.node_id).scalar_subquery()
    node_name = select(gn.c.name).where(gn.c.node_id == t.c.node_id).scalar_subquery()
    cols = (
        t.c.node_id.label("key"),
        graph_name.label("graph_name"),
        node_name.label("node_name"),
        func.count().label("occurrences"),
    )
    stmt = _filtered_stmt(select(*cols), criteria)
    stmt = stmt.where(t.c.node_id.is_not(None)).group_by(t.c.node_id)
    return stmt.order_by(func.count().desc(), t.c.node_id.asc())


def _to_record(row: Any) -> EventRecord:
    return EventRecord(
        id=row.id,
        kind=row.kind,
        subject=row.subject,
        tool=row.tool,
        payload=row.payload,
        chunk_id=row.chunk_id,
        node_id=row.node_id,
        epoch=row.epoch,
        spawn_generation=row.spawn_generation,
        graph_id=row.graph_id,
        depth=row.depth,
        agent_type=row.agent_type,
        occurred_at=row.occurred_at,
        harness_id=row.harness_id,
        harness_version=row.harness_version,
        model=row.model,
        effort=row.effort,
    )


class AnalyticsEventQueryStore:
    """Read-only analytics-event query adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def events(self, criteria: EventQueryCriteria, *, cursor: str | None = None, limit: int) -> EventPage:
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        with self._store.read("events") as conn:
            rows = conn.execute(_events_stmt(criteria, cursor=cursor, limit=limit)).all()
        page_rows = rows[:limit]
        next_cursor = encode_cursor(page_rows[-1].id) if len(rows) > limit else None
        return EventPage(events=[_to_record(row) for row in page_rows], next_cursor=next_cursor)

    def counts_by_file(self, criteria: EventQueryCriteria) -> list[CountRow]:
        return self._counts(criteria, group_col=s.transcript_events.c.subject, kind=KIND_FILE_READ)

    def counts_by_skill(self, criteria: EventQueryCriteria) -> list[CountRow]:
        return self._counts(criteria, group_col=s.transcript_events.c.subject, kind=KIND_SKILL_INVOCATION)

    def counts_by_agent_type(self, criteria: EventQueryCriteria) -> list[CountRow]:
        return self._counts(criteria, group_col=s.transcript_events.c.agent_type, kind=None)

    def counts_by_node(self, criteria: EventQueryCriteria) -> list[CountRow]:
        with self._store.read("counts_by_node") as conn:
            rows = conn.execute(_counts_by_node_stmt(criteria)).all()
        return [
            CountRow(key=row.key, count=row.occurrences, graph_name=row.graph_name, node_name=row.node_name)
            for row in rows
        ]

    def _counts(self, criteria: EventQueryCriteria, *, group_col: Any, kind: str | None) -> list[CountRow]:
        with self._store.read("counts") as conn:
            rows = conn.execute(_counts_stmt(criteria, group_col=group_col, kind=kind)).all()
        return [CountRow(key=row.key, count=row.occurrences) for row in rows]


def _conforms_analytics_event_query_store(x: AnalyticsEventQueryStore) -> IReadAnalyticsEventQueries:
    return x
