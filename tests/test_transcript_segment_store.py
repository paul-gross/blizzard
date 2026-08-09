"""Transcript-segment store: dialect-portable DDL, the codec round-trip, and the
natural-key uniqueness the schema enforces (blizzard#247, Phase 1 — unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.transcripts import SegmentRecord
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 9, tzinfo=UTC)


def _record(**overrides: object) -> SegmentRecord:
    values: dict[str, object] = {
        "segment_id": "sg_1",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "runner_id": "r1",
        "turn_range_start": 0,
        "turn_range_end": 9,
        "final": True,
        "normalizer_version": "v1",
        "harness_version": "claude-code-1.0",
        "turns_json": '[{"index": 0, "kind": "asst"}]',
    }
    values.update(overrides)
    return SegmentRecord(**values)  # type: ignore[arg-type]


def _migrated_engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    return create_engine_from_url(db_url)


# --- dialect-portable DDL and cap-accounting selects -------------------------


def test_transcript_segments_ddl_compiles_under_both_dialects() -> None:
    from sqlalchemy.schema import CreateTable

    for dialect in (postgresql.dialect(), sqlite.dialect()):
        sql = str(CreateTable(s.transcript_segments).compile(dialect=dialect))
        assert "transcript_segments" in sql


def test_cap_accounting_selects_compile_under_both_dialects() -> None:
    chunk_budget = select(func.coalesce(func.sum(s.transcript_segments.c.byte_count), 0)).where(
        s.transcript_segments.c.chunk_id == "ch_1", s.transcript_segments.c.rejected.is_(False)
    )
    daily_rate = select(func.coalesce(func.sum(s.transcript_segments.c.byte_count), 0)).where(
        s.transcript_segments.c.runner_id == "r1", s.transcript_segments.c.received_at >= _NOW
    )
    for stmt in (chunk_budget, daily_rate):
        pg_sql = str(stmt.compile(dialect=postgresql.dialect()))
        sqlite_sql = str(stmt.compile(dialect=sqlite.dialect()))
        assert "coalesce" in pg_sql.lower()
        assert "coalesce" in sqlite_sql.lower()


# --- codec round-trip ---------------------------------------------------------


def test_turn_content_round_trips_through_the_codec(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    record = _record()

    store.insert_accepted(record, byte_count=len(record.turns_json.encode("utf-8")), codec="zlib", at=_NOW)

    [content] = store.records_for_segment("ch_1", "sg_1")
    assert content.turns_json == record.turns_json
    with engine.connect() as conn:
        row = conn.execute(select(s.transcript_segments.c.codec)).one()
    assert row.codec == "zlib"


def test_a_rejected_record_stores_no_content_or_codec(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    record = _record()

    store.insert_rejected(record, byte_count=999, reason="record_too_large", at=_NOW)

    with engine.connect() as conn:
        row = conn.execute(select(s.transcript_segments)).one()
    assert row.content is None
    assert row.codec is None
    assert row.rejected is True
    assert row.rejection_reason == "record_too_large"
    assert row.byte_count == 999


# --- natural key ---------------------------------------------------------------


def test_the_natural_key_is_enforced_by_the_schema(tmp_path: Path) -> None:
    """D8: ``(segment_id, turn_range_start)`` is a schema-level unique constraint —
    a second insert at the same key must fail even if a caller forgot to check first."""
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    record = _record()
    store.insert_accepted(record, byte_count=10, codec="zlib", at=_NOW)

    with pytest.raises(IntegrityError):
        store.insert_accepted(record, byte_count=10, codec="zlib", at=_NOW)


def test_natural_key_state_is_absent_then_accepted_for_a_stored_pair(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    assert store.natural_key_state("sg_1", 0) == "absent"

    store.insert_accepted(_record(), byte_count=10, codec="zlib", at=_NOW)

    assert store.natural_key_state("sg_1", 0) == "accepted"
    assert store.natural_key_state("sg_1", 5) == "absent"


def test_natural_key_state_is_rejected_for_a_cap_rejected_pair(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    store.insert_rejected(_record(), byte_count=999, reason="record_too_large", at=_NOW)

    assert store.natural_key_state("sg_1", 0) == "rejected"


def test_update_to_accepted_transitions_a_rejected_row_in_place(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    record = _record()
    store.insert_rejected(record, byte_count=999, reason="record_too_large", at=_NOW)

    store.update_to_accepted(record, byte_count=10, codec="zlib", at=_NOW)

    assert store.natural_key_state("sg_1", 0) == "accepted"
    [content] = store.records_for_segment("ch_1", "sg_1")
    assert content.rejected is False
    assert content.turns_json == record.turns_json
    with engine.connect() as conn:
        rows = conn.execute(select(s.transcript_segments)).all()
    assert len(rows) == 1


# --- lease reads (D2, issue #249) -----------------------------------------------


def test_runner_id_for_lease_is_none_when_the_lease_holds_no_segments(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)

    assert store.runner_id_for_lease("ch_1", "nd_build", 1) is None


def test_runner_id_for_lease_resolves_to_the_shipping_runner(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    store.insert_accepted(_record(), byte_count=10, codec="zlib", at=_NOW)

    assert store.runner_id_for_lease("ch_1", "nd_build", 1) == "r1"


def test_records_for_lease_spans_every_spawn_generation_but_not_other_leases(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    first_spawn = _record(segment_id="sg_1", spawn_generation=1, turn_range_start=0, turn_range_end=0)
    second_spawn = _record(segment_id="sg_2", spawn_generation=2, turn_range_start=1, turn_range_end=1)
    other_epoch = _record(segment_id="sg_3", epoch=2, turn_range_start=0, turn_range_end=0)
    other_node = _record(segment_id="sg_4", node_id="nd_other", turn_range_start=0, turn_range_end=0)
    other_runner = _record(segment_id="sg_5", runner_id="r2", turn_range_start=0, turn_range_end=0)
    for record in (first_spawn, second_spawn, other_epoch, other_node, other_runner):
        store.insert_accepted(record, byte_count=10, codec="zlib", at=_NOW)

    records = store.records_for_lease("ch_1", "nd_build", 1, "r1")

    assert [r.turn_range_start for r in records] == [0, 1]


def test_records_for_lease_confines_to_the_declared_runner(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    store.insert_accepted(_record(), byte_count=10, codec="zlib", at=_NOW)

    assert store.records_for_lease("ch_1", "nd_build", 1, "r2") == []


def test_update_still_rejected_refreshes_the_row_without_storing_content(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    record = _record()
    store.insert_rejected(record, byte_count=999, reason="record_too_large", at=_NOW)
    later = _NOW.replace(hour=12)

    store.update_still_rejected(record, byte_count=1200, reason="record_too_large", at=later)

    with engine.connect() as conn:
        rows = conn.execute(select(s.transcript_segments)).all()
    assert len(rows) == 1
    assert rows[0].content is None
    assert rows[0].byte_count == 1200
    assert rows[0].received_at == later
