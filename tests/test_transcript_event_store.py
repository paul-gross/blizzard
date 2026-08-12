"""Transcript-event store: dialect-portable DDL, the visible-set query, the decode of a
segment's stored content into turn objects, and the scoped-replacement natural key
(blizzard#254, Phase 1 — unit tier)."""

from __future__ import annotations

import ast
import json
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
from blizzard.hub.domain.analytics.events import TranscriptEvent
from blizzard.hub.domain.transcripts import SegmentRecord
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import transcript_event_store as store_module
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 12, tzinfo=UTC)
_EXTRACTOR_VERSION = "blizzard-analytics/1"


def _segment_record(**overrides: object) -> SegmentRecord:
    values: dict[str, object] = {
        "segment_id": "sg_1",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "runner_id": "r1",
        "turn_range_start": 0,
        "turn_range_end": 0,
        "final": True,
        "normalizer_version": "claude-code-jsonl/2",
        "harness_version": "claude-code-1.0",
        "record_truncated": False,
        "turns_json": json.dumps(
            [
                {
                    "index": 0,
                    "kind": "tool",
                    "timestamp": None,
                    "text": "",
                    "tool": {
                        "name": "Read",
                        "input": {"file_path": "a.py"},
                        "input_unparsed": None,
                        "input_shape": "object",
                        "tool_use_id": "t1",
                        "output": None,
                        "output_truncated": False,
                    },
                    "thinking_redacted": False,
                    "sidechain": None,
                    "truncated": False,
                }
            ]
        ),
    }
    values.update(overrides)
    return SegmentRecord(**values)  # type: ignore[arg-type]


def _event(**overrides: object) -> TranscriptEvent:
    values: dict[str, object] = {
        "kind": "file_read",
        "turn_path": "0",
        "occurrence": 0,
        "payload": json.dumps({"tool_name": "Read", "path": "a.py"}),
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "graph_id": "gr_1",
        "depth": 0,
        "agent_type": None,
        "occurred_at": None,
    }
    values.update(overrides)
    return TranscriptEvent(**values)  # type: ignore[arg-type]


def _migrated_engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    return create_engine_from_url(db_url)


# --- dialect-portable DDL and the statements the store itself executes -------


def _executed_statements() -> dict[str, ClauseElement]:
    m = store_module
    return {
        "_visible_segment_ids_stmt": m._visible_segment_ids_stmt(),
        "_derived_segment_ids_stmt": m._derived_segment_ids_stmt(),
        "_segment_records_stmt": m._segment_records_stmt("sg_1"),
        "_marker_stmt": m._marker_stmt("sg_1", _EXTRACTOR_VERSION),
        "_delete_events_stmt": m._delete_events_stmt("sg_1", _EXTRACTOR_VERSION),
        "_delete_marker_stmt": m._delete_marker_stmt("sg_1", _EXTRACTOR_VERSION),
        "_insert_events_stmt": m._insert_events_stmt("sg_1", _EXTRACTOR_VERSION, [_event()]),
        "_upsert_marker_stmt": m._upsert_marker_stmt(
            "sg_1", _EXTRACTOR_VERSION, content_fingerprint="fp", event_count=1, complete=True, at=_NOW
        ),
        "_delete_all_events_for_segment_stmt": m._delete_all_events_for_segment_stmt("sg_1"),
        "_delete_all_markers_for_segment_stmt": m._delete_all_markers_for_segment_stmt("sg_1"),
    }


def test_transcript_events_ddl_compiles_under_both_dialects() -> None:
    from sqlalchemy.schema import CreateTable

    for dialect in (postgresql.dialect(), sqlite.dialect()):
        sql = str(CreateTable(s.transcript_events).compile(dialect=dialect))
        assert "transcript_events" in sql


def test_transcript_event_derivations_ddl_compiles_under_both_dialects() -> None:
    from sqlalchemy.schema import CreateTable

    for dialect in (postgresql.dialect(), sqlite.dialect()):
        sql = str(CreateTable(s.transcript_event_derivations).compile(dialect=dialect))
        assert "transcript_event_derivations" in sql


def test_every_statement_the_store_executes_compiles_under_both_dialects() -> None:
    for name, stmt in _executed_statements().items():
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            assert "transcript_event" in str(stmt.compile(dialect=dialect)) or "transcript_segments" in str(
                stmt.compile(dialect=dialect)
            ), name


def test_no_statement_the_store_executes_leaves_the_portable_surface() -> None:
    assert store_module.__name__.endswith("transcript_event_store")  # pins which adapter this sweep covers
    for name, stmt in _executed_statements().items():
        assert type(stmt).__module__.startswith("sqlalchemy.sql."), name
        assert not [e for e in visitors.iterate(stmt) if isinstance(e, TextClause)], name


def test_the_compile_sweep_reaches_every_statement_the_store_can_execute() -> None:
    builders = {name for name in vars(store_module) if name.endswith("_stmt") or name == "_upsert_marker_stmt"}
    assert builders == set(_executed_statements())

    source = ast.parse(Path(store_module.__file__ or "").read_text())
    executed = [
        node.args[0]
        for node in ast.walk(source)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "execute"
    ]
    assert len(executed) == len(builders)
    for arg in executed:
        built_by_a_builder = isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) and arg.func.id in builders
        assert built_by_a_builder, ast.unparse(arg)


# --- visible set ---------------------------------------------------------------


def test_visible_segment_ids_excludes_a_non_final_segment(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    segments = TranscriptSegmentStore(engine)
    segments.insert_accepted(_segment_record(final=False), byte_count=10, codec="zlib", at=_NOW)

    store = TranscriptEventStore(engine)
    assert store.visible_segment_ids() == frozenset()


def test_visible_segment_ids_excludes_a_superseded_segment(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    segments = TranscriptSegmentStore(engine)
    segments.insert_accepted(_segment_record(segment_id="sg_old"), byte_count=10, codec="zlib", at=_NOW)
    segments.insert_accepted(
        _segment_record(segment_id="sg_new", supersedes="sg_old"), byte_count=10, codec="zlib", at=_NOW
    )

    store = TranscriptEventStore(engine)
    assert store.visible_segment_ids() == frozenset({"sg_new"})


def test_visible_segment_ids_includes_a_final_unsuperseded_segment(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    segments = TranscriptSegmentStore(engine)
    segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)

    store = TranscriptEventStore(engine)
    assert store.visible_segment_ids() == frozenset({"sg_1"})


# --- decode ----------------------------------------------------------------


def test_segment_derivation_input_decodes_turns_from_stored_content(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    segments = TranscriptSegmentStore(engine)
    record = _segment_record()
    segments.insert_accepted(record, byte_count=10, codec="zlib", at=_NOW)

    store = TranscriptEventStore(engine)
    result = store.segment_derivation_input("sg_1")

    assert result is not None
    assert result.chunk_id == "ch_1"
    assert result.node_id == "nd_build"
    assert result.epoch == 1
    assert result.spawn_generation == 1
    assert result.normalizer_version == "claude-code-jsonl/2"
    assert result.complete is True
    assert len(result.turns) == 1
    assert result.turns[0].tool is not None
    assert result.turns[0].tool.name == "Read"
    assert result.turns[0].tool.input == {"file_path": "a.py"}


def test_segment_derivation_input_is_none_for_an_unknown_segment(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptEventStore(engine)
    assert store.segment_derivation_input("sg_missing") is None


def test_segment_derivation_input_is_incomplete_when_a_record_is_rejected(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    segments = TranscriptSegmentStore(engine)
    segments.insert_rejected(_segment_record(), byte_count=999, reason="record_too_large", at=_NOW)

    store = TranscriptEventStore(engine)
    result = store.segment_derivation_input("sg_1")

    assert result is not None
    assert result.complete is False
    assert result.turns == []


def test_content_fingerprint_changes_when_a_rejected_record_is_later_accepted(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    segments = TranscriptSegmentStore(engine)
    record = _segment_record()
    segments.insert_rejected(record, byte_count=999, reason="record_too_large", at=_NOW)
    store = TranscriptEventStore(engine)
    before = store.segment_derivation_input("sg_1")
    assert before is not None

    segments.update_to_accepted(record, byte_count=10, codec="zlib", at=_NOW)
    after = store.segment_derivation_input("sg_1")

    assert after is not None
    assert after.content_fingerprint != before.content_fingerprint


# --- scoped replacement (D6) ------------------------------------------------


def test_replace_segment_events_writes_events_and_marker(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptEventStore(engine)

    store.replace_segment_events(
        "sg_1", _EXTRACTOR_VERSION, [_event()], complete=True, content_fingerprint="fp1", at=_NOW
    )

    with engine.connect() as conn:
        rows = conn.execute(select(s.transcript_events)).all()
    assert len(rows) == 1
    assert rows[0].kind == "file_read"

    marker = store.derivation_marker("sg_1", _EXTRACTOR_VERSION)
    assert marker is not None
    assert marker.content_fingerprint == "fp1"
    assert marker.event_count == 1
    assert marker.complete is True


def test_replace_segment_events_converges_under_a_repeated_call(tmp_path: Path) -> None:
    """The natural key — ``(segment_id, extractor_version, kind, turn_path, occurrence)`` —
    makes even a partially-applied re-run converge: a second identical replace leaves
    exactly one row, not two, and never raises."""
    engine = _migrated_engine(tmp_path)
    store = TranscriptEventStore(engine)

    for _ in range(2):
        store.replace_segment_events(
            "sg_1", _EXTRACTOR_VERSION, [_event()], complete=True, content_fingerprint="fp1", at=_NOW
        )

    with engine.connect() as conn:
        rows = conn.execute(select(s.transcript_events)).all()
    assert len(rows) == 1


def test_replace_segment_events_leaves_other_extractor_versions_untouched(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptEventStore(engine)
    store.replace_segment_events(
        "sg_1", "blizzard-analytics/1", [_event()], complete=True, content_fingerprint="fp1", at=_NOW
    )

    store.replace_segment_events(
        "sg_1", "blizzard-analytics/2", [_event(turn_path="1")], complete=True, content_fingerprint="fp2", at=_NOW
    )

    with engine.connect() as conn:
        rows = conn.execute(select(s.transcript_events)).all()
    assert {row.extractor_version for row in rows} == {"blizzard-analytics/1", "blizzard-analytics/2"}
    assert len(rows) == 2


def test_replace_segment_events_with_no_events_still_writes_a_marker(tmp_path: Path) -> None:
    """An unknown-dialect or empty-content segment derives zero events, honestly recorded
    rather than left looking like it was never attempted."""
    engine = _migrated_engine(tmp_path)
    store = TranscriptEventStore(engine)

    store.replace_segment_events("sg_1", _EXTRACTOR_VERSION, [], complete=True, content_fingerprint="fp1", at=_NOW)

    marker = store.derivation_marker("sg_1", _EXTRACTOR_VERSION)
    assert marker is not None
    assert marker.event_count == 0
    with engine.connect() as conn:
        rows = conn.execute(select(s.transcript_events)).all()
    assert rows == []


def test_the_natural_key_is_enforced_by_the_schema(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(store_module._insert_events_stmt("sg_1", _EXTRACTOR_VERSION, [_event()]))
        with pytest.raises(IntegrityError):
            conn.execute(store_module._insert_events_stmt("sg_1", _EXTRACTOR_VERSION, [_event()]))


def test_drop_segment_removes_events_and_markers_at_every_extractor_version(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptEventStore(engine)
    store.replace_segment_events(
        "sg_1", "blizzard-analytics/1", [_event()], complete=True, content_fingerprint="fp1", at=_NOW
    )
    store.replace_segment_events(
        "sg_1", "blizzard-analytics/2", [_event(turn_path="1")], complete=True, content_fingerprint="fp2", at=_NOW
    )

    store.drop_segment("sg_1")

    with engine.connect() as conn:
        assert conn.execute(select(s.transcript_events)).all() == []
        assert conn.execute(select(s.transcript_event_derivations)).all() == []
    assert store.derived_segment_ids() == frozenset()


def test_derived_segment_ids_reflects_every_segment_with_a_marker(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptEventStore(engine)
    store.replace_segment_events(
        "sg_1", _EXTRACTOR_VERSION, [_event()], complete=True, content_fingerprint="fp1", at=_NOW
    )
    store.replace_segment_events("sg_2", _EXTRACTOR_VERSION, [], complete=True, content_fingerprint="fp2", at=_NOW)

    assert store.derived_segment_ids() == frozenset({"sg_1", "sg_2"})
