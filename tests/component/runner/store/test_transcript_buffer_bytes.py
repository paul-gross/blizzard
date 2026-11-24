"""The unshipped transcript backlog is sized from each buffered row's recorded byte length: the sum covers every unacked
row, final markers included, and the read never touches a payload."""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.store.schema import transcript_outbound_buffer
from blizzard.runner.store.scoped import RUNNER_UNSCOPED_OPTION
from tests.support.config_inputs import init_host
from tests.support.pin_store import NOW
from tests.support.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors
from tests.support.runner_mint import new_mint
from tests.support.scaffolding import capture_statements


def _open_segment(store: SqlAlchemyRunnerStore, lease_id: str, chunk_id: str) -> str:
    store.record_mint(
        new_mint(
            NewLease(
                lease_id=lease_id,
                chunk_id=chunk_id,
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=NOW,
            )
        )
    )
    store.record_spawn(
        lease_id, pid=1, process_start_time="1", session=SessionReference(CLAUDE_CODE_HARNESS_ID, "s"), spawned_at=NOW
    )
    return store.open_transcript_segments_for_lease(lease_id)[0].segment_id


def _window(store: SqlAlchemyRunnerStore, segment_id: str, chunk_id: str, payloads: list[str]) -> list[int]:
    return store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id=chunk_id,
        cursor="c1",
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=payloads,
        created_at=NOW,
    )


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
    engine = create_engine_from_url(init_host(tmp_path).db_url)
    try:
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        shipped = _open_segment(store, "lease_1", "ch_1")
        acked = _window(store, shipped, "ch_1", [json.dumps({"text": "shipped"})])
        store.finalize_transcript_segment(shipped, finalized_at=NOW)
        store.ack_transcript_outbound_batch(
            [*acked, *(d.seq for d in store.pending_transcript_outbound() if d.final)], acked_at=NOW
        )
        open_segment = _open_segment(store, "lease_2", "ch_2")
        _window(store, open_segment, "ch_2", [json.dumps({"text": "plain"}), '{"text": "café ☃"}'])
        pending_segment = _open_segment(store, "lease_3", "ch_3")
        _window(store, pending_segment, "ch_3", [json.dumps({"text": "café"})])
        store.finalize_transcript_segment(pending_segment, finalized_at=NOW)
        unacked = _unacked_payloads(engine)
        pending_finals = [d.segment_id for d in store.pending_transcript_outbound() if d.final]

        outstanding = store.outstanding_transcript_buffer_bytes()
    finally:
        engine.dispose()
    assert pending_finals == [pending_segment]
    assert len(unacked) == 4
    assert outstanding == sum(len(p.encode()) for p in unacked)


def test_the_backlog_size_reads_the_recorded_length_and_never_the_payload(tmp_path: Path) -> None:
    engine = create_engine_from_url(init_host(tmp_path).db_url)
    try:
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        segment_id = _open_segment(store, "lease_1", "ch_1")
        _window(store, segment_id, "ch_1", ["{}"])
        with capture_statements(engine) as statements:
            store.outstanding_transcript_buffer_bytes()
    finally:
        engine.dispose()
    [(text, _)] = statements
    assert "payload_bytes" in text
    assert "payload" not in text.replace("payload_bytes", "")
