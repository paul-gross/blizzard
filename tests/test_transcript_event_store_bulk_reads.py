"""``TranscriptEventStore``'s batch reads — ``derivation_markers``,
``segment_derivation_inputs``, and ``segment_contexts`` (component tier).

Proves each agrees with its singular sibling across a lowered ``BATCH_SIZE`` boundary; a
segment that fails to decode stays out of ``segment_derivation_inputs`` without costing
the rest of the batch; and ``segment_contexts`` never selects ``content``."""

from __future__ import annotations

import json
import zlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, update

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.transcripts import SegmentRecord
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import batching as batching_module
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore, _segment_contexts_stmt
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from tests.support import hub_store_connections, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
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


def _migrated_engine(tmp_path: Path, *, chunk_ids: tuple[str, ...] = ("ch_1", "ch_2", "ch_3")) -> Engine:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        for chunk_id in chunk_ids:
            seed_chunk(conn, chunk_id, graph_id="gr_1", at=_T0)
    return engine


# --- derivation_markers -------------------------------------------------------- #


def test_derivation_markers_matches_derivation_marker_per_segment(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptEventStore(hub_store_connections(engine))
    store.replace_segment_events("sg_1", _EXTRACTOR_VERSION, [], complete=True, content_fingerprint="fp1", at=_T0)
    store.replace_segment_events("sg_2", _EXTRACTOR_VERSION, [], complete=True, content_fingerprint="fp2", at=_T0)
    # A different extractor version's marker must not leak into the requested version's read.
    store.replace_segment_events("sg_2", "blizzard-analytics/2", [], complete=True, content_fingerprint="fp3", at=_T0)

    result = store.derivation_markers(_EXTRACTOR_VERSION)

    assert set(result) == {"sg_1", "sg_2"}
    for segment_id, marker in result.items():
        assert marker == store.derivation_marker(segment_id, _EXTRACTOR_VERSION)


def test_derivation_markers_of_no_segments_is_empty(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptEventStore(hub_store_connections(engine))
    assert store.derivation_markers(_EXTRACTOR_VERSION) == {}


# --- segment_derivation_inputs -------------------------------------------------- #


def test_segment_derivation_inputs_matches_the_singular_across_a_normal_and_an_unknown_id(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    segments = TranscriptSegmentStore(hub_store_connections(engine))
    segments.insert_accepted(_segment_record(segment_id="sg_1", chunk_id="ch_1"), byte_count=10, codec="zlib", at=_T0)
    segments.insert_accepted(_segment_record(segment_id="sg_2", chunk_id="ch_2"), byte_count=10, codec="zlib", at=_T0)
    store = TranscriptEventStore(hub_store_connections(engine))

    result = store.segment_derivation_inputs(["sg_1", "sg_2", "sg_missing"])

    assert set(result) == {"sg_1", "sg_2"}
    for segment_id, value in result.items():
        assert value == store.segment_derivation_input(segment_id)


def test_segment_derivation_inputs_of_no_ids_is_empty(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptEventStore(hub_store_connections(engine))
    assert store.segment_derivation_inputs([]) == {}


def test_segment_derivation_inputs_matches_across_a_batch_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    chunk_ids = tuple(f"ch_{i}" for i in range(7))
    engine = _migrated_engine(tmp_path, chunk_ids=chunk_ids)
    segments = TranscriptSegmentStore(hub_store_connections(engine))
    segment_ids = [f"sg_{i}" for i in range(7)]
    for segment_id, chunk_id in zip(segment_ids, chunk_ids, strict=True):
        segments.insert_accepted(
            _segment_record(segment_id=segment_id, chunk_id=chunk_id), byte_count=10, codec="zlib", at=_T0
        )
    store = TranscriptEventStore(hub_store_connections(engine))

    result = store.segment_derivation_inputs(segment_ids)

    assert set(result) == set(segment_ids)
    for segment_id in segment_ids:
        assert result[segment_id] == store.segment_derivation_input(segment_id)


def test_segment_derivation_inputs_drops_a_segment_whose_content_fails_to_decode_without_failing_the_batch(
    tmp_path: Path,
) -> None:
    engine = _migrated_engine(tmp_path)
    segments = TranscriptSegmentStore(hub_store_connections(engine))
    segments.insert_accepted(
        _segment_record(segment_id="sg_good", chunk_id="ch_1"), byte_count=10, codec="zlib", at=_T0
    )
    segments.insert_accepted(
        _segment_record(segment_id="sg_corrupt", chunk_id="ch_2"), byte_count=10, codec="zlib", at=_T0
    )
    with engine.begin() as conn:
        conn.execute(
            update(s.transcript_segments)
            .where(s.transcript_segments.c.segment_id == "sg_corrupt")
            .values(content=b"not valid zlib content")
        )
    store = TranscriptEventStore(hub_store_connections(engine))

    result = store.segment_derivation_inputs(["sg_good", "sg_corrupt"])

    assert set(result) == {"sg_good"}
    assert result["sg_good"] == store.segment_derivation_input("sg_good")
    # The singular does not share the plural's fault isolation — it lets the same
    # decode failure raise, which is exactly why the plural exists.
    with pytest.raises(zlib.error):
        store.segment_derivation_input("sg_corrupt")


# --- segment_contexts ------------------------------------------------------------ #


def test_segment_contexts_agrees_with_the_matching_fields_of_segment_derivation_inputs(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    segments = TranscriptSegmentStore(hub_store_connections(engine))
    segments.insert_accepted(_segment_record(segment_id="sg_1", chunk_id="ch_1"), byte_count=10, codec="zlib", at=_T0)
    segments.insert_rejected(
        _segment_record(segment_id="sg_2", chunk_id="ch_2"), byte_count=999, reason="record_too_large", at=_T0
    )
    store = TranscriptEventStore(hub_store_connections(engine))

    contexts = store.segment_contexts(["sg_1", "sg_2", "sg_missing"])
    inputs = store.segment_derivation_inputs(["sg_1", "sg_2", "sg_missing"])

    assert set(contexts) == set(inputs) == {"sg_1", "sg_2"}
    for segment_id, context in contexts.items():
        derivation_input = inputs[segment_id]
        assert context.segment_id == derivation_input.segment_id
        assert context.chunk_id == derivation_input.chunk_id
        assert context.node_id == derivation_input.node_id
        assert context.epoch == derivation_input.epoch
        assert context.spawn_generation == derivation_input.spawn_generation
        assert context.normalizer_version == derivation_input.normalizer_version
        assert context.complete == derivation_input.complete
        assert context.content_fingerprint == derivation_input.content_fingerprint


def test_segment_contexts_of_no_ids_is_empty(tmp_path: Path) -> None:
    engine = _migrated_engine(tmp_path)
    store = TranscriptEventStore(hub_store_connections(engine))
    assert store.segment_contexts([]) == {}


def test_segment_contexts_matches_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    chunk_ids = tuple(f"ch_{i}" for i in range(7))
    engine = _migrated_engine(tmp_path, chunk_ids=chunk_ids)
    segments = TranscriptSegmentStore(hub_store_connections(engine))
    segment_ids = [f"sg_{i}" for i in range(7)]
    for segment_id, chunk_id in zip(segment_ids, chunk_ids, strict=True):
        segments.insert_accepted(
            _segment_record(segment_id=segment_id, chunk_id=chunk_id), byte_count=10, codec="zlib", at=_T0
        )
    store = TranscriptEventStore(hub_store_connections(engine))

    result = store.segment_contexts(segment_ids)

    assert set(result) == set(segment_ids)


def test_segment_contexts_never_selects_the_content_column() -> None:
    stmt = _segment_contexts_stmt(["sg_1"])
    assert "content" not in {column.name for column in stmt.selected_columns}
