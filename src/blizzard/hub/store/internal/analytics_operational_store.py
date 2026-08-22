"""SQLAlchemy adapter for the operational analytics query seam (package-private,
blizzard#256). Reads ``transitions``/``lease_facts``/``usage_facts``/``chunk_migrations``
directly, the same tables :mod:`chunk_store` writes — two ``internal/`` adapters sharing
one engine is established (see :mod:`analytics_event_query_store`). Filtering stays
portable SQL (``bzh:sql-portable``); D2/D5's own business rules live in the domain-owned
fold this adapter only fetches and maps rows for."""

from __future__ import annotations

from typing import Any

from sqlalchemy import CompoundSelect, Engine, Select, case, func, select, tuple_, union

from blizzard.foundation.ids import CHUNK_PREFIX, Id
from blizzard.hub.domain.analytics import MalformedCursor
from blizzard.hub.domain.analytics.operational import (
    ChunkSpendPage,
    DurationStats,
    IReadOperationalAnalytics,
    JudgedChoiceRow,
    LeaseEpoch,
    MigrationMovement,
    OperationalCriteria,
    OutcomeStats,
    SpendStats,
    StepDuration,
    TransitionMovement,
    fold_step_durations,
    group_judged_choices,
    resolve_attempt_failures,
    steps_in_window,
    summarize_durations,
    summarize_outcomes,
)
from blizzard.hub.domain.graph import Executor
from blizzard.hub.domain.work import UsageTotal
from blizzard.hub.store import schema as s

# --- statements: nothing below executes a statement built elsewhere, so the unit tier
# compiles the real ones under both dialects (`bzh:sql-portable`).


def _runner_executed_or_entry(t: Any, gn: Any) -> Any:
    """D2's runner-executed-step restriction, factored once so
    ``_duration_window_groups_stmt`` and ``_duration_rows_stmt`` — which must agree, since
    the first selects the groups the second fetches rows for — can't silently diverge."""
    return (gn.c.executor == Executor.RUNNER.value) | (t.c.from_node_id.is_(None))


def _lease_min_stmt() -> Select[Any]:
    """One row per ``(chunk_id, epoch)``, the earliest ``minted_at`` among any duplicate
    lease rows that pair (A7) — a bare insert with no unique constraint, so a join
    straight to ``lease_facts`` could double-count a step. Deliberately unwindowed —
    ``_candidate_lease_epochs_stmt`` dedupes fleet-wide before windowing the deduped
    mint; a window-narrowed variant is ``_duration_lease_min_stmt``."""
    t = s.lease_facts
    return select(t.c.chunk_id, t.c.epoch, func.min(t.c.minted_at).label("minted_at")).group_by(t.c.chunk_id, t.c.epoch)


def _duration_lease_min_stmt(criteria: OperationalCriteria) -> Select[Any]:
    """``_lease_min_stmt``, narrowed to ``_duration_window_groups_stmt``'s own
    window-admitted ``(chunk_id, epoch)`` groups via a correlated subquery — only those
    groups ever feed the duration fold, so a bare fleet-wide scan on every
    ``durations/*`` request reads far more than the caller's window ever needs."""
    t = s.lease_facts
    groups = _duration_window_groups_stmt(criteria).subquery()
    stmt = select(t.c.chunk_id, t.c.epoch, func.min(t.c.minted_at).label("minted_at"))
    stmt = stmt.where(tuple_(t.c.chunk_id, t.c.epoch).in_(select(groups.c.chunk_id, groups.c.epoch)))
    return stmt.group_by(t.c.chunk_id, t.c.epoch)


def _source_chunks_stmt(source: str) -> Select[Any]:
    return select(s.chunk_work_refs.c.chunk_id).where(s.chunk_work_refs.c.source == source)


def _duration_window_groups_stmt(criteria: OperationalCriteria) -> Select[Any]:
    """Every distinct ``(chunk_id, epoch)`` with a matching runner-step transition in the
    window (D2) — which GROUPS the window admits, not which rows survive.
    ``_duration_rows_stmt`` fetches each group's full unwindowed history; ``steps_in_window``
    narrows the fold's output back down afterward."""
    t, gn = s.transitions, s.graph_nodes
    stmt = select(t.c.chunk_id, t.c.epoch).distinct().select_from(t.outerjoin(gn, t.c.from_node_id == gn.c.node_id))
    stmt = stmt.where(_runner_executed_or_entry(t, gn))
    if criteria.graph_id is not None:
        stmt = stmt.where(t.c.graph_id == criteria.graph_id)
    if criteria.source is not None:
        stmt = stmt.where(t.c.chunk_id.in_(_source_chunks_stmt(criteria.source)))
    if criteria.since is not None:  # the half-open [since, until) bound `steps_in_window` owns, in SQL form
        stmt = stmt.where(t.c.recorded_at >= criteria.since)
    if criteria.until is not None:
        stmt = stmt.where(t.c.recorded_at < criteria.until)
    return stmt


def _duration_rows_stmt(criteria: OperationalCriteria) -> Select[Any]:
    """Every completed *runner-executed* step (D2) for a window-admitted
    ``(chunk_id, epoch)`` group, unwindowed by time — narrowed via a correlated subquery
    over ``_duration_window_groups_stmt``, not a materialized id list, so the bind count
    stays independent of the caller's chunk fan-out (``bzh:sql-portable``). Outer-joined
    to ``graph_nodes`` so an entry transition (``from_node_id`` null) survives regardless."""
    t, gn = s.transitions, s.graph_nodes
    cols = (t.c.chunk_id, t.c.epoch, t.c.transition_id, t.c.from_node_id, t.c.to_node_id, t.c.graph_id, t.c.recorded_at)
    stmt = select(*cols).select_from(t.outerjoin(gn, t.c.from_node_id == gn.c.node_id))
    stmt = stmt.where(_runner_executed_or_entry(t, gn))
    groups = _duration_window_groups_stmt(criteria).subquery()
    stmt = stmt.where(tuple_(t.c.chunk_id, t.c.epoch).in_(select(groups.c.chunk_id, groups.c.epoch)))
    return stmt.order_by(t.c.chunk_id, t.c.epoch, t.c.recorded_at, t.c.transition_id)


def _to_transition_movement(row: Any) -> TransitionMovement:
    return TransitionMovement(
        chunk_id=row.chunk_id,
        epoch=row.epoch,
        transition_id=row.transition_id,
        from_node_id=row.from_node_id,
        to_node_id=row.to_node_id,
        graph_id=row.graph_id,
        recorded_at=row.recorded_at,
    )


def _decode_chunk_cursor(cursor: str) -> str:
    """``spend_by_chunk``'s whole cursor format: a plain chunk id — validated through the
    canonical id parser rather than a hand-rolled pattern, so a too-short or malformed
    value 422s instead of being accepted as a keyset offset."""
    parsed = Id.parse(cursor)
    if parsed is None or not parsed.has_prefix(CHUNK_PREFIX):
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
    """Usage/cost summed in SQL, grouped by ``group_col`` (D6) — the sums
    :meth:`~blizzard.hub.domain.work.UsageTotal.of_grouped_sums` applies the lower-bound
    + PARTIAL contract to: a null ``cost_usd`` is skipped from the sum (``coalesce``
    never substitutes a fabricated zero into the total itself), and ``null_cost_rows``
    counts how many of the group's rows lacked one."""
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
    """Deliberate deferral: each page re-runs a full ``GROUP BY`` with no supporting
    ``usage_facts.chunk_id`` index — adding one needs a migration, and this change
    carries none (D1); left for whatever next touches this table's schema."""
    u = s.usage_facts
    stmt = _spend_group_stmt(criteria, group_col=u.c.chunk_id)
    if cursor is not None:
        stmt = stmt.where(u.c.chunk_id > _decode_chunk_cursor(cursor))
    return stmt.limit(limit + 1)


def _to_spend_stats(row: Any) -> SpendStats:
    total = UsageTotal.of_grouped_sums(
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cache_read_tokens=row.cache_read_tokens,
        cache_create_tokens=row.cache_create_tokens,
        cost_usd_sum=row.cost_usd,
        null_cost_rows=row.null_cost_rows,
    )
    return SpendStats(key=row.key, total=total)


def _judged_distribution_stmt(criteria: OperationalCriteria) -> Select[Any]:
    """One row per ``(node, choice)`` matching ``criteria`` (D4) — only rows carrying a
    ``choice_name``, anti-joined against ``chunk_bounces`` on ``(chunk_id, epoch)`` so a
    kick-back's own same-epoch routing transition is excluded too (``hub_node.py``
    records both). A migration-completed step is not counted here either — a documented
    gap, see ``docs/deployment/analytics.md``."""
    t, b = s.transitions, s.chunk_bounces
    stmt = select(t.c.from_node_id, t.c.choice_name, func.count().label("occurrences"))
    stmt = stmt.select_from(t.outerjoin(b, (b.c.chunk_id == t.c.chunk_id) & (b.c.epoch == t.c.epoch)))
    stmt = stmt.where(t.c.from_node_id.is_not(None), t.c.choice_name.is_not(None), b.c.chunk_id.is_(None))
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
    and whose *deduped* mint falls in the window — filtered after the A7 dedup via the
    subquery wrap below, not before it, so the window can't pick a different duplicate as
    "earliest mint" than the dedupe alone would. The graph filter is not pushed here: it
    applies once D5 has derived a failed attempt's own graph."""
    sub = _lease_min_stmt().subquery()
    stmt = select(sub.c.chunk_id, sub.c.epoch, sub.c.minted_at)
    if criteria.source is not None:
        stmt = stmt.where(sub.c.chunk_id.in_(_source_chunks_stmt(criteria.source)))
    if criteria.since is not None:
        stmt = stmt.where(sub.c.minted_at >= criteria.since)
    if criteria.until is not None:
        stmt = stmt.where(sub.c.minted_at < criteria.until)
    return stmt


def _candidate_chunk_ids_stmt(criteria: OperationalCriteria) -> Select[Any]:
    """The candidate lease epochs' own distinct chunk ids, as a correlated subquery
    rather than a materialized id list — avoids binding one host parameter per id,
    unbounded by a caller's window with no `since`/`until`."""
    sub = _candidate_lease_epochs_stmt(criteria).subquery()
    return select(sub.c.chunk_id.distinct())


def _chunk_max_lease_epoch_stmt(criteria: OperationalCriteria) -> Select[Any]:
    """Each candidate chunk's own newest lease epoch, deliberately unwindowed by
    ``criteria`` — a lease minted after ``until`` (or before ``since``) still proves an
    in-window candidate epoch IS superseded (D5's positive end-of-attempt evidence), so
    whether it is over cannot itself be decided from inside the window."""
    t = s.lease_facts
    stmt = select(t.c.chunk_id, func.max(t.c.epoch).label("max_epoch"))
    return stmt.where(t.c.chunk_id.in_(_candidate_chunk_ids_stmt(criteria))).group_by(t.c.chunk_id)


def _chunk_transitions_stmt(criteria: OperationalCriteria) -> Select[Any]:
    """Every transition ever recorded for a candidate chunk — unfiltered by
    ``criteria``'s own window/graph: D5 resolves a failed attempt's node from whatever
    movement came before it, which can predate the window a caller asked about."""
    t = s.transitions
    cols = (t.c.chunk_id, t.c.epoch, t.c.transition_id, t.c.from_node_id, t.c.to_node_id, t.c.graph_id, t.c.recorded_at)
    return select(*cols).where(t.c.chunk_id.in_(_candidate_chunk_ids_stmt(criteria)))


def _chunk_migrations_stmt(criteria: OperationalCriteria) -> Select[Any]:
    m = s.chunk_migrations
    cols = (
        m.c.chunk_id,
        m.c.epoch,
        m.c.migration_id,
        m.c.landed_node_id,
        m.c.from_graph_id,
        m.c.to_graph_id,
        m.c.recorded_at,
    )
    return select(*cols).where(m.c.chunk_id.in_(_candidate_chunk_ids_stmt(criteria)))


def _chunk_bounces_stmt(criteria: OperationalCriteria) -> Select[Any]:
    b = s.chunk_bounces
    return select(b.c.chunk_id, b.c.epoch).where(b.c.chunk_id.in_(_candidate_chunk_ids_stmt(criteria)))


def _chunks_graph_stmt(criteria: OperationalCriteria) -> Select[Any]:
    c = s.chunks
    return select(c.c.chunk_id, c.c.graph_id).where(c.c.chunk_id.in_(_candidate_chunk_ids_stmt(criteria)))


def _candidate_graph_ids_stmt(criteria: OperationalCriteria) -> CompoundSelect[Any]:
    """Every graph id the outcomes fold might index into — a candidate chunk's own pin,
    or a migration's ``to``/``from_graph_id`` (D5's no-movement fallback can resolve via
    the latter) — as a correlated subquery, not a materialized id list, mirroring
    ``_candidate_chunk_ids_stmt``'s own reason: `graphs.graph_id` is a per-mint id, so an
    unwindowed request binds one parameter per graph version any chunk has ever run."""
    c, m = s.chunks, s.chunk_migrations
    chunk_ids = _candidate_chunk_ids_stmt(criteria)
    return union(
        select(c.c.graph_id).where(c.c.chunk_id.in_(chunk_ids)),
        select(m.c.to_graph_id).where(m.c.chunk_id.in_(_candidate_chunk_ids_stmt(criteria))),
        select(m.c.from_graph_id).where(m.c.chunk_id.in_(_candidate_chunk_ids_stmt(criteria))),
    )


def _graph_entry_nodes_stmt(criteria: OperationalCriteria) -> Select[Any]:
    g = s.graphs
    return select(g.c.graph_id, g.c.entry_node_id).where(g.c.graph_id.in_(_candidate_graph_ids_stmt(criteria)))


class AnalyticsOperationalStore:
    """Read-only operational-analytics query adapter over the hub store engine."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def durations_by_node(self, criteria: OperationalCriteria) -> list[DurationStats]:
        rows = self._step_durations(criteria)
        return summarize_durations(rows, key="node")

    def durations_by_graph(self, criteria: OperationalCriteria) -> list[DurationStats]:
        rows = self._step_durations(criteria)
        return summarize_durations(rows, key="graph")

    def _step_durations(self, criteria: OperationalCriteria) -> list[StepDuration]:
        """F6 (review round 4): no separate group-existence probe — an empty admitted-
        group set makes both statements below's correlated subquery empty too, so
        ``fold_step_durations`` already returns ``[]`` without a dedicated early-out."""
        with self._engine.connect() as conn:
            transition_rows = conn.execute(_duration_rows_stmt(criteria)).all()
            lease_rows = conn.execute(_duration_lease_min_stmt(criteria)).all()
        transitions = [_to_transition_movement(r) for r in transition_rows]
        lease_min_by_epoch = {(r.chunk_id, r.epoch): r.minted_at for r in lease_rows}
        all_rows = fold_step_durations(transitions, lease_min_by_epoch)
        return steps_in_window(all_rows, since=criteria.since, until=criteria.until, graph_id=criteria.graph_id)

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
        return ChunkSpendPage(records=[_to_spend_stats(row) for row in page_rows], next_cursor=next_cursor)

    def outcomes_by_node(self, criteria: OperationalCriteria) -> list[OutcomeStats]:
        with self._engine.connect() as conn:
            judged_rows = conn.execute(_judged_distribution_stmt(criteria)).all()
            lease_rows = conn.execute(_candidate_lease_epochs_stmt(criteria)).all()
            max_epoch_rows = conn.execute(_chunk_max_lease_epoch_stmt(criteria)).all()
            transition_rows = conn.execute(_chunk_transitions_stmt(criteria)).all()
            migration_rows = conn.execute(_chunk_migrations_stmt(criteria)).all()
            bounce_rows = conn.execute(_chunk_bounces_stmt(criteria)).all()
            chunk_graph_rows = conn.execute(_chunks_graph_stmt(criteria)).all()
            graph_entry_rows = conn.execute(_graph_entry_nodes_stmt(criteria)).all()

        judged = group_judged_choices(
            [
                JudgedChoiceRow(from_node_id=r.from_node_id, choice_name=r.choice_name, occurrences=r.occurrences)
                for r in judged_rows
            ]
        )
        failures = resolve_attempt_failures(
            lease_epochs=[LeaseEpoch(chunk_id=r.chunk_id, epoch=r.epoch, minted_at=r.minted_at) for r in lease_rows],
            transitions=[_to_transition_movement(r) for r in transition_rows],
            migrations=[
                MigrationMovement(
                    chunk_id=r.chunk_id,
                    epoch=r.epoch,
                    migration_id=r.migration_id,
                    landed_node_id=r.landed_node_id,
                    from_graph_id=r.from_graph_id,
                    to_graph_id=r.to_graph_id,
                    recorded_at=r.recorded_at,
                )
                for r in migration_rows
            ],
            bounced=[(r.chunk_id, r.epoch) for r in bounce_rows],
            chunk_graph={r.chunk_id: r.graph_id for r in chunk_graph_rows},
            chunk_max_lease_epoch={r.chunk_id: r.max_epoch for r in max_epoch_rows},
            graph_entry_node={r.graph_id: r.entry_node_id for r in graph_entry_rows},
            graph_id_filter=criteria.graph_id,
        )
        return summarize_outcomes(judged, failures)


def _conforms_analytics_operational_store(x: AnalyticsOperationalStore) -> IReadOperationalAnalytics:
    return x
