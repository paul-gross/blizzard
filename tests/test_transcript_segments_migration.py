"""The transcript-segments migration (blizzard#247, Phase 1 — component tier): applies
from an empty store to head, and survives a downgrade/upgrade roundtrip."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime

pytestmark = pytest.mark.component


def _table_names(db_url: str) -> set[str]:
    engine = create_engine_from_url(db_url)
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_transcript_tables_exist_after_a_fresh_migration_to_head(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path)  # upgrades an empty store to head
    tables = _table_names(config.db_url)
    assert "transcript_segments" in tables
    assert "transcript_high_water" in tables


def test_transcript_tables_survive_a_downgrade_upgrade_roundtrip(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path)
    runner = hub_runtime.migration_runner(config)

    runner.downgrade("20260803_1000_hub_escalation_wrapped_takeover")
    tables = _table_names(config.db_url)
    assert "transcript_segments" not in tables
    assert "transcript_high_water" not in tables

    runner.upgrade("head")
    tables = _table_names(config.db_url)
    assert "transcript_segments" in tables
    assert "transcript_high_water" in tables


def test_questions_historical_revision_does_not_create_later_harness_provenance(tmp_path: Path) -> None:
    """The later provenance migration must see a column to add and backfill itself."""
    config = hub_runtime.init_environment(tmp_path)
    runner = hub_runtime.migration_runner(config)
    runner.downgrade("20260713_1716_hub_graph_node_produces_checks")

    runner.upgrade("20260713_1801_hub_questions_and_answers")
    engine = create_engine_from_url(config.db_url)
    try:
        columns = {column["name"] for column in sa.inspect(engine).get_columns("questions")}
    finally:
        engine.dispose()

    assert "harness_id" not in columns


def test_harness_provenance_backfills_concrete_session_rows_without_versions(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path)
    runner = hub_runtime.migration_runner(config)
    runner.downgrade("20260907_1000_event_log_runner_id_nullable")
    engine = create_engine_from_url(config.db_url)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    try:
        old = sa.MetaData()
        questions = sa.Table("questions", old, autoload_with=engine)
        transcripts = sa.Table("transcript_segments", old, autoload_with=engine)
        with engine.begin() as conn:
            conn.execute(
                questions.insert(),
                [
                    {
                        "question_id": "qn_session",
                        "chunk_id": "ch_1",
                        "node_id": None,
                        "session_id": "session_1",
                        "runner_id": "r1",
                        "epoch": 1,
                        "question": "continue?",
                        "options": "[]",
                        "asked_at": now,
                    },
                    {
                        "question_id": "qn_without_session",
                        "chunk_id": "ch_1",
                        "node_id": None,
                        "session_id": None,
                        "runner_id": "r1",
                        "epoch": 1,
                        "question": "continue?",
                        "options": "[]",
                        "asked_at": now,
                    },
                ],
            )
            conn.execute(
                transcripts.insert().values(
                    segment_id="sg_1",
                    chunk_id="ch_1",
                    node_id="nd_1",
                    epoch=1,
                    spawn_generation=1,
                    runner_id="r1",
                    turn_range_start=0,
                    turn_range_end=0,
                    final=True,
                    rejected=True,
                    rejection_reason="record_too_large",
                    byte_count=0,
                    codec=None,
                    content=None,
                    normalizer_version="normalizer/1",
                    harness_version=None,
                    record_truncated=False,
                    supersedes=None,
                    received_at=now,
                )
            )
        runner.upgrade("head")
        current = sa.MetaData()
        questions = sa.Table("questions", current, autoload_with=engine)
        transcripts = sa.Table("transcript_segments", current, autoload_with=engine)
        with engine.connect() as conn:
            owner_by_question = {
                row.question_id: row.harness_id
                for row in conn.execute(sa.select(questions.c.question_id, questions.c.harness_id))
            }
            transcript = conn.execute(
                sa.select(transcripts.c.harness_id, transcripts.c.harness_version, transcripts.c.normalizer_version)
            ).one()
        assert owner_by_question == {"qn_session": "claude_code", "qn_without_session": None}
        assert transcript == ("claude_code", None, "normalizer/1")
    finally:
        engine.dispose()
