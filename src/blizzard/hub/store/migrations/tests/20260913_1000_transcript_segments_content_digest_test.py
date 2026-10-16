"""Tests proving the ``20260913_1000_transcript_segments_content_digest`` revision."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from tests.migration_support_store_migrations import (
    assert_frozen_table_lacks_a_later_revisions_columns,
)
from tests.support import (
    MIGRATION_CLOCK,
    hub_scoped_engine,
    seed_chunk,
    seed_graph,
)


def _row_digest(*, turn_range_start: int, rejected: bool, content: bytes | None) -> str:
    """Restates ``transcript_segment_store.content_digest``, frozen, so this test pins the
    migration's own backfill against a fixed formula rather than the production module."""
    digest = hashlib.sha256()
    digest.update(str(turn_range_start).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(b"1" if rejected else b"0")
    digest.update(b"\x00")
    digest.update(content or b"")
    digest.update(b"\x01")
    return digest.hexdigest()


def _digests_fingerprint(digests: list[str]) -> str:
    digest = hashlib.sha256()
    for d in digests:
        digest.update(d.encode("ascii"))
        digest.update(b"\x01")
    return digest.hexdigest()


def _old_formula_fingerprint(rows: list[tuple[int, bool, bytes]]) -> str:
    digest = hashlib.sha256()
    for turn_range_start, rejected, content in rows:
        digest.update(str(turn_range_start).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(b"1" if rejected else b"0")
        digest.update(b"\x00")
        digest.update(content or b"")
        digest.update(b"\x01")
    return digest.hexdigest()


@pytest.mark.unit
def test_transcript_segments_content_digest_backfills_and_carries_markers_forward(tmp_path: Path) -> None:
    """The backfill computes every digest from raw stored bytes with
    no decompression; a marker matching the retired whole-segment formula carries forward,
    one that doesn't stays stale for the next pass to correctly re-derive."""
    url = f"sqlite:///{tmp_path / 'store.db'}"
    runner = MigrationRunner(script_location=HUB_MIGRATIONS_DIR, url=url, clock=MIGRATION_CLOCK)
    runner.upgrade("20260907_1000_event_log_runner_id_nullable")

    rows = [(0, False, b"AAAA"), (1, False, b"BBBB")]
    engine = create_engine_from_url(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "insert into graphs (graph_id, name, entry_node_id, definition_yaml, created_at) "
                    "values ('gr_1', 'g', 'nd_build', '', :at)"
                ),
                {"at": "2026-08-01 00:00:00"},
            )
            conn.execute(
                sa.text("insert into chunks (chunk_id, graph_id, minted_at, model) values ('ch_1', 'gr_1', :at, '')"),
                {"at": "2026-08-01 00:00:00"},
            )
            for turn_range_start, rejected, content in rows:
                conn.execute(
                    sa.text(
                        "insert into transcript_segments (segment_id, chunk_id, node_id, epoch, spawn_generation, "
                        "runner_id, turn_range_start, turn_range_end, final, rejected, rejection_reason, byte_count, "
                        "codec, content, normalizer_version, harness_version, record_truncated, supersedes, received_at) "
                        "values ('sg_1', 'ch_1', 'nd_build', 1, 1, 'r1', :trs, :trs, :final, :rejected, NULL, 10, "
                        "'zlib', :content, 'norm/1', 'harness/1', 0, NULL, :at)"
                    ),
                    {
                        "trs": turn_range_start,
                        "final": 1 if turn_range_start == rows[-1][0] else 0,
                        "rejected": rejected,
                        "content": content,
                        "at": "2026-08-01 00:00:00",
                    },
                )
            # A marker whose stored fingerprint matches the retired whole-segment formula
            # over these current rows exactly — carried forward onto the new formula.
            conn.execute(
                sa.text(
                    "insert into transcript_event_derivations (segment_id, extractor_version, content_fingerprint, "
                    "derived_at, event_count, complete) values ('sg_1', 'v1', :fp, :at, 0, 1)"
                ),
                {"fp": _old_formula_fingerprint(rows), "at": "2026-08-01 00:00:00"},
            )
            # A second marker whose stored fingerprint does NOT match — left untouched.
            conn.execute(
                sa.text(
                    "insert into transcript_event_derivations (segment_id, extractor_version, content_fingerprint, "
                    "derived_at, event_count, complete) values ('sg_1', 'v2', 'stale-fp', :at, 0, 1)"
                ),
                {"at": "2026-08-01 00:00:00"},
            )
    finally:
        engine.dispose()

    runner.upgrade("head")

    engine = create_engine_from_url(url)
    try:
        with engine.connect() as conn:
            segment_rows = conn.execute(
                sa.text(
                    "select turn_range_start, rejected, content, content_digest from transcript_segments order by turn_range_start"
                )
            ).all()
            markers = {
                row.extractor_version: row.content_fingerprint
                for row in conn.execute(
                    sa.text("select extractor_version, content_fingerprint from transcript_event_derivations")
                )
            }
    finally:
        engine.dispose()

    expected_digests = [
        _row_digest(turn_range_start=r.turn_range_start, rejected=bool(r.rejected), content=r.content)
        for r in segment_rows
    ]
    assert [r.content_digest for r in segment_rows] == expected_digests

    assert markers["v1"] == _digests_fingerprint(expected_digests)  # carried forward
    assert markers["v2"] == "stale-fp"  # left stale — correctly re-derived next pass

    # downgrade() drops the column and leaves markers as they are.
    runner.downgrade("20260907_1000_event_log_runner_id_nullable")
    engine = create_engine_from_url(url)
    try:
        assert "content_digest" not in {c["name"] for c in sa.inspect(engine).get_columns("transcript_segments")}
    finally:
        engine.dispose()


@pytest.mark.unit
def test_transcript_segments_content_digest_resumes_after_an_interrupted_first_pass(tmp_path: Path) -> None:
    """A crash between `add_column` and the closing `alter_column(...,
    nullable=False)` leaves the column present but still nullable and unbackfilled; a
    retried `upgrade()` must finish the backfill and the NOT NULL close, not short-circuit
    on column presence alone the way a bare `_has_column` guard would."""
    url = f"sqlite:///{tmp_path / 'store.db'}"
    runner = MigrationRunner(script_location=HUB_MIGRATIONS_DIR, url=url, clock=MIGRATION_CLOCK)
    runner.upgrade("20260907_1000_event_log_runner_id_nullable")

    rows = [(0, False, b"AAAA"), (1, False, b"BBBB")]
    engine = create_engine_from_url(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "insert into graphs (graph_id, name, entry_node_id, definition_yaml, created_at) "
                    "values ('gr_1', 'g', 'nd_build', '', :at)"
                ),
                {"at": "2026-08-01 00:00:00"},
            )
            conn.execute(
                sa.text("insert into chunks (chunk_id, graph_id, minted_at, model) values ('ch_1', 'gr_1', :at, '')"),
                {"at": "2026-08-01 00:00:00"},
            )
            for turn_range_start, rejected, content in rows:
                conn.execute(
                    sa.text(
                        "insert into transcript_segments (segment_id, chunk_id, node_id, epoch, spawn_generation, "
                        "runner_id, turn_range_start, turn_range_end, final, rejected, rejection_reason, byte_count, "
                        "codec, content, normalizer_version, harness_version, record_truncated, supersedes, received_at) "
                        "values ('sg_1', 'ch_1', 'nd_build', 1, 1, 'r1', :trs, :trs, :final, :rejected, NULL, 10, "
                        "'zlib', :content, 'norm/1', 'harness/1', 0, NULL, :at)"
                    ),
                    {
                        "trs": turn_range_start,
                        "final": 1 if turn_range_start == rows[-1][0] else 0,
                        "rejected": rejected,
                        "content": content,
                        "at": "2026-08-01 00:00:00",
                    },
                )
            # Simulate a crash after this revision's own `add_column` but before its
            # backfill or closing NOT NULL ever ran: the column exists, is nullable, and
            # every row's digest is still unset.
            conn.execute(sa.text("alter table transcript_segments add column content_digest varchar"))
    finally:
        engine.dispose()

    cols_before = {c["name"]: c for c in sa.inspect(create_engine_from_url(url)).get_columns("transcript_segments")}
    assert cols_before["content_digest"]["nullable"] is True

    runner.upgrade("head")

    engine = create_engine_from_url(url)
    try:
        with engine.connect() as conn:
            segment_rows = conn.execute(
                sa.text(
                    "select turn_range_start, rejected, content, content_digest from transcript_segments order by turn_range_start"
                )
            ).all()
        cols_after = {c["name"]: c for c in sa.inspect(engine).get_columns("transcript_segments")}
    finally:
        engine.dispose()

    expected_digests = [
        _row_digest(turn_range_start=r.turn_range_start, rejected=bool(r.rejected), content=r.content)
        for r in segment_rows
    ]
    assert [r.content_digest for r in segment_rows] == expected_digests
    assert all(d is not None for d in expected_digests)
    assert cols_after["content_digest"]["nullable"] is False


@pytest.mark.component
def test_harness_provenance_backfills_concrete_session_rows_without_versions(tmp_path: Path) -> None:
    config = hub_runtime.init_environment(tmp_path)
    runner = hub_runtime.migration_runner(config)
    runner.downgrade("20260907_1000_event_log_runner_id_nullable")
    engine = hub_scoped_engine(create_engine_from_url(config.db_url))
    now = datetime(2026, 9, 11, tzinfo=UTC)
    try:
        old = sa.MetaData()
        questions = sa.Table("questions", old, autoload_with=engine)
        transcripts = sa.Table("transcript_segments", old, autoload_with=engine)
        with engine.begin() as conn:
            seed_graph(conn, "gr_1", at=now)
            seed_chunk(conn, "ch_1", graph_id="gr_1", at=now)
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


@pytest.mark.unit
@pytest.mark.parametrize(
    "tree,parent_revision,table,columns,direction",
    [
        pytest.param(
            "hub",
            "20260907_1000_event_log_runner_id_nullable",
            "transcript_segments",
            ("content_digest",),
            "added",
            id="hub.transcript_segments.content_digest",
        ),
    ],
)
def test_frozen_table_lacks_a_later_revisions_columns(
    tree: str, parent_revision: str, table: str, columns: tuple[str, ...], direction: str, tmp_path: Path
) -> None:
    assert_frozen_table_lacks_a_later_revisions_columns(tree, parent_revision, table, columns, direction, tmp_path)
