"""``ChunkQueueStore``'s plural position writes (component tier).

``record_queue_positions``/``record_backlog_positions`` each write a whole batch of
``(chunk_id, position)`` pairs in one write transaction — proves the statement count
stays flat as the batch grows, and that the backlog variant's promoted-chunk guard still
skips a chunk promoted since the caller resolved candidates, as one bulk ``.in_()`` read."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunks.stores import ChunkStores
from blizzard.hub.domain.work import Chunk
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from tests.support import count_queries, hub_store_connections, migrate_to, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path: Path) -> tuple[ChunkStores, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    return build_chunk_stores(hub_store_connections(engine), FixedClock(_T0)), engine


def _mint(store: ChunkStores, chunk_id: str) -> None:
    store.record.mint(Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0))


def test_record_queue_positions_writes_an_empty_sequence_as_a_no_op(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)

    store.queue.record_queue_positions([], at=_T0)

    assert store.queue.queue_positions() == {}


def test_record_queue_positions_writes_every_pair(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    for chunk_id in ("ch_a", "ch_b", "ch_c"):
        _mint(store, chunk_id)

    store.queue.record_queue_positions([("ch_a", 0.0), ("ch_b", 1.0), ("ch_c", 2.0)], at=_T0)

    assert store.queue.queue_positions() == {"ch_a": 0.0, "ch_b": 1.0, "ch_c": 2.0}


def test_record_queue_positions_statement_count_does_not_grow_with_batch_size(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    small_ids = [f"ch_small_{i}" for i in range(3)]
    large_ids = [f"ch_large_{i}" for i in range(9)]
    for chunk_id in [*small_ids, *large_ids]:
        _mint(store, chunk_id)

    small_count = count_queries(
        engine, lambda: store.queue.record_queue_positions([(c, float(i)) for i, c in enumerate(small_ids)], at=_T0)
    )
    large_count = count_queries(
        engine, lambda: store.queue.record_queue_positions([(c, float(i)) for i, c in enumerate(large_ids)], at=_T0)
    )

    assert small_count == large_count


def test_record_backlog_positions_skips_a_chunk_promoted_since_candidates_were_resolved(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _mint(store, "ch_a")
    _mint(store, "ch_promoted")
    # Simulates the race: the caller resolved backlog candidates before this promote
    # committed, so the reorder must not override the fresh tail stamp it landed.
    store.queue.record_promote("ch_promoted", at=_T0)

    store.queue.record_backlog_positions([("ch_a", 0.0), ("ch_promoted", 1.0)], at=_T0)

    positions = store.queue.queue_positions()
    assert positions.get("ch_a") == 0.0
    assert "ch_promoted" not in positions


def test_record_backlog_positions_guard_is_one_bulk_read_not_one_per_pair(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    small_ids = [f"ch_small_{i}" for i in range(3)]
    large_ids = [f"ch_large_{i}" for i in range(9)]
    for chunk_id in [*small_ids, *large_ids]:
        _mint(store, chunk_id)

    small_count = count_queries(
        engine, lambda: store.queue.record_backlog_positions([(c, float(i)) for i, c in enumerate(small_ids)], at=_T0)
    )
    large_count = count_queries(
        engine, lambda: store.queue.record_backlog_positions([(c, float(i)) for i, c in enumerate(large_ids)], at=_T0)
    )

    assert small_count == large_count
