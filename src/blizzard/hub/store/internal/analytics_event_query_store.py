"""SQLAlchemy adapter for the analytics event query seam (package-private, blizzard#255).

Reads ``transcript_events`` directly — the same table :mod:`transcript_event_store`
writes — rather than depending on that adapter: two ``internal/`` adapters sharing one
engine and schema module is established, not a coupling between them (see that module's
own docstring). All ``sqlalchemy`` usage stays confined here (``bzh:dependency-inversion``)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, Select, func, select

from blizzard.hub.domain.analytics.events import KIND_FILE_READ, KIND_SKILL_INVOCATION
from blizzard.hub.domain.analytics.queries import (
    CountRow,
    EventPage,
    EventQueryCriteria,
    EventRecord,
    IReadAnalyticsEventQueries,
)
from blizzard.hub.store import schema as s

# --- statements: nothing below executes a statement built elsewhere, so the unit tier
# compiles the real ones under both dialects (`bzh:sql-portable`).


def _filtered_stmt(base: Select[Any], criteria: EventQueryCriteria) -> Select[Any]:
    t = s.transcript_events
    stmt = base.where(t.c.extractor_version == criteria.extractor_version)
    if criteria.kind is not None:
        stmt = stmt.where(t.c.kind == criteria.kind)
    if criteria.tool is not None:
        stmt = stmt.where(t.c.tool == criteria.tool)
    if criteria.path_prefix is not None:
        # `autoescape` is load-bearing, not decoration: a bare `startswith` leaves LIKE's
        # own `_`/`%` live, and a path prefix carries underscores constantly
        # (`analytics_event_query_store.py`), so an unescaped prefix silently over-matches.
        stmt = stmt.where(t.c.subject.startswith(criteria.path_prefix, autoescape=True))
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
    return stmt


def _events_stmt(criteria: EventQueryCriteria, *, cursor: str | None, limit: int) -> Select[Any]:
    t = s.transcript_events
    stmt = _filtered_stmt(select(t), criteria)
    if cursor is not None:
        stmt = stmt.where(t.c.id > int(cursor))
    # An explicit total order (`bzh:sql-portable`) — `id` alone, since it is already a
    # total order over the table and needs no tiebreak, unlike the nullable `occurred_at`.
    return stmt.order_by(t.c.id).limit(limit + 1)


def _counts_stmt(criteria: EventQueryCriteria, *, group_col: Any, kind: str | None) -> Select[Any]:
    # Labeled "occurrences", never "count" — `Row` inherits `tuple.count`, so a same-named
    # label would shadow attribute access to the aggregate with a bound method.
    stmt = _filtered_stmt(select(group_col.label("key"), func.count().label("occurrences")), criteria)
    if kind is not None:
        # Intersected with `criteria.kind`, never substituted for it: a caller naming a
        # different kind asked for an empty scope and gets one, rather than this count's
        # own kind silently back.
        stmt = stmt.where(s.transcript_events.c.kind == kind)
    stmt = stmt.where(group_col.is_not(None)).group_by(group_col)
    # Most-frequent first, key ascending as the deterministic tiebreak.
    return stmt.order_by(func.count().desc(), group_col.asc())


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
    )


class AnalyticsEventQueryStore:
    """Read-only analytics-event query adapter over the hub store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def events(self, criteria: EventQueryCriteria, *, cursor: str | None = None, limit: int = 200) -> EventPage:
        with self._engine.connect() as conn:
            rows = conn.execute(_events_stmt(criteria, cursor=cursor, limit=limit)).all()
        page_rows = rows[:limit]
        next_cursor = str(page_rows[-1].id) if len(rows) > limit else None
        return EventPage(events=[_to_record(row) for row in page_rows], next_cursor=next_cursor)

    def counts_by_file(self, criteria: EventQueryCriteria) -> list[CountRow]:
        return self._counts(criteria, group_col=s.transcript_events.c.subject, kind=KIND_FILE_READ)

    def counts_by_skill(self, criteria: EventQueryCriteria) -> list[CountRow]:
        return self._counts(criteria, group_col=s.transcript_events.c.subject, kind=KIND_SKILL_INVOCATION)

    def counts_by_agent_type(self, criteria: EventQueryCriteria) -> list[CountRow]:
        return self._counts(criteria, group_col=s.transcript_events.c.agent_type, kind=None)

    def counts_by_node(self, criteria: EventQueryCriteria) -> list[CountRow]:
        return self._counts(criteria, group_col=s.transcript_events.c.node_id, kind=None)

    def _counts(self, criteria: EventQueryCriteria, *, group_col: Any, kind: str | None) -> list[CountRow]:
        with self._engine.connect() as conn:
            rows = conn.execute(_counts_stmt(criteria, group_col=group_col, kind=kind)).all()
        return [CountRow(key=row.key, count=row.occurrences) for row in rows]


def _conforms_analytics_event_query_store(x: AnalyticsEventQueryStore) -> IReadAnalyticsEventQueries:
    return x
