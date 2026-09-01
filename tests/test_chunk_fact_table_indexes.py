"""Fact-table ``chunk_id`` indexes (blizzard#421, component tier).

Migrated-to-head sqlite-on-disk. Proves every per-chunk fact-table read
``ChunkFactsStore.load_facts``/``_route_of_conn`` issue plans as an index search against its own
``ix_<table>_chunk_id`` rather than a full table scan — the ``tests/test_finding_store.py``
shape, over the table set the ``20260829_1930_fact_tables_chunk_id_index`` revision indexes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner

from .support import seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)

# Mirrors the revision's own table list, minus `delivery_pr_opened` (covered by its
# own unique constraint).
_INDEXED_TABLES = (
    "transitions",
    "chunk_migrations",
    "chunk_restarts",
    "lease_facts",
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
    "chunk_bounces",
    "hub_node_poll",
    "chunk_stopped",
    "chunk_completed",
    "delivery_pr_closed",
    "chunk_promoted",
    "delivery_landed",
)


def _engine(tmp_path: Path) -> Engine:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        seed_graph(conn, "g1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="g1", at=_NOW)
    return engine


@pytest.mark.parametrize("table", _INDEXED_TABLES)
def test_fact_table_chunk_id_read_plans_as_an_index_search(tmp_path: Path, table: str) -> None:
    engine = _engine(tmp_path)
    with engine.connect() as conn:
        plan = conn.execute(sa.text(f"EXPLAIN QUERY PLAN SELECT * FROM {table} WHERE chunk_id = 'ch_1'")).all()
    assert any(f"ix_{table}_chunk_id" in str(row) for row in plan), plan


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
