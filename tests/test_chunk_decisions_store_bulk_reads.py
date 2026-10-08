"""``ChunkDecisionsStore``'s SQL-filtered reads (component tier).

Proves ``list_open_decisions``/``decision_for_chunk`` filter in SQL and hydrate in bounded
batches, with a flat query count as fleet/history size grows."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import Chunk, DecisionChoice
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from tests.support import chunk_stores, count_queries, migrate_to, seed_chunk_record, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path: Path) -> tuple[ChunkStores, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    return chunk_stores(engine, FixedClock(_T0)), engine


def _mint(store: ChunkStores, chunk_id: str) -> None:
    seed_chunk_record(store, Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0))


def _record_decision(store: ChunkStores, chunk_id: str, decision_id: str, *, at: datetime = _T0) -> None:
    store.decisions.record_decision(
        imposed_by_runner_id=None,
        decision_id=decision_id,
        chunk_id=chunk_id,
        node_id="nd_1",
        node_name="n",
        epoch=1,
        choices=[DecisionChoice(name="ok", description="d")],
        at=at,
        artifacts=[],
        admission=EpochAdmission.AT_OR_ABOVE,
    )


def _resolve(store: ChunkStores, decision_id: str, *, choice: str = "pass", by: str = "op") -> None:
    store.decisions.record_decision_resolution(decision_id, choice=choice, resolved_by=by, at=_T0)


def _transition(store: ChunkStores, chunk_id: str, decision_id: str, *, transition_id: str) -> None:
    """Closes ``decision_id`` — the movement fact `decision_for_chunk`'s
    ``_not_closed_clause`` reads to drop it from a chunk's live decision."""
    store.movement.record_transition(
        transition_id=transition_id,
        chunk_id=chunk_id,
        from_node_id="nd_1",
        to_node_id="nd_2",
        choice_name="pass",
        epoch=1,
        runner_id="r1",
        at=_T0,
        artifacts=[],
        decision_id=decision_id,
        admission=EpochAdmission.AT_OR_ABOVE,
    )


def test_list_open_decisions_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, small_engine = _store(tmp_path / "small")
    large, large_engine = _store(tmp_path / "large")
    for i in range(3):
        _mint(small, f"ch_{i}")
        _record_decision(small, f"ch_{i}", f"dec_{i}")
    for i in range(9):
        _mint(large, f"ch_{i}")
        _record_decision(large, f"ch_{i}", f"dec_{i}")

    small_count = count_queries(small_engine, lambda: small.decisions.list_open_decisions())
    large_count = count_queries(large_engine, lambda: large.decisions.list_open_decisions())

    assert len(small.decisions.list_open_decisions()) == 3
    assert len(large.decisions.list_open_decisions()) == 9
    assert small_count == large_count


def test_decision_for_chunk_query_count_is_independent_of_that_chunks_history_length(tmp_path: Path) -> None:
    """A chunk's own closed history shouldn't cost more queries the longer it gets — the
    ``NOT EXISTS`` filter drops every closed decision in SQL, before hydration."""
    (tmp_path / "short").mkdir()
    (tmp_path / "long").mkdir()
    short, short_engine = _store(tmp_path / "short")
    long_, long_engine = _store(tmp_path / "long")
    _mint(short, "ch_1")
    for i in range(3):
        _record_decision(short, "ch_1", f"dec_closed_{i}")
        _resolve(short, f"dec_closed_{i}")
        _transition(short, "ch_1", f"dec_closed_{i}", transition_id=f"tr_{i}")
    _record_decision(short, "ch_1", "dec_live")
    _mint(long_, "ch_1")
    for i in range(9):
        _record_decision(long_, "ch_1", f"dec_closed_{i}")
        _resolve(long_, f"dec_closed_{i}")
        _transition(long_, "ch_1", f"dec_closed_{i}", transition_id=f"tr_{i}")
    _record_decision(long_, "ch_1", "dec_live")

    short_count = count_queries(short_engine, lambda: short.decisions.decision_for_chunk("ch_1"))
    long_count = count_queries(long_engine, lambda: long_.decisions.decision_for_chunk("ch_1"))

    short_result = short.decisions.decision_for_chunk("ch_1")
    long_result = long_.decisions.decision_for_chunk("ch_1")
    assert short_result is not None and short_result.decision_id == "dec_live"
    assert long_result is not None and long_result.decision_id == "dec_live"
    assert short_count == long_count


def test_list_open_decisions_and_decision_for_chunk_across_the_four_decision_shapes(tmp_path: Path) -> None:
    """A resolved decision, an open one, a transitioned chunk, and a chunk with several
    decisions across its history — the shapes both SQL-filtered reads must still
    resolve correctly, over a real migrated store."""
    store, _ = _store(tmp_path)
    _mint(store, "ch_resolved")
    _mint(store, "ch_open")
    _mint(store, "ch_transitioned")
    _mint(store, "ch_history")

    _record_decision(store, "ch_resolved", "dec_resolved")
    _resolve(store, "dec_resolved")

    _record_decision(store, "ch_open", "dec_open")

    _record_decision(store, "ch_transitioned", "dec_transitioned")
    _resolve(store, "dec_transitioned")
    _transition(store, "ch_transitioned", "dec_transitioned", transition_id="tr_transitioned")

    _record_decision(store, "ch_history", "dec_history_old")
    _resolve(store, "dec_history_old")
    _transition(store, "ch_history", "dec_history_old", transition_id="tr_history")
    _record_decision(store, "ch_history", "dec_history_new")

    open_ids = {d.decision_id for d in store.decisions.list_open_decisions()}
    assert open_ids == {"dec_open", "dec_history_new"}

    resolved = store.decisions.decision_for_chunk("ch_resolved")
    assert resolved is not None
    assert resolved.decision_id == "dec_resolved"
    assert resolved.resolved_choice == "pass"
    assert not resolved.transitioned

    open_decision = store.decisions.decision_for_chunk("ch_open")
    assert open_decision is not None
    assert open_decision.decision_id == "dec_open"
    assert not open_decision.resolved

    assert store.decisions.decision_for_chunk("ch_transitioned") is None

    history = store.decisions.decision_for_chunk("ch_history")
    assert history is not None
    assert history.decision_id == "dec_history_new"
