"""The unshipped transcript backlog is sized from each buffered row's recorded byte length: the sum covers every unacked
row, final markers included, and the read never touches a payload."""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa

from blizzard.runner.store.schema import transcript_outbound_buffer
from blizzard.runner.store.scoped import RUNNER_UNSCOPED_OPTION
from tests.support.pin_store import NOW
from tests.support.scaffolding import capture_statements
from tests.support.transcript_outbox import head_runner_store, open_segment, record_window


def _unacked_payloads(engine: sa.Engine) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                sa.select(transcript_outbound_buffer.c.payload)
                .where(transcript_outbound_buffer.c.acked_at.is_(None))
                .execution_options(**{RUNNER_UNSCOPED_OPTION: True})
            ).scalars()
        )


def test_the_backlog_size_sums_the_unacked_rows_byte_lengths_final_markers_included(tmp_path: Path) -> None:
    with head_runner_store(tmp_path) as (engine, store):
        shipped = open_segment(store, "lease_1", "ch_1")
        acked = record_window(store, shipped, [json.dumps({"text": "shipped"})], chunk_id="ch_1")
        store.finalize_transcript_segment(shipped, finalized_at=NOW)
        store.ack_transcript_outbound_batch(
            [*acked, *(d.seq for d in store.pending_transcript_outbound() if d.final)], acked_at=NOW
        )
        open_one = open_segment(store, "lease_2", "ch_2")
        record_window(store, open_one, [json.dumps({"text": "plain"}), '{"text": "café ☃"}'], chunk_id="ch_2")
        pending_segment = open_segment(store, "lease_3", "ch_3")
        record_window(store, pending_segment, [json.dumps({"text": "café"})], chunk_id="ch_3")
        store.finalize_transcript_segment(pending_segment, finalized_at=NOW)
        unacked = _unacked_payloads(engine)
        pending_finals = [d.segment_id for d in store.pending_transcript_outbound() if d.final]

        outstanding = store.outstanding_transcript_buffer_bytes()
    assert pending_finals == [pending_segment]
    assert len(unacked) == 4
    assert outstanding == sum(len(p.encode()) for p in unacked)


def test_the_backlog_size_reads_the_recorded_length_and_never_the_payload(tmp_path: Path) -> None:
    with head_runner_store(tmp_path) as (engine, store):
        segment_id = open_segment(store)
        record_window(store, segment_id, ["{}"])
        with capture_statements(engine) as statements:
            store.outstanding_transcript_buffer_bytes()
    [(text, _)] = statements
    assert "payload_bytes" in text
    assert "payload" not in text.replace("payload_bytes", "")
