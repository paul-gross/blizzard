"""``ChunkWorkRefsStore.live_holders`` — ``find_live_holder``'s batched sibling
(component tier, blizzard#bulk-read-seams).

Proves the batch read agrees with calling ``find_live_holder`` once per pointer, across
a live holder, a holder grouped away, a holder at a terminal status, and a pointer no
chunk ever held at all — the last three each contributing no entry to the result, not a
``None`` value — and that it stays correct across a lowered ``BATCH_SIZE`` boundary."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunks.stores import ChunkStores
from blizzard.hub.domain.work import Chunk, WorkRef
from blizzard.hub.store.internal import batching as batching_module
from blizzard.hub.store.internal.chunk_rows import record_deleted_row, record_grouped_row_conn
from tests.support import chunk_stores, migrate_to, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path: Path) -> tuple[ChunkStores, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    return chunk_stores(engine, FixedClock(_T0)), engine


def _mint(store: ChunkStores, chunk_id: str, *, work_refs: list[WorkRef]) -> None:
    store.record.mint(Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=work_refs, minted_at=_T0))


def test_live_holders_matches_find_live_holder_across_live_ephemeral_terminal_and_absent_pointers(
    tmp_path: Path,
) -> None:
    store, engine = _store(tmp_path)
    live_ref = WorkRef(source="default", ref="1")
    grouped_ref = WorkRef(source="default", ref="2")
    terminal_ref = WorkRef(source="default", ref="3")
    absent_ref = WorkRef(source="default", ref="9")

    _mint(store, "ch_live", work_refs=[live_ref])
    store.queue.record_promote("ch_live", at=_T0)

    _mint(store, "ch_grouped", work_refs=[grouped_ref])
    with engine.begin() as conn:
        record_grouped_row_conn(conn, "ch_grouped", grouped_into="ch_live", at=_T0)

    _mint(store, "ch_terminal", work_refs=[terminal_ref])
    store.queue.record_promote("ch_terminal", at=_T0)
    store.lifecycle.record_completion("ch_terminal", by="op", at=_T0)

    pointers = [live_ref, grouped_ref, terminal_ref, absent_ref]
    result = store.work_refs.live_holders(pointers)

    assert result == {live_ref: "ch_live"}
    for pointer in pointers:
        expected = store.work_refs.find_live_holder(pointer)
        assert result.get(pointer) == expected


def test_live_holders_of_no_pointers_is_empty(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)

    assert store.work_refs.live_holders([]) == {}


def test_live_holders_matches_find_live_holder_across_a_batch_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    pointers = [WorkRef(source="default", ref=str(i)) for i in range(7)]  # 3 batches of 3, 3, 1
    for i, pointer in enumerate(pointers):
        _mint(store, f"ch_{i}", work_refs=[pointer])
        store.queue.record_promote(f"ch_{i}", at=_T0)

    result = store.work_refs.live_holders(pointers)

    assert {p: c for p, c in result.items()} == {pointer: f"ch_{i}" for i, pointer in enumerate(pointers)}
    for pointer in pointers:
        assert result[pointer] == store.work_refs.find_live_holder(pointer)


def test_live_holders_groups_pointers_by_source_before_batching_refs(tmp_path: Path) -> None:
    """``ref`` alone carries no cross-source uniqueness — two different sources' pointer
    sharing the same ``ref`` string must resolve independently."""
    store, _ = _store(tmp_path)
    ref_a = WorkRef(source="source_a", ref="1")
    ref_b = WorkRef(source="source_b", ref="1")
    _mint(store, "ch_a", work_refs=[ref_a])
    store.queue.record_promote("ch_a", at=_T0)
    _mint(store, "ch_b", work_refs=[ref_b])
    store.queue.record_promote("ch_b", at=_T0)

    result = store.work_refs.live_holders([ref_a, ref_b])

    assert result == {ref_a: "ch_a", ref_b: "ch_b"}
