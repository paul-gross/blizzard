"""Transcript-segment store: dialect-portable DDL, the codec round-trip, and the
natural-key uniqueness the schema enforces (blizzard#247, Phase 1 — unit tier)."""

from __future__ import annotations

import ast
import json
import zlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import ClauseElement, visitors
from sqlalchemy.sql.elements import TextClause

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.transcripts import SegmentRecord
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import transcript_segment_store as store_module
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
        "record_truncated": False,
        "turns_json": '[{"index": 0, "kind": "asst"}]',
    }
    values.update(overrides)
    return SegmentRecord(**values)  # type: ignore[arg-type]


def _migrated_engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    return create_engine_from_url(db_url)


# --- dialect-portable DDL and the statements the store itself executes -------


def _executed_statements() -> dict[str, ClauseElement]:
    """The store's OWN statements — a look-alike re-declared here would keep compiling
    while the product's drifted off the portable surface."""
    record = _record()
    m = store_module
    return {
        "_segments_for_chunk_stmt": m._segments_for_chunk_stmt("ch_1"),
        "_records_for_segment_stmt": m._records_for_segment_stmt("ch_1", "sg_1"),
        "_lease_runner_ids_stmt": m._lease_runner_ids_stmt("ch_1", "nd_build", 1),
        "_records_for_lease_stmt": m._records_for_lease_stmt("ch_1", "nd_build", 1, "r1"),
        "_high_water_stmt": m._high_water_stmt("r1"),
        "_high_water_owner_stmt": m._high_water_owner_stmt("r1"),
        "_natural_key_state_stmt": m._natural_key_state_stmt("sg_1", 0),
        "_chunk_stored_bytes_stmt": m._chunk_stored_bytes_stmt("ch_1"),
        "_runner_window_bytes_stmt": m._runner_window_bytes_stmt("r1", _NOW),
        "_insert_high_water_stmt": m._insert_high_water_stmt("r1", seq=7, at=_NOW),
        "_update_high_water_stmt": m._update_high_water_stmt("r1", seq=7, at=_NOW),
        "_insert_accepted_stmt": m._insert_accepted_stmt(record, byte_count=10, codec="zlib", content=b"z", at=_NOW),
        "_insert_rejected_stmt": m._insert_rejected_stmt(record, byte_count=999, reason="record_too_large", at=_NOW),
        "_update_to_accepted_stmt": m._update_to_accepted_stmt(
            record, byte_count=10, codec="zlib", content=b"z", at=_NOW
        ),
        "_update_still_rejected_stmt": m._update_still_rejected_stmt(
            record, byte_count=999, reason="record_too_large", at=_NOW
        ),
    }


def test_transcript_segments_ddl_compiles_under_both_dialects() -> None:
    from sqlalchemy.schema import CreateTable

    for dialect in (postgresql.dialect(), sqlite.dialect()):
        sql = str(CreateTable(s.transcript_segments).compile(dialect=dialect))
        assert "transcript_segments" in sql


def test_transcript_high_water_ddl_compiles_under_both_dialects() -> None:
    from sqlalchemy.schema import CreateTable

    for dialect in (postgresql.dialect(), sqlite.dialect()):
        sql = str(CreateTable(s.transcript_high_water).compile(dialect=dialect))
        assert "transcript_high_water" in sql


def test_every_statement_the_store_executes_compiles_under_both_dialects() -> None:
    for name, stmt in _executed_statements().items():
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            assert "transcript_" in str(stmt.compile(dialect=dialect)), name


def test_no_statement_the_store_executes_leaves_the_portable_surface() -> None:
    """`bzh:sql-portable`'s two escape hatches a both-dialects compile cannot see: raw
    `text()` SQL (opaque to every compiler) and a dialect-namespaced construct."""
    for name, stmt in _executed_statements().items():
        assert type(stmt).__module__.startswith("sqlalchemy.sql."), name
        assert not [e for e in visitors.iterate(stmt) if isinstance(e, TextClause)], name


def test_the_compile_sweep_reaches_every_statement_the_store_can_execute() -> None:
    builders = {name for name in vars(store_module) if name.endswith("_stmt")}
    assert builders == set(_executed_statements())

    source = ast.parse(Path(store_module.__file__ or "").read_text())
    executed = [
        node.args[0]
        for node in ast.walk(source)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"
    ]
    assert len(executed) == len(builders)
    for arg in executed:
        built_by_a_builder = (
            isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id.endswith("_stmt")
        )
        assert built_by_a_builder, ast.unparse(arg)


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


def test_turn_content_is_compressed_at_rest_not_stored_as_plaintext(tmp_path: Path) -> None:
    """AC1/D10: the round-trip above cannot tell zlib from a no-op codec, so this reads
    the raw column — highly compressible turns must land far under their plaintext."""
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    turns = [{"index": i, "kind": "asst", "text": "the same sentence, over and over. " * 40} for i in range(64)]
    plaintext = json.dumps(turns).encode("utf-8")
    record = _record(turns_json=plaintext.decode("utf-8"))

    store.insert_accepted(record, byte_count=len(plaintext), codec="zlib", at=_NOW)

    with engine.connect() as conn:
        row = conn.execute(select(s.transcript_segments.c.content)).one()
    assert row.content != plaintext
    assert zlib.decompress(row.content) == plaintext
    assert len(row.content) * 10 < len(plaintext)


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


def test_update_to_accepted_carries_the_re_offers_own_truncated_flag(tmp_path: Path) -> None:
    """review F10: a natural-key re-offer must not keep the FIRST offer's flag — the worse,
    later offer's own truth wins, not a stale one."""
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    first = _record(record_truncated=True)
    store.insert_rejected(first, byte_count=999, reason="record_too_large", at=_NOW)

    store.update_to_accepted(_record(record_truncated=False), byte_count=10, codec="zlib", at=_NOW)

    [content] = store.records_for_segment("ch_1", "sg_1")
    assert content.record_truncated is False


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


def test_runner_id_for_lease_raises_when_two_runners_hold_the_same_lease_key(tmp_path: Path) -> None:
    """The query's own safety rests on an invariant (a genuine requeue bumps the fencing
    epoch) it neither states nor enforces — a violation must surface as an error, never
    as an arbitrary pick between the two runners' rows."""
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    store.insert_accepted(_record(segment_id="sg_1", runner_id="r1"), byte_count=10, codec="zlib", at=_NOW)
    store.insert_accepted(_record(segment_id="sg_2", runner_id="r2"), byte_count=10, codec="zlib", at=_NOW)

    with pytest.raises(RuntimeError, match="multiple runners"):
        store.runner_id_for_lease("ch_1", "nd_build", 1)


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


def test_records_for_lease_drops_a_segment_another_one_supersedes(tmp_path: Path) -> None:
    """A re-ship reuses its source's whole lease key, and this read is keyed on the lease —
    so without the pointer it concatenates both copies and renders the conversation twice."""
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    original = _record(segment_id="sg_old", turn_range_start=0, turn_range_end=0, record_truncated=True)
    reship = _record(segment_id="sg_new", turn_range_start=0, turn_range_end=0, supersedes="sg_old")
    for record in (original, reship):
        store.insert_accepted(record, byte_count=10, codec="zlib", at=_NOW)

    records = store.records_for_lease("ch_1", "nd_build", 1, "r1")

    assert len(records) == 1  # the superseded copy is gone, not merely ordered after
    assert records[0].record_truncated is False  # and its truncation no longer taints the read


def test_records_for_lease_keeps_a_segment_superseded_only_on_a_different_lease(tmp_path: Path) -> None:
    """The pointer is scoped to its own lease: a same-named segment under another node or
    epoch must not vanish because an unrelated lease named that id."""
    engine = _migrated_engine(tmp_path)
    store = TranscriptSegmentStore(engine)
    store.insert_accepted(_record(segment_id="sg_old"), byte_count=10, codec="zlib", at=_NOW)
    elsewhere = _record(segment_id="sg_new", node_id="nd_other", supersedes="sg_old")
    store.insert_accepted(elsewhere, byte_count=10, codec="zlib", at=_NOW)

    assert len(store.records_for_lease("ch_1", "nd_build", 1, "r1")) == 1


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

    store.update_still_rejected(_record(record_truncated=True), byte_count=1200, reason="record_too_large", at=later)

    with engine.connect() as conn:
        rows = conn.execute(select(s.transcript_segments)).all()
    assert len(rows) == 1
    assert rows[0].content is None
    assert rows[0].byte_count == 1200
    assert rows[0].received_at == later
    assert rows[0].record_truncated is True  # review F10: the re-offer's own flag, not the first's
