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
    OutcomeStats,
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


def _judged_distribution_stmt(criteria: OperationalCriteria) -> Select[Any]:
    """One row per ``(node, choice)`` matching ``criteria``, with its occurrence count
    (D4) — a *judged* outcome, so only rows carrying a ``choice_name`` (a gate's
    decision-only migration and every failed attempt carry none)."""
    t = s.transitions
    stmt = select(t.c.from_node_id, t.c.choice_name, func.count().label("occurrences"))
    stmt = stmt.where(t.c.from_node_id.is_not(None), t.c.choice_name.is_not(None))
    if criteria.graph_id is not None:
        stmt = stmt.where(t.c.graph_id == criteria.graph_id)
    if criteria.source is not None:
        stmt = stmt.where(t.c.chunk_id.in_(_source_chunks_stmt(criteria.source)))
    if criteria.since is not None:
        stmt = stmt.where(t.c.recorded_at >= criteria.since)
    if criteria.until is not None:
        stmt = stmt.where(t.c.recorded_at < criteria.until)
    return stmt.group_by(t.c.from_node_id, t.c.choice_name)


def _candidate_lease_epochs_stmt(criteria: OperationalCriteria) -> Select[Any]:
    """Every deduped ``(chunk_id, epoch, minted_at)`` (A7) whose chunk matches ``source``
    and whose mint falls in the window — the attempts D5 must resolve a node for. The
    graph filter is not pushed here: a failed attempt carries no graph of its own, so it
    is applied in Python once D5 has derived one (see :func:`_attempt_failures`)."""
    t = s.lease_facts
    stmt = _lease_min_stmt()
    if criteria.source is not None:
        stmt = stmt.where(t.c.chunk_id.in_(_source_chunks_stmt(criteria.source)))
    if criteria.since is not None:
        stmt = stmt.where(t.c.minted_at >= criteria.since)
    if criteria.until is not None:
        stmt = stmt.where(t.c.minted_at < criteria.until)
    return stmt


def _chunk_transitions_stmt(chunk_ids: Sequence[str]) -> Select[Any]:
    """Every transition ever recorded for ``chunk_ids`` — unfiltered by ``criteria``'s
    own window/graph: D5 resolves a failed attempt's node from whatever movement came
    before it, which can predate the window a caller asked about."""
    t = s.transitions
    return select(t.c.chunk_id, t.c.epoch, t.c.to_node_id, t.c.graph_id).where(t.c.chunk_id.in_(chunk_ids))


def _chunk_migrations_stmt(chunk_ids: Sequence[str]) -> Select[Any]:
    m = s.chunk_migrations
    return select(m.c.chunk_id, m.c.epoch, m.c.landed_node_id, m.c.to_graph_id).where(m.c.chunk_id.in_(chunk_ids))


def _chunk_bounces_stmt(chunk_ids: Sequence[str]) -> Select[Any]:
    b = s.chunk_bounces
    return select(b.c.chunk_id, b.c.epoch).where(b.c.chunk_id.in_(chunk_ids))


def _chunks_graph_stmt(chunk_ids: Sequence[str]) -> Select[Any]:
    c = s.chunks
    return select(c.c.chunk_id, c.c.graph_id).where(c.c.chunk_id.in_(chunk_ids))


def _graph_entry_nodes_stmt(graph_ids: Sequence[str]) -> Select[Any]:
    g = s.graphs
    return select(g.c.graph_id, g.c.entry_node_id).where(g.c.graph_id.in_(graph_ids))


def _attempt_failures(
    lease_rows: Sequence[Any],
    transition_rows: Sequence[Any],
    migration_rows: Sequence[Any],
    bounce_rows: Sequence[Any],
    chunk_graph_rows: Sequence[Any],
    graph_entry_rows: Sequence[Any],
    graph_id_filter: str | None,
) -> dict[str, int]:
    """D5: derive a node for every lease epoch that produced neither a transition nor a
    migration — the crashes, verdict-less exits, and reaps that consume a node's retry
    budget — and count them per node. A ``chunk_bounces`` epoch is excluded outright
    (D4: contention, not failure). The derived node is the destination of the chunk's
    latest movement below that epoch — a transition's ``to_node_id`` or a migration's
    ``landed_node_id`` — or the chunk's pinned graph's own entry node when there is none;
    that fallback is exact only because a chunk with zero movements has never migrated
    (a migration is itself a movement), so its current graph pin is still its first."""
    transitions_by_chunk: dict[str, list[tuple[int, str, str]]] = {}
    for row in transition_rows:
        transitions_by_chunk.setdefault(row.chunk_id, []).append((row.epoch, row.to_node_id, row.graph_id))
    migrations_by_chunk: dict[str, list[tuple[int, str, str]]] = {}
    for row in migration_rows:
        migrations_by_chunk.setdefault(row.chunk_id, []).append((row.epoch, row.landed_node_id, row.to_graph_id))
    resolved_epochs_by_chunk: dict[str, set[int]] = {}
    for row in transition_rows:
        resolved_epochs_by_chunk.setdefault(row.chunk_id, set()).add(row.epoch)
    for row in migration_rows:
        resolved_epochs_by_chunk.setdefault(row.chunk_id, set()).add(row.epoch)
    bounced_epochs_by_chunk: dict[str, set[int]] = {}
    for row in bounce_rows:
        bounced_epochs_by_chunk.setdefault(row.chunk_id, set()).add(row.epoch)
    chunk_graph: dict[str, str] = {row.chunk_id: row.graph_id for row in chunk_graph_rows}
    graph_entry_node: dict[str, str] = {row.graph_id: row.entry_node_id for row in graph_entry_rows}

    failures: dict[str, int] = {}
    for row in lease_rows:
        chunk_id, epoch = row.chunk_id, row.epoch
        if epoch in bounced_epochs_by_chunk.get(chunk_id, ()):
            continue
        if epoch in resolved_epochs_by_chunk.get(chunk_id, ()):
            continue
        movements = [(e, node, graph) for e, node, graph in transitions_by_chunk.get(chunk_id, []) if e < epoch]
        movements += [(e, node, graph) for e, node, graph in migrations_by_chunk.get(chunk_id, []) if e < epoch]
        if movements:
            _, node_id, graph_id = max(movements, key=lambda m: m[0])
        else:
            graph_id = chunk_graph[chunk_id]
            node_id = graph_entry_node[graph_id]
        if graph_id_filter is not None and graph_id != graph_id_filter:
            continue
        failures[node_id] = failures.get(node_id, 0) + 1
    return failures


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

    def outcomes_by_node(self, criteria: OperationalCriteria) -> list[OutcomeStats]:
        with self._engine.connect() as conn:
            judged_rows = conn.execute(_judged_distribution_stmt(criteria)).all()
            lease_rows = conn.execute(_candidate_lease_epochs_stmt(criteria)).all()
            chunk_ids = sorted({row.chunk_id for row in lease_rows})
            transition_rows = conn.execute(_chunk_transitions_stmt(chunk_ids)).all() if chunk_ids else []
            migration_rows = conn.execute(_chunk_migrations_stmt(chunk_ids)).all() if chunk_ids else []
            bounce_rows = conn.execute(_chunk_bounces_stmt(chunk_ids)).all() if chunk_ids else []
            chunk_graph_rows = conn.execute(_chunks_graph_stmt(chunk_ids)).all() if chunk_ids else []
            graph_ids = sorted({row.graph_id for row in chunk_graph_rows} | {row.to_graph_id for row in migration_rows})
            graph_entry_rows = conn.execute(_graph_entry_nodes_stmt(graph_ids)).all() if graph_ids else []

        judged: dict[str, dict[str, int]] = {}
        for row in judged_rows:
            judged.setdefault(row.from_node_id, {})[row.choice_name] = row.occurrences
        failures = _attempt_failures(
            lease_rows,
            transition_rows,
            migration_rows,
            bounce_rows,
            chunk_graph_rows,
            graph_entry_rows,
            criteria.graph_id,
        )
        nodes = set(judged) | set(failures)
        return [
            OutcomeStats(node_id=n, choice_counts=judged.get(n, {}), attempt_failures=failures.get(n, 0))
            for n in sorted(nodes)
        ]


def _conforms_analytics_operational_store(x: AnalyticsOperationalStore) -> IReadOperationalAnalytics:
    return x
