"""``ChunkRecordStore``'s batch reads — ``get_many``, ``graph_id_of_many``, and
``list_ready``/``list_not_ready``'s required ``statuses`` (component tier).

Proves each batch read matches its singular sibling for a normal, ephemeral, and unminted
id, stays correct across a lowered ``BATCH_SIZE`` boundary, and that filtering over a
given ``statuses`` map costs no more than ``list_all`` alone — no facts collaborator."""

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
from tests.support import chunk_stores, count_queries, migrate_to, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path: Path) -> tuple[ChunkStores, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    return chunk_stores(engine, FixedClock(_T0)), engine


def _mint(store: ChunkStores, chunk_id: str, *, work_refs: list[WorkRef] | None = None) -> None:
    store.record.mint(Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=work_refs or [], minted_at=_T0))


def test_get_many_matches_get_across_a_normal_ephemeral_and_unknown_id(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _mint(store, "ch_live", work_refs=[WorkRef(source="default", ref="1")])
    _mint(store, "ch_grouped")
    with engine.begin() as conn:
        record_grouped_row_conn(conn, "ch_grouped", grouped_into="ch_live", at=_T0)
    _mint(store, "ch_deleted")
    with engine.begin() as conn:
        record_deleted_row(conn, "ch_deleted", by="op", at=_T0)

    result = store.record.get_many(["ch_live", "ch_grouped", "ch_deleted", "ch_never_minted"])

    assert set(result) == {"ch_live"}
    assert result["ch_live"] == store.record.get("ch_live")


def test_get_many_of_no_ids_is_empty(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)

    assert store.record.get_many([]) == {}


def test_get_many_matches_get_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    ids = [f"ch_batch_{i}" for i in range(7)]  # 3 batches of size 3, 3, 1 under the lowered cap
    for chunk_id in ids:
        _mint(store, chunk_id, work_refs=[WorkRef(source="default", ref=chunk_id)])

    result = store.record.get_many(ids)

    assert set(result) == set(ids)
    for chunk_id in ids:
        assert result[chunk_id] == store.record.get(chunk_id)


def test_graph_id_of_many_matches_per_id_resolution_across_ephemeral_and_unknown_ids(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _mint(store, "ch_live")
    _mint(store, "ch_grouped")
    with engine.begin() as conn:
        record_grouped_row_conn(conn, "ch_grouped", grouped_into="ch_live", at=_T0)

    result = store.record.graph_id_of_many(["ch_live", "ch_grouped", "ch_never_minted"])

    assert result == {"ch_live": "gr_1"}


def test_graph_id_of_many_matches_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    ids = [f"ch_batch_{i}" for i in range(7)]
    for chunk_id in ids:
        _mint(store, chunk_id)

    result = store.record.graph_id_of_many(ids)

    assert result == dict.fromkeys(ids, "gr_1")


def test_list_ready_and_list_not_ready_filter_by_the_given_statuses(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _mint(store, "ch_ready")
    store.queue.record_promote("ch_ready", at=_T0)
    _mint(store, "ch_not_ready")

    statuses = store.facts.load_all_statuses()

    assert [c.chunk_id for c in store.record.list_ready(statuses=statuses)] == ["ch_ready"]
    assert [c.chunk_id for c in store.record.list_not_ready(statuses=statuses)] == ["ch_not_ready"]


def test_list_ready_with_statuses_issues_no_statement_against_the_facts_seam(tmp_path: Path) -> None:
    store, engine = _store(tmp_path)
    _mint(store, "ch_ready")
    store.queue.record_promote("ch_ready", at=_T0)
    _mint(store, "ch_not_ready")
    statuses = store.facts.load_all_statuses()

    # The baseline: `list_all`'s own cost alone, with no facts read at all — supplying
    # `statuses` must not add a single statement beyond it.
    baseline = count_queries(engine, store.record.list_all)
    with_statuses = count_queries(engine, lambda: store.record.list_ready(statuses=statuses))

    assert with_statuses == baseline
