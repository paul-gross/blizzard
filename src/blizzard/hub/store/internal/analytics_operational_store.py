"""SQLAlchemy adapter for the operational analytics query seam (package-private,
blizzard#256).

Reads ``transitions``, ``lease_facts``, ``usage_facts``, and ``chunk_migrations``
directly — the same tables :mod:`chunk_store` writes — rather than depending on that
adapter: two ``internal/`` adapters sharing one engine and schema module is established,
not a coupling between them (see :mod:`analytics_event_query_store`'s own docstring for
the precedent). All ``sqlalchemy`` usage stays confined here (``bzh:dependency-inversion``).

Filtering and joining stay on the portable SQL surface (``bzh:sql-portable``); the
datetime arithmetic a duration needs has no dialect-independent SQL form in this
codebase's established style (:mod:`analytics_event_query_store` avoids it too), so a
duration's own subtraction happens in Python, once per matching row."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Engine, Select, case, func, select

from blizzard.hub.domain.analytics.operational import (
    ChunkSpendPage,
    ChunkSpendRecord,
    DurationStats,
    IReadOperationalAnalytics,
    OperationalCriteria,
    SpendStats,
)
from blizzard.hub.domain.analytics.queries import MalformedCursor
from blizzard.hub.store import schema as s

#: The whole cursor format `spend_by_chunk` mints: a chunk id, plain (a ULID sorts
#: chronologically as a string, `bzh:sql-portable`).
_CHUNK_CURSOR = re.compile(r"ch_[0-9A-HJKMNP-TV-Z]+")

# --- statements: nothing below executes a statement built elsewhere, so the unit tier
# compiles the real ones under both dialects (`bzh:sql-portable`).


def _lease_min_stmt() -> Select[Any]:
    """One row per ``(chunk_id, epoch)``, the earliest ``minted_at`` among any duplicate
    lease rows that pair (A7) — ``record_lease`` is a bare insert with no unique
    constraint, so a join straight to ``lease_facts`` could double-count a step."""
    t = s.lease_facts
    return select(t.c.chunk_id, t.c.epoch, func.min(t.c.minted_at).label("minted_at")).group_by(t.c.chunk_id, t.c.epoch)


def _source_chunks_stmt(source: str) -> Select[Any]:
    return select(s.chunk_work_refs.c.chunk_id).where(s.chunk_work_refs.c.source == source)


def _duration_rows_stmt(criteria: OperationalCriteria) -> Select[Any]:
    """Every completed step matching ``criteria`` (D2) — one row per transition with an
    attributable ``from_node_id``, paired to the lease that fenced the attempt producing
    it. A transition with no matching lease row (never expected — a transition never
    accepts without a live lease) is silently excluded by the inner join, same as it
    would be by any join naming that pair."""
    t = s.transitions
    lease_min = _lease_min_stmt().subquery()
    stmt = select(t.c.from_node_id, t.c.graph_id, t.c.recorded_at, lease_min.c.minted_at).select_from(
        t.join(lease_min, (t.c.chunk_id == lease_min.c.chunk_id) & (t.c.epoch == lease_min.c.epoch))
    )
    stmt = stmt.where(t.c.from_node_id.is_not(None))
    if criteria.graph_id is not None:
        stmt = stmt.where(t.c.graph_id == criteria.graph_id)
    if criteria.source is not None:
        stmt = stmt.where(t.c.chunk_id.in_(_source_chunks_stmt(criteria.source)))
    if criteria.since is not None:
        stmt = stmt.where(t.c.recorded_at >= criteria.since)
    if criteria.until is not None:
        stmt = stmt.where(t.c.recorded_at < criteria.until)
    return stmt


def _duration_stats(rows: Sequence[Any], *, key: str) -> list[DurationStats]:
    """Group already-fetched ``(from_node_id, graph_id, recorded_at, minted_at)`` rows by
    ``key`` (``"node"`` or ``"graph"``) and roll each group up — the aggregation Python
    side, alongside the seconds subtraction itself (see module docstring)."""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in rows:
        group_key = row.from_node_id if key == "node" else row.graph_id
        seconds = (row.recorded_at - row.minted_at).total_seconds()
        totals[group_key] = totals.get(group_key, 0.0) + seconds
        counts[group_key] = counts.get(group_key, 0) + 1
    return [
        DurationStats(key=k, completed_steps=counts[k], total_seconds=totals[k], avg_seconds=totals[k] / counts[k])
        for k in sorted(counts)
    ]


def _decode_chunk_cursor(cursor: str) -> str:
    if not _CHUNK_CURSOR.fullmatch(cursor):
        raise MalformedCursor(cursor)
    return cursor


def _spend_filtered_stmt(base: Select[Any], criteria: OperationalCriteria) -> Select[Any]:
    """``usage_facts`` joined to its chunk — every grouping needs the join, since the
    graph filter (D7) narrows by the chunk's current graph pin, ``usage_facts`` carrying
    no ``graph_id`` of its own (D6's ``spend_by_graph`` documents why that pin, not a
    historical one, is what "this graph" means here)."""
    u, c = s.usage_facts, s.chunks
    stmt = base.select_from(u.join(c, u.c.chunk_id == c.c.chunk_id))
    if criteria.graph_id is not None:
        stmt = stmt.where(c.c.graph_id == criteria.graph_id)
    if criteria.source is not None:
        stmt = stmt.where(u.c.chunk_id.in_(_source_chunks_stmt(criteria.source)))
    if criteria.since is not None:
        stmt = stmt.where(u.c.recorded_at >= criteria.since)
    if criteria.until is not None:
        stmt = stmt.where(u.c.recorded_at < criteria.until)
    return stmt


def _spend_group_stmt(criteria: OperationalCriteria, *, group_col: Any) -> Select[Any]:
    """Usage/cost summed in SQL, grouped by ``group_col`` (D6) — ``UsageTotal.of``'s own
    lower-bound + PARTIAL contract, reproduced here rather than imported: a null
    ``cost_usd`` is skipped from the sum (``coalesce`` never substitutes a fabricated
    zero into the total itself), and ``null_cost_rows`` counts how many of the group's
    rows lacked one, so the store can render ``cost_partial`` without re-reading rows."""
    u = s.usage_facts
    stmt = select(
        group_col.label("key"),
        func.coalesce(func.sum(u.c.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(u.c.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(u.c.cache_read_tokens), 0).label("cache_read_tokens"),
        func.coalesce(func.sum(u.c.cache_create_tokens), 0).label("cache_create_tokens"),
        func.coalesce(func.sum(u.c.cost_usd), 0.0).label("cost_usd"),
        func.coalesce(func.sum(case((u.c.cost_usd.is_(None), 1), else_=0)), 0).label("null_cost_rows"),
    )
    stmt = _spend_filtered_stmt(stmt, criteria)
    return stmt.group_by(group_col).order_by(group_col.asc())


def _spend_by_node_stmt(criteria: OperationalCriteria) -> Select[Any]:
    return _spend_group_stmt(criteria, group_col=s.usage_facts.c.node_id)


def _spend_by_graph_stmt(criteria: OperationalCriteria) -> Select[Any]:
    return _spend_group_stmt(criteria, group_col=s.chunks.c.graph_id)


def _spend_by_chunk_stmt(criteria: OperationalCriteria, *, cursor: str | None, limit: int) -> Select[Any]:
    u = s.usage_facts
    stmt = _spend_group_stmt(criteria, group_col=u.c.chunk_id)
    if cursor is not None:
        stmt = stmt.where(u.c.chunk_id > _decode_chunk_cursor(cursor))
    return stmt.limit(limit + 1)


def _to_spend_stats(row: Any) -> SpendStats:
    return SpendStats(
        key=row.key,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cache_read_tokens=row.cache_read_tokens,
        cache_create_tokens=row.cache_create_tokens,
        cost_usd=row.cost_usd,
        cost_partial=row.null_cost_rows > 0,
    )


def _to_chunk_spend_record(row: Any) -> ChunkSpendRecord:
    return ChunkSpendRecord(
        chunk_id=row.key,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cache_read_tokens=row.cache_read_tokens,
        cache_create_tokens=row.cache_create_tokens,
        cost_usd=row.cost_usd,
        cost_partial=row.null_cost_rows > 0,
    )


class AnalyticsOperationalStore:
    """Read-only operational-analytics query adapter over the hub store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def durations_by_node(self, criteria: OperationalCriteria) -> list[DurationStats]:
        with self._engine.connect() as conn:
            rows = conn.execute(_duration_rows_stmt(criteria)).all()
        return _duration_stats(rows, key="node")

    def durations_by_graph(self, criteria: OperationalCriteria) -> list[DurationStats]:
        with self._engine.connect() as conn:
            rows = conn.execute(_duration_rows_stmt(criteria)).all()
        return _duration_stats(rows, key="graph")

    def spend_by_node(self, criteria: OperationalCriteria) -> list[SpendStats]:
        with self._engine.connect() as conn:
            rows = conn.execute(_spend_by_node_stmt(criteria)).all()
        return [_to_spend_stats(row) for row in rows]

    def spend_by_graph(self, criteria: OperationalCriteria) -> list[SpendStats]:
        with self._engine.connect() as conn:
            rows = conn.execute(_spend_by_graph_stmt(criteria)).all()
        return [_to_spend_stats(row) for row in rows]

    def spend_by_chunk(self, criteria: OperationalCriteria, *, cursor: str | None = None, limit: int) -> ChunkSpendPage:
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        with self._engine.connect() as conn:
            rows = conn.execute(_spend_by_chunk_stmt(criteria, cursor=cursor, limit=limit)).all()
        page_rows = rows[:limit]
        next_cursor = page_rows[-1].key if len(rows) > limit else None
        return ChunkSpendPage(records=[_to_chunk_spend_record(row) for row in page_rows], next_cursor=next_cursor)


def _conforms_analytics_operational_store(x: AnalyticsOperationalStore) -> IReadOperationalAnalytics:
    return x
