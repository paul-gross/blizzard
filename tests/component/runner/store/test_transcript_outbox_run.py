"""One transcript window advances the outbox counter once: ``record_transcript_deltas`` takes its window's seqs as one
contiguous run, so the counter's statements do not grow with the payloads in the window."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.store.schema import OUTBOX_LANE_TRANSCRIPT, outbox_counters
from blizzard.runner.store.scoped import RUNNER_UNSCOPED_OPTION
from blizzard.tools.invariants import runner_invariants
from tests.support.config_inputs import init_host
from tests.support.pin_store import NOW
from tests.support.runner_fakes import FIXTURE_RUNNER_ID, SqlAlchemyRunnerStore, runner_store_errors
from tests.support.runner_mint import new_mint
from tests.support.scaffolding import capture_statements


def _counter(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(outbox_counters.c.last_seq)
            .where(outbox_counters.c.runner_id == FIXTURE_RUNNER_ID, outbox_counters.c.lane == OUTBOX_LANE_TRANSCRIPT)
            .execution_options(**{RUNNER_UNSCOPED_OPTION: True})
        ).scalar_one()


def _open_segment(store: SqlAlchemyRunnerStore) -> str:
    store.record_mint(
        new_mint(
            NewLease(
                lease_id="lease_1",
                chunk_id="ch_1",
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
        "lease_1", pid=1, process_start_time="1", session=SessionReference(CLAUDE_CODE_HARNESS_ID, "s"), spawned_at=NOW
    )
    return store.open_transcript_segments()[0].segment_id


def _window(store: SqlAlchemyRunnerStore, segment_id: str, payloads: list[str], *, cursor: str) -> list[int]:
    return store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=cursor,
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=payloads,
        created_at=NOW,
    )


def _window_statements(tmp_path: Path, size: int) -> tuple[int, int]:
    """The (``outbox_counters``, outbox INSERT) statement counts of one window of ``size`` payloads, after a prior one."""
    engine = create_engine_from_url(init_host(tmp_path).db_url)
    try:
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        segment_id = _open_segment(store)
        _window(store, segment_id, ["{}"], cursor="c0")
        with capture_statements(engine) as statements:
            _window(store, segment_id, [f'{{"i":{i}}}' for i in range(size)], cursor="c1")
    finally:
        engine.dispose()
    texts = [text for text, _ in statements]
    return (
        sum("outbox_counters" in text for text in texts),
        sum(text.lstrip().upper().startswith("INSERT INTO TRANSCRIPT_OUTBOUND_BUFFER") for text in texts),
    )


def test_a_windows_counter_statements_are_flat_in_its_payload_count(tmp_path: Path) -> None:
    one = _window_statements(tmp_path / "one", 1)
    three = _window_statements(tmp_path / "three", 3)
    assert one == three
    assert three[1] == 1


def test_a_windows_seqs_are_one_contiguous_run_in_payload_order_above_earlier_rows(tmp_path: Path) -> None:
    engine = create_engine_from_url(init_host(tmp_path).db_url)
    try:
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        segment_id = _open_segment(store)
        earlier = _window(store, segment_id, ["{}", "{}"], cursor="c0")
        payloads = ['{"i":0}', '{"i":1}', '{"i":2}']
        seqs = _window(store, segment_id, payloads, cursor="c1")
        stored = {
            delta.seq: delta.payload for delta in store.pending_transcript_outbound(limit=10) if delta.seq in seqs
        }
    finally:
        engine.dispose()
    assert seqs == list(range(max(earlier) + 1, max(earlier) + 1 + len(payloads)))
    assert [stored[seq] for seq in seqs] == payloads


@pytest.mark.parametrize("size", [1, 4])
def test_a_first_allocation_of_n_rows_answers_one_through_n(tmp_path: Path, size: int) -> None:
    engine = create_engine_from_url(init_host(tmp_path).db_url)
    try:
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        segment_id = _open_segment(store)
        seqs = _window(store, segment_id, ["{}"] * size, cursor="c0")
        counter = _counter(engine)
        violations = [v for v in runner_invariants(engine).run() if v.invariant == "runner:outbox-counter-above-seq"]
    finally:
        engine.dispose()
    assert seqs == list(range(1, size + 1))
    assert counter == size
    assert violations == []
