"""``ChunkQueueStore``'s plural position writes (component tier).

``record_queue_positions``/``record_backlog_positions`` each write a whole batch of
``(chunk_id, position)`` pairs in the caller's unit — proves the statement count
stays flat as the batch grows, that the backlog variant's promoted-chunk guard still
skips a currently promoted chunk, as one bulk ``.in_()`` read, and that each refuses a unit
not holding its list's ranking lock."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.queue import BACKLOG_RANKING_LOCK, READY_RANKING_LOCK
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.kernel.transactions import TransactionMisuse
from tests.support import chunk_stores, count_queries, migrate_to, ranking_unit, seed_chunk_record, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path: Path) -> tuple[ChunkStores, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    return chunk_stores(engine, FixedClock(_T0)), engine


def _mint(store: ChunkStores, chunk_id: str) -> None:
    seed_chunk_record(store, Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0))


def _ready(store: ChunkStores, engine: Engine, positions: Sequence[tuple[str, float]]) -> None:
    with ranking_unit(engine) as unit:
        store.queue.record_queue_positions(positions, at=_T0, transaction=unit)


def _backlog(store: ChunkStores, engine: Engine, positions: Sequence[tuple[str, float]]) -> None:
    with ranking_unit(engine, lock=BACKLOG_RANKING_LOCK) as unit:
        store.queue.record_backlog_positions(positions, at=_T0, transaction=unit)


def test_record_queue_positions_writes_an_empty_sequence_as_a_no_op(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)

    _ready(store, engine, [])

    assert store.queue.queue_positions(["ch_a"]) == {}


def test_record_queue_positions_writes_every_pair(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    for chunk_id in ("ch_a", "ch_b", "ch_c"):
        _mint(store, chunk_id)

    _ready(store, engine, [("ch_a", 0.0), ("ch_b", 1.0), ("ch_c", 2.0)])

    assert store.queue.queue_positions(["ch_a", "ch_b", "ch_c"]) == {"ch_a": 0.0, "ch_b": 1.0, "ch_c": 2.0}


def test_record_queue_positions_statement_count_does_not_grow_with_batch_size(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    small_ids = [f"ch_small_{i}" for i in range(3)]
    large_ids = [f"ch_large_{i}" for i in range(9)]
    for chunk_id in [*small_ids, *large_ids]:
        _mint(store, chunk_id)

    _ready(store, engine, [])  # the ranking lock row exists before either count

    small_count = count_queries(engine, lambda: _ready(store, engine, [(c, float(i)) for i, c in enumerate(small_ids)]))
    large_count = count_queries(engine, lambda: _ready(store, engine, [(c, float(i)) for i, c in enumerate(large_ids)]))

    assert small_count == large_count


def test_record_backlog_positions_skips_a_currently_promoted_chunk(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _mint(store, "ch_a")
    _mint(store, "ch_promoted")
    store.queue.record_promote("ch_promoted", at=_T0)

    _backlog(store, engine, [("ch_a", 0.0), ("ch_promoted", 1.0)])

    positions = store.queue.queue_positions(["ch_a", "ch_promoted"])
    assert positions.get("ch_a") == 0.0
    assert "ch_promoted" not in positions


def test_record_backlog_positions_guard_is_one_bulk_read_not_one_per_pair(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    small_ids = [f"ch_small_{i}" for i in range(3)]
    large_ids = [f"ch_large_{i}" for i in range(9)]
    for chunk_id in [*small_ids, *large_ids]:
        _mint(store, chunk_id)

    _backlog(store, engine, [])  # the ranking lock row exists before either count

    small_count = count_queries(
        engine, lambda: _backlog(store, engine, [(c, float(i)) for i, c in enumerate(small_ids)])
    )
    large_count = count_queries(
        engine, lambda: _backlog(store, engine, [(c, float(i)) for i, c in enumerate(large_ids)])
    )

    assert small_count == large_count


@pytest.mark.parametrize(
    ("write", "held"),
    [("record_queue_positions", BACKLOG_RANKING_LOCK), ("record_backlog_positions", READY_RANKING_LOCK)],
)
def test_a_position_write_refuses_a_unit_not_holding_its_lists_ranking_lock(
    tmp_path: Path, write: str, held: tuple[str, str]
) -> None:
    store, engine = _store(tmp_path)
    _mint(store, "ch_a")

    with pytest.raises(TransactionMisuse), ranking_unit(engine, lock=held) as unit:
        getattr(store.queue, write)([("ch_a", 0.0)], at=_T0, transaction=unit)

    assert store.queue.queue_positions(["ch_a"]) == {}
