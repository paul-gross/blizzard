"""One transcript window advances the outbox counter once: ``record_transcript_deltas`` takes its window's seqs as one
contiguous run, so the counter's statements do not grow with the payloads in the window."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.runner.store.schema import OUTBOX_LANE_TRANSCRIPT, outbox_counters
from blizzard.runner.store.scoped import RUNNER_UNSCOPED_OPTION
from blizzard.tools.invariants import runner_invariants
from tests.support.runner_fakes import FIXTURE_RUNNER_ID
from tests.support.scaffolding import capture_statements
from tests.support.transcript_outbox import head_runner_store, open_segment, record_window

#: More rows than one multi-row INSERT can bind on SQLite (eight columns a row against its 32766-variable limit).
_OVER_BIND_LIMIT_ROWS = 4200


def _counter(engine: sa.Engine) -> int:
    with engine.connect() as conn:
        return conn.execute(
            sa.select(outbox_counters.c.last_seq)
            .where(outbox_counters.c.runner_id == FIXTURE_RUNNER_ID, outbox_counters.c.lane == OUTBOX_LANE_TRANSCRIPT)
            .execution_options(**{RUNNER_UNSCOPED_OPTION: True})
        ).scalar_one()


def _window_statements(tmp_path: Path, size: int) -> tuple[int, int]:
    """One window's ``outbox_counters`` and outbox INSERT statement counts at ``size`` payloads, after a prior one."""
    with head_runner_store(tmp_path) as (engine, store):
        segment_id = open_segment(store)
        record_window(store, segment_id, ["{}"], cursor="c0")
        with capture_statements(engine) as statements:
            record_window(store, segment_id, [f'{{"i":{i}}}' for i in range(size)])
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
    with head_runner_store(tmp_path) as (_engine, store):
        segment_id = open_segment(store)
        earlier = record_window(store, segment_id, ["{}", "{}"], cursor="c0")
        payloads = ['{"i":0}', '{"i":1}', '{"i":2}']
        seqs = record_window(store, segment_id, payloads)
        stored = {
            delta.seq: delta.payload for delta in store.pending_transcript_outbound(limit=10) if delta.seq in seqs
        }
    assert seqs == list(range(max(earlier) + 1, max(earlier) + 1 + len(payloads)))
    assert [stored[seq] for seq in seqs] == payloads


@pytest.mark.parametrize("size", [1, 4])
def test_a_first_allocation_of_n_rows_answers_one_through_n(tmp_path: Path, size: int) -> None:
    with head_runner_store(tmp_path) as (engine, store):
        segment_id = open_segment(store)
        seqs = record_window(store, segment_id, ["{}"] * size, cursor="c0")
        counter = _counter(engine)
        violations = [v for v in runner_invariants(engine).run() if v.invariant == "runner:outbox-counter-above-seq"]
    assert seqs == list(range(1, size + 1))
    assert counter == size
    assert violations == []


def test_a_window_past_the_bind_limit_lands_whole_under_one_counter_advance(tmp_path: Path) -> None:
    with head_runner_store(tmp_path) as (engine, store):
        segment_id = open_segment(store)
        with capture_statements(engine) as statements:
            seqs = record_window(store, segment_id, ["{}"] * _OVER_BIND_LIMIT_ROWS, cursor="c0")
        counter = _counter(engine)
        pending = len(store.pending_transcript_outbound(limit=_OVER_BIND_LIMIT_ROWS + 1))
    assert seqs == list(range(1, _OVER_BIND_LIMIT_ROWS + 1))
    assert counter == pending == _OVER_BIND_LIMIT_ROWS
    assert sum(text.lstrip().upper().startswith("UPDATE OUTBOX_COUNTERS") for text, _ in statements) == 1
