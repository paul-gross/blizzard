"""Fact-table ``chunk_id`` indexes (blizzard#421) and the hot-path indexes blizzard#519
adds over the store's other unindexed predicates and orderings (component tier).

Migrated-to-head sqlite-on-disk. Proves every per-chunk fact-table read
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

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

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
    return not any("USE TEMP B-TREE FOR ORDER BY" in str(row) for row in plan)


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


def test_visible_segment_ids_supersedes_subquery_plans_as_an_index_search(tmp_path: Path) -> None:
    """`_visible_segment_ids_stmt`'s `NOT IN (SELECT supersedes ...)` subquery
    (blizzard#519) — the outer `final = TRUE` filter plans through
    `ix_transcript_segments_final_chunk_id`, and the subquery's own `supersedes IS NOT
    NULL` scan plans through `ix_transcript_segments_supersedes`."""
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        plan = conn.execute(
            sa.text(
                "EXPLAIN QUERY PLAN SELECT DISTINCT segment_id FROM transcript_segments "
                "WHERE final = 1 "
                "AND segment_id NOT IN (SELECT supersedes FROM transcript_segments WHERE supersedes IS NOT NULL) "
                "AND chunk_id IN (SELECT chunk_id FROM chunks)"
            )
        ).all()
    assert _plan_uses_index(plan, "ix_transcript_segments_final_chunk_id"), plan
    assert _plan_uses_index(plan, "ix_transcript_segments_supersedes"), plan


# --- blizzard#519 D6: activity_facts_since's per-source `_bounded` read -------------

# (table, ts_col, pk_col, index name) — mirrors `ChunkEventsStore.activity_facts_since`'s
# per-source `_bounded` call: `WHERE ts_col >= :since ORDER BY ts_col DESC, pk_col DESC`.
_ACTIVITY_FEED_SOURCES = (
    ("chunks", "minted_at", "chunk_id", "ix_chunks_minted_at_chunk_id"),
    ("transitions", "recorded_at", "transition_id", "ix_transitions_recorded_at_transition_id"),
    ("route_created", "created_at", "route_id", "ix_route_created_created_at_route_id"),
    ("chunk_migrations", "recorded_at", "migration_id", "ix_chunk_migrations_recorded_at_migration_id"),
    ("decisions", "submitted_at", "decision_id", "ix_decisions_submitted_at_decision_id"),
    (
        "decision_resolutions",
        "resolved_at",
        "decision_id",
        "ix_decision_resolutions_resolved_at_decision_id",
    ),
    ("questions", "asked_at", "question_id", "ix_questions_asked_at_question_id"),
    ("question_answers", "answered_at", "question_id", "ix_question_answers_answered_at_question_id"),
    ("chunk_promoted", "promoted_at", "id", "ix_chunk_promoted_promoted_at"),
    ("chunk_grouped", "grouped_at", "id", "ix_chunk_grouped_grouped_at"),
    ("chunk_restarts", "recorded_at", "id", "ix_chunk_restarts_recorded_at"),
    ("escalations", "recorded_at", "id", "ix_escalations_recorded_at"),
    ("requeues", "requeued_at", "id", "ix_requeues_requeued_at"),
    ("route_released", "released_at", "id", "ix_route_released_released_at"),
    ("chunk_pause_facts", "set_at", "id", "ix_chunk_pause_facts_set_at"),
    ("chunk_stopped", "stopped_at", "id", "ix_chunk_stopped_stopped_at"),
    ("chunk_completed", "completed_at", "id", "ix_chunk_completed_completed_at"),
    ("chunk_deleted", "deleted_at", "id", "ix_chunk_deleted_deleted_at"),
)


@pytest.mark.parametrize(
    "table, ts_col, pk_col, index_name", _ACTIVITY_FEED_SOURCES, ids=[c[0] for c in _ACTIVITY_FEED_SOURCES]
)
def test_activity_feed_source_bounded_read_plans_as_an_index_search_with_no_sort(
    tmp_path: Path, table: str, ts_col: str, pk_col: str, index_name: str
) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        plan = conn.execute(
            sa.text(
                f"EXPLAIN QUERY PLAN SELECT * FROM {table} "
                f"WHERE {ts_col} >= '2020-01-01T00:00:00' "
                f"ORDER BY {ts_col} DESC, {pk_col} DESC LIMIT 50"
            )
        ).all()
    assert _plan_uses_index(plan, index_name), (table, plan)
    assert _no_temp_btree_for_order_by(plan), (table, plan)
