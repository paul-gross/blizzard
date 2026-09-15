"""Fact-table ``chunk_id`` indexes (blizzard#421) and the hot-path indexes blizzard#519
adds over the store's other unindexed predicates and orderings (component tier).

This pins a narrower claim than ``tests/test_store_read_index_gate.py``'s gate makes:
not just that a read avoids scanning, but *which named index* it plans through —
``tests/test_store_read_index_gate.py`` owns the general "every hub/runner read avoids
an unallowed scan" coverage; this file keeps the exact-named-index pins that gate does
not make. Migrated-to-head sqlite-on-disk. Proves every per-chunk fact-table read
``ChunkFactsStore.load_facts``/``_route_of_conn`` issue plans as an index search against its own
``ix_<table>_chunk_id`` rather than a full table scan — the ``tests/test_finding_store.py``
shape, over the table set the ``20260829_1930_fact_tables_chunk_id_index`` revision indexes,
plus blizzard#519's other hot-path reads and blizzard#517's spend range."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, Row
from sqlalchemy.dialects import sqlite

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.analytics.operational import OperationalCriteria
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.analytics_operational_store import _spend_by_node_stmt
from blizzard.hub.store.internal.chunk_events_store import (
    _bounded_stmt,
    _chunk_completed_activity_stmt,
    _chunk_deleted_activity_stmt,
    _chunk_grouped_stmt,
    _chunk_migrations_activity_stmt,
    _chunk_minted_stmt,
    _chunk_pause_facts_activity_stmt,
    _chunk_promoted_stmt,
    _chunk_restarts_activity_stmt,
    _chunk_stopped_activity_stmt,
    _decision_resolutions_activity_stmt,
    _decisions_activity_stmt,
    _deleted_chunk_ids_stmt,
    _escalations_activity_stmt,
    _question_answers_activity_stmt,
    _questions_activity_stmt,
    _requeues_activity_stmt,
    _route_created_activity_stmt,
    _route_released_activity_stmt,
    _transitions_activity_stmt,
)
from blizzard.hub.store.internal.transcript_event_store import _visible_segment_ids_stmt

from .support import seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)

# Mirrors the revision's own table list, minus `delivery_pr_opened` (covered by its own
# unique constraint) and the three blizzard#519 D5 composite-superseded tables
# (`transitions`, `lease_facts`, `chunk_bounces`) — those get their own exact-name
# assertion below, since a substring match here would also match their new
# `ix_<table>_chunk_id_epoch` composite.
_INDEXED_TABLES = (
    "chunk_migrations",
    "chunk_restarts",
    "escalations",
    "route_created",
    "route_released",
    "route_token_minted",
    "questions",
    "decisions",
    "requeues",
    "chunk_pause_facts",
    "usage_facts",
    "delivery_repo_landed",
    "hub_node_poll",
    "chunk_stopped",
    "chunk_completed",
    "delivery_pr_closed",
    "chunk_promoted",
    "delivery_landed",
)

# blizzard#519 D5: the three `(chunk_id, epoch)` composites that supersede a
# single-column `ix_<table>_chunk_id` index of the same table.
_CHUNK_ID_EPOCH_COMPOSITE_TABLES = ("transitions", "lease_facts", "chunk_bounces")


def _engine(tmp_path: Path) -> Engine:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        seed_graph(conn, "g1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="g1", at=_NOW)
    return engine


def _plan_uses_index(plan: Sequence[Row[Any]], index_name: str) -> bool:
    """Exact-name match (blizzard#519 D5): a substring check on ``ix_foo_chunk_id``
    also matches ``ix_foo_chunk_id_epoch``, so a dropped single-column index's name
    could still "pass" by riding its composite successor's plan text. `\\b` fails to
    end the match inside `_epoch` (`_` is a word character), so this only matches the
    index actually named in the plan."""
    pattern = re.compile(rf"\bUSING (?:COVERING )?INDEX {re.escape(index_name)}\b")
    return any(pattern.search(str(row)) for row in plan)


def _no_temp_btree_for_order_by(plan: Sequence[Row[Any]]) -> bool:
    """Neither a full nor a partial (``... FOR LAST TERM OF ORDER BY``) sort — sqlite emits
    the latter when an index covers the leading `ORDER BY` term but not a trailing
    tie-break column."""
    return not any("USE TEMP B-TREE FOR" in str(row) and "ORDER BY" in str(row) for row in plan)


@pytest.mark.parametrize("table", _INDEXED_TABLES)
def test_fact_table_chunk_id_read_plans_as_an_index_search(tmp_path: Path, table: str) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        plan = conn.execute(sa.text(f"EXPLAIN QUERY PLAN SELECT * FROM {table} WHERE chunk_id = 'ch_1'")).all()
    assert _plan_uses_index(plan, f"ix_{table}_chunk_id"), plan


@pytest.mark.parametrize("table", _CHUNK_ID_EPOCH_COMPOSITE_TABLES)
def test_chunk_id_epoch_composite_tables_plan_through_the_exact_composite(tmp_path: Path, table: str) -> None:
    """blizzard#519 D5: `ix_{table}_chunk_id` is dropped in favor of
    `ix_{table}_chunk_id_epoch`, a strict superset that still serves a plain `chunk_id`
    equality filter as its leading column."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        plan = conn.execute(sa.text(f"EXPLAIN QUERY PLAN SELECT * FROM {table} WHERE chunk_id = 'ch_1'")).all()
    assert _plan_uses_index(plan, f"ix_{table}_chunk_id_epoch"), plan
    assert not _plan_uses_index(plan, f"ix_{table}_chunk_id"), plan


def test_delivery_pr_opened_read_plans_as_an_index_search_on_its_own_unique_constraint(tmp_path: Path) -> None:
    """No `ix_delivery_pr_opened_chunk_id` exists — `uq_delivery_pr_opened_chunk_repo`
    already leads with `chunk_id`, so sqlite's own autoindex for that constraint already
    serves the filter and a fresh index would be redundant."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        plan = conn.execute(
            sa.text("EXPLAIN QUERY PLAN SELECT * FROM delivery_pr_opened WHERE chunk_id = 'ch_1'")
        ).all()
    assert any("SEARCH" in str(row) and "INDEX" in str(row) for row in plan), plan


def test_load_facts_answered_question_read_plans_as_an_index_search(tmp_path: Path) -> None:
    """`load_facts`'s `answered` read (blizzard#421) joins on `questions.chunk_id`, so it
    plans against `ix_questions_chunk_id` rather than an unfiltered join."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        plan = conn.execute(
            sa.text(
                "EXPLAIN QUERY PLAN SELECT question_answers.question_id FROM question_answers "
                "JOIN questions ON questions.question_id = question_answers.question_id "
                "WHERE questions.chunk_id = 'ch_1'"
            )
        ).all()
    assert any("ix_questions_chunk_id" in str(row) for row in plan), plan


# --- blizzard#519: the hot-path reads named in its acceptance criteria --------------

# (label, sql, index name the read must plan through)
_HOT_PATH_INDEX_SEARCHES = (
    ("artifacts by chunk", "SELECT * FROM artifacts WHERE chunk_id = 'ch_1'", "ix_artifacts_chunk_id_node_id_epoch"),
    ("graph_choices by node", "SELECT * FROM graph_choices WHERE node_id = 'nd_1'", "ix_graph_choices_node_id"),
    (
        "chunk_work_refs by chunk",
        "SELECT * FROM chunk_work_refs WHERE chunk_id = 'ch_1'",
        "ix_chunk_work_refs_chunk_id",
    ),
    (
        "chunk_work_refs by (source, ref)",
        "SELECT chunk_id FROM chunk_work_refs WHERE source = 'github' AND ref = '1'",
        "ix_chunk_work_refs_source_ref",
    ),
    (
        "close_intents pending",
        "SELECT chunk_id, source, ref FROM close_intents WHERE retired_at IS NULL ORDER BY id",
        "ix_close_intents_pending",
    ),
)


@pytest.mark.parametrize(
    "label, sql, index_name", _HOT_PATH_INDEX_SEARCHES, ids=[c[0] for c in _HOT_PATH_INDEX_SEARCHES]
)
def test_hot_path_read_plans_as_an_index_search(tmp_path: Path, label: str, sql: str, index_name: str) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        plan = conn.execute(sa.text(f"EXPLAIN QUERY PLAN {sql}")).all()
    assert _plan_uses_index(plan, index_name), (label, plan)


def test_spend_by_node_plans_through_the_node_index_when_unfiltered(tmp_path: Path) -> None:
    """`ix_usage_facts_node_id` serves `spend_by_node`'s `GROUP BY` with no scan and no
    temp B-tree when the caller passes no date range (blizzard#519 F11)."""
    engine = _engine(tmp_path)
    compiled = str(
        _spend_by_node_stmt(OperationalCriteria()).compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    with engine.connect() as conn:
        plan = conn.execute(sa.text(f"EXPLAIN QUERY PLAN {compiled}")).all()
    assert _plan_uses_index(plan, "ix_usage_facts_node_id"), plan
    assert not any("TEMP B-TREE" in str(row) for row in plan), plan


def test_spend_by_node_prefers_the_range_index_and_sorts_for_group_by_when_windowed(tmp_path: Path) -> None:
    """A windowed `spend_by_node` call plans through `ix_usage_facts_recorded_at` for the
    range instead — `ix_usage_facts_node_id` goes unused and the `GROUP BY` falls back to
    a temp B-tree (blizzard#519 F11). Accepted, not fixed: the range is the more
    selective predicate on a windowed call, and no case elsewhere pins this trade-off."""
    engine = _engine(tmp_path)
    criteria = OperationalCriteria(since=_NOW, until=_NOW)
    compiled = str(
        _spend_by_node_stmt(criteria).compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True})
    )
    with engine.connect() as conn:
        plan = conn.execute(sa.text(f"EXPLAIN QUERY PLAN {compiled}")).all()
    assert _plan_uses_index(plan, "ix_usage_facts_recorded_at"), plan
    assert any("TEMP B-TREE FOR GROUP BY" in str(row) for row in plan), plan


def test_visible_segment_ids_supersedes_subquery_plans_as_an_index_search(tmp_path: Path) -> None:
    """`_visible_segment_ids_stmt`'s `NOT IN (SELECT supersedes ...)` subquery
    (blizzard#519) — the outer `final = TRUE` filter plans through
    `ix_transcript_segments_final_chunk_id`, and the subquery's own `supersedes IS NOT
    NULL` scan plans through `ix_transcript_segments_supersedes`. Compiles the real
    statement rather than a hand-written mirror, so the two can't silently diverge."""
    engine = _engine(tmp_path)
    compiled = str(
        _visible_segment_ids_stmt().compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True})
    )
    with engine.connect() as conn:
        plan = conn.execute(sa.text(f"EXPLAIN QUERY PLAN {compiled}")).all()
    assert _plan_uses_index(plan, "ix_transcript_segments_final_chunk_id"), plan
    assert _plan_uses_index(plan, "ix_transcript_segments_supersedes"), plan


# --- blizzard#519 D6: activity_facts_since's per-source `_bounded` read -------------

# (label, stmt fn, ts_col, pk_col, index name) — the real per-source base-statement
# builder `ChunkEventsStore.activity_facts_since` calls, so the compiled statement
# includes the same joins and `chunk_deleted` anti-join production issues, not a
# hand-written mirror that could diverge (blizzard#519 F6).
_ACTIVITY_FEED_SOURCES = (
    ("chunks", _chunk_minted_stmt, s.chunks.c.minted_at, s.chunks.c.chunk_id, "ix_chunks_minted_at_chunk_id"),
    (
        "transitions",
        _transitions_activity_stmt,
        s.transitions.c.recorded_at,
        s.transitions.c.transition_id,
        "ix_transitions_recorded_at_transition_id",
    ),
    (
        "route_created",
        _route_created_activity_stmt,
        s.route_created.c.created_at,
        s.route_created.c.route_id,
        "ix_route_created_created_at_route_id",
    ),
    (
        "chunk_migrations",
        _chunk_migrations_activity_stmt,
        s.chunk_migrations.c.recorded_at,
        s.chunk_migrations.c.migration_id,
        "ix_chunk_migrations_recorded_at_migration_id",
    ),
    (
        "decisions",
        _decisions_activity_stmt,
        s.decisions.c.submitted_at,
        s.decisions.c.decision_id,
        "ix_decisions_submitted_at_decision_id",
    ),
    (
        "decision_resolutions",
        _decision_resolutions_activity_stmt,
        s.decision_resolutions.c.resolved_at,
        s.decision_resolutions.c.decision_id,
        "ix_decision_resolutions_resolved_at_decision_id",
    ),
    (
        "questions",
        _questions_activity_stmt,
        s.questions.c.asked_at,
        s.questions.c.question_id,
        "ix_questions_asked_at_question_id",
    ),
    (
        "question_answers",
        _question_answers_activity_stmt,
        s.question_answers.c.answered_at,
        s.question_answers.c.question_id,
        "ix_question_answers_answered_at_question_id",
    ),
    (
        "chunk_promoted",
        _chunk_promoted_stmt,
        s.chunk_promoted.c.promoted_at,
        s.chunk_promoted.c.id,
        "ix_chunk_promoted_promoted_at_id",
    ),
    (
        "chunk_grouped",
        lambda d: _chunk_grouped_stmt(),
        s.chunk_grouped.c.grouped_at,
        s.chunk_grouped.c.id,
        "ix_chunk_grouped_grouped_at_id",
    ),
    (
        "chunk_restarts",
        _chunk_restarts_activity_stmt,
        s.chunk_restarts.c.recorded_at,
        s.chunk_restarts.c.id,
        "ix_chunk_restarts_recorded_at_id",
    ),
    (
        "escalations",
        _escalations_activity_stmt,
        s.escalations.c.recorded_at,
        s.escalations.c.id,
        "ix_escalations_recorded_at_id",
    ),
    (
        "requeues",
        _requeues_activity_stmt,
        s.requeues.c.requeued_at,
        s.requeues.c.id,
        "ix_requeues_requeued_at_id",
    ),
    (
        "route_released",
        _route_released_activity_stmt,
        s.route_released.c.released_at,
        s.route_released.c.id,
        "ix_route_released_released_at_id",
    ),
    (
        "chunk_pause_facts",
        _chunk_pause_facts_activity_stmt,
        s.chunk_pause_facts.c.set_at,
        s.chunk_pause_facts.c.id,
        "ix_chunk_pause_facts_set_at_id",
    ),
    (
        "chunk_stopped",
        _chunk_stopped_activity_stmt,
        s.chunk_stopped.c.stopped_at,
        s.chunk_stopped.c.id,
        "ix_chunk_stopped_stopped_at_id",
    ),
    (
        "chunk_completed",
        _chunk_completed_activity_stmt,
        s.chunk_completed.c.completed_at,
        s.chunk_completed.c.id,
        "ix_chunk_completed_completed_at_id",
    ),
    (
        "chunk_deleted",
        lambda d: _chunk_deleted_activity_stmt(),
        s.chunk_deleted.c.deleted_at,
        s.chunk_deleted.c.id,
        "ix_chunk_deleted_deleted_at_id",
    ),
)


@pytest.mark.parametrize(
    "table, stmt_fn, ts_col, pk_col, index_name", _ACTIVITY_FEED_SOURCES, ids=[c[0] for c in _ACTIVITY_FEED_SOURCES]
)
def test_activity_feed_source_bounded_read_plans_as_an_index_search_with_no_sort(
    tmp_path: Path, table: str, stmt_fn: Any, ts_col: Any, pk_col: Any, index_name: str
) -> None:
    engine = _engine(tmp_path)
    since = datetime(2020, 1, 1, tzinfo=UTC)
    stmt = _bounded_stmt(stmt_fn(_deleted_chunk_ids_stmt()), ts_col=ts_col, pk_col=pk_col, since=since, limit=50)
    compiled = str(stmt.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}))
    with engine.connect() as conn:
        plan = conn.execute(sa.text(f"EXPLAIN QUERY PLAN {compiled}")).all()
    assert _plan_uses_index(plan, index_name), (table, plan)
    assert _no_temp_btree_for_order_by(plan), (table, plan)
