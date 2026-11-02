"""``ChunkQueueStore``'s plural position writes (component tier).

``record_queue_positions``/``record_backlog_positions`` each write a whole batch of
``(chunk_id, position)`` pairs in one write transaction — proves the insert stays bulk
as the batch grows, and that the backlog variant's promoted-chunk guard still
skips a chunk promoted since the caller resolved candidates, as one bulk ``.in_()`` read."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store import batching
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from tests.support import capture_statements, chunk_stores, count_queries, migrate_to, seed_chunk_record, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path: Path) -> tuple[ChunkStores, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    return chunk_stores(engine, FixedClock(_T0)), engine


def _mint(store: ChunkStores, chunk_id: str) -> None:
    seed_chunk_record(store, Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0))


def test_locked_promoted_ats_batches_and_combines_results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, engine = _store(tmp_path)
    ids = [f"ch_{i}" for i in range(5)]
    for chunk_id in ids:
        _mint(store, chunk_id)
    for chunk_id in (ids[0], ids[-1]):
        store.queue.record_promote(chunk_id, at=_T0)
    monkeypatch.setattr(batching, "BATCH_SIZE", 2)

    with store.exclusive.locked_queue([]) as handle, capture_statements(engine) as statements:
        actual = store.queue.promoted_ats_locked(handle, ids)

    assert actual == {ids[0]: _T0, ids[-1]: _T0}
    promoted_reads = [sql for sql, _ in statements if "FROM chunk_promoted" in sql]
    assert len(promoted_reads) == 3


def test_record_queue_positions_writes_an_empty_sequence_as_a_no_op(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)

    store.queue.record_queue_positions([], at=_T0)

    assert store.queue.queue_positions(["ch_a"]) == {}


def test_record_queue_positions_writes_every_pair(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    for chunk_id in ("ch_a", "ch_b", "ch_c"):
        _mint(store, chunk_id)

    store.queue.record_queue_positions([("ch_a", 0.0), ("ch_b", 1.0), ("ch_c", 2.0)], at=_T0)

    assert store.queue.queue_positions(["ch_a", "ch_b", "ch_c"]) == {"ch_a": 0.0, "ch_b": 1.0, "ch_c": 2.0}


def test_record_queue_positions_locks_once_and_inserts_in_bulk(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    small_ids = [f"ch_small_{i}" for i in range(3)]
    large_ids = [f"ch_large_{i}" for i in range(9)]
    for chunk_id in [*small_ids, *large_ids]:
        _mint(store, chunk_id)

    store.queue.record_queue_positions([(small_ids[0], 0.0)], at=_T0)
    small_count = count_queries(
        engine, lambda: store.queue.record_queue_positions([(c, float(i)) for i, c in enumerate(small_ids)], at=_T0)
    )
    large_count = count_queries(
        engine, lambda: store.queue.record_queue_positions([(c, float(i)) for i, c in enumerate(large_ids)], at=_T0)
    )

    assert large_count == small_count


@pytest.mark.parametrize("method", ["record_queue_positions", "record_backlog_positions"])
def test_position_writes_take_one_queue_lock_across_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    store, engine = _store(tmp_path)
    for chunk_id in ("ch_a", "ch_b", "ch_c"):
        _mint(store, chunk_id)
    monkeypatch.setattr(batching, "BATCH_SIZE", 2)

    with capture_statements(engine) as statements:
        getattr(store.queue, method)([("ch_c", 1.0), ("ch_a", 2.0), ("ch_b", 3.0), ("ch_a", 4.0)], at=_T0)

    assert not [sql for sql, _ in statements if sql.startswith("UPDATE chunks SET")]
    assert len([sql for sql, _ in statements if sql.startswith("UPDATE keyed_locks SET")]) == 1


def test_record_backlog_positions_skips_a_chunk_promoted_since_candidates_were_resolved(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _mint(store, "ch_a")
    _mint(store, "ch_promoted")
    # Simulates the race: the caller resolved backlog candidates before this promote
    # committed, so the reorder must not override the fresh tail stamp it landed.
    store.queue.record_promote("ch_promoted", at=_T0)

    store.queue.record_backlog_positions([("ch_a", 0.0), ("ch_promoted", 1.0)], at=_T0)

    positions = store.queue.queue_positions(["ch_a", "ch_promoted"])
    assert positions.get("ch_a") == 0.0
    assert "ch_promoted" not in positions


def test_record_backlog_positions_guard_is_one_bulk_read_not_one_per_pair(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    small_ids = [f"ch_small_{i}" for i in range(3)]
    large_ids = [f"ch_large_{i}" for i in range(9)]
    for chunk_id in [*small_ids, *large_ids]:
        _mint(store, chunk_id)

    store.queue.record_backlog_positions([(small_ids[0], 0.0)], at=_T0)
    small_count = count_queries(
        engine, lambda: store.queue.record_backlog_positions([(c, float(i)) for i, c in enumerate(small_ids)], at=_T0)
    )
    large_count = count_queries(
        engine, lambda: store.queue.record_backlog_positions([(c, float(i)) for i, c in enumerate(large_ids)], at=_T0)
    )

    assert large_count == small_count
