"""Runner-store decisions that had only a comment defending them — the migrated outbound sequences.

Each test below pins one store-level decision whose reversion no other test caught."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import TypedDict

import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.store.schema import (
    OUTBOX_LANE_OUTBOUND,
    OUTBOX_LANE_TRANSCRIPT,
    outbox_counters,
)
from blizzard.runner.store.scoped import RUNNER_UNSCOPED_OPTION
from tests.support.config_inputs import init_host
from tests.support.pin_store import NOW
from tests.support.runner_fakes import FIXTURE_RUNNER_ID, SqlAlchemyRunnerStore, runner_store_errors
from tests.support.runner_mint import new_mint


def _counter(engine: sa.Engine, lane: str) -> int:
    counters = outbox_counters
    with engine.connect() as conn:
        return conn.execute(
            sa.select(counters.c.last_seq)
            .where(counters.c.runner_id == FIXTURE_RUNNER_ID, counters.c.lane == lane)
            .execution_options(**{RUNNER_UNSCOPED_OPTION: True})
        ).scalar_one()


def _delta(store: SqlAlchemyRunnerStore, segment_id: str, *, cursor: str) -> int:
    (seq,) = store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor=cursor,
        shipped_bytes=1,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version=None,
        payloads=["{}"],
        created_at=NOW,
    )
    return seq


def test_a_migrated_transcript_outbound_seq_is_never_reissued_after_a_prune(tmp_path: Path) -> None:
    """The lane's ``seq`` comes from its durable counter, not the table: pruning the table's only, and highest, row
    leaves the counter on it, so the next delta takes the next ``seq``. Production migrates
    (`bzh:gating-tier-pins-production-paths`), so the pin runs on a migrated store."""
    config = init_host(tmp_path)
    engine = create_engine_from_url(config.db_url)
    try:
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
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
            "lease_1",
            pid=1,
            process_start_time="1",
            session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
            spawned_at=NOW,
        )
        segment_id = store.open_transcript_segments()[0].segment_id
        first = _delta(store, segment_id, cursor="tok-1")
        store.ack_transcript_outbound(first, acked_at=NOW)  # prunes the table's only, and highest, row
        assert _counter(engine, OUTBOX_LANE_TRANSCRIPT) == first
        second = _delta(store, segment_id, cursor="tok-2")
    finally:
        engine.dispose()

    assert second == first + 1


def test_a_migrated_outbound_seq_is_never_reissued_after_a_prune(tmp_path: Path) -> None:
    """``outbound_buffer`` counts the same way, and retention prunes it too: with every issued ``seq`` acked and pruned,
    the counter still holds the highest, and the next fact takes the one after it. Production migrates
    (`bzh:gating-tier-pins-production-paths`), so the pin runs on a migrated store."""
    config = init_host(tmp_path)
    engine = create_engine_from_url(config.db_url)
    try:
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        old = NOW - timedelta(days=8)  # past the 7-day retention window
        seqs = [
            store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=old)
            for _ in range(3)
        ]
        for seq in seqs:
            store.ack_outbound(seq, acked_at=old)  # every issued seq acked — none pending, no floor to respect

        pruned = store.prune_outbound(now=NOW)
        assert pruned == len(seqs)  # every acked row, including the table's own highest seq, is gone
        assert _counter(engine, OUTBOX_LANE_OUTBOUND) == max(seqs)

        fresh = store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="l", payload="{}", created_at=NOW)
    finally:
        engine.dispose()

    assert seqs == [1, 2, 3]
    assert fresh == max(seqs) + 1


class _Enqueue(TypedDict):
    kind: str
    chunk_id: str | None
    lease_id: str | None
    payload: str
    created_at: datetime


def test_each_runner_counts_its_own_outbound_seq_in_one_store(tmp_path: Path) -> None:
    """Two runners over one file each take ``seq`` 1, 2, ... from a counter of their own."""
    config = init_host(tmp_path)
    engine = create_engine_from_url(config.db_url)
    try:
        first = SqlAlchemyRunnerStore(engine, runner_store_errors(), "rn_first")
        second = SqlAlchemyRunnerStore(engine, runner_store_errors(), "rn_second")
        enqueue: _Enqueue = {
            "kind": "lease.minted",
            "chunk_id": None,
            "lease_id": None,
            "payload": "{}",
            "created_at": NOW,
        }
        seqs = (
            [first.enqueue_outbound(**enqueue) for _ in range(2)],
            [second.enqueue_outbound(**enqueue) for _ in range(3)],
            [first.enqueue_outbound(**enqueue)],
        )
        pending = ([f.seq for f in first.pending_outbound()], [f.seq for f in second.pending_outbound()])
    finally:
        engine.dispose()

    assert seqs == ([1, 2], [1, 2, 3], [3])
    assert pending == ([1, 2, 3], [1, 2, 3])
