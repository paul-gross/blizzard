"""``list_open_decisions`` lists only gates still awaiting a resolution: a gate a transition
closed undecided, or whose chunk is stopped or done, has left the open list (component tier)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import Chunk, DecisionChoice
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from tests.support import chunk_stores, migrate_to, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store_with_gate(tmp_path: Path, chunk_id: str = "ch_1") -> ChunkStores:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    store = chunk_stores(engine, FixedClock(_T0))
    store.record.mint(Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0))
    store.decisions.record_decision(
        imposed_by_runner_id=None,
        decision_id="dec_1",
        chunk_id=chunk_id,
        node_id="nd_1",
        node_name="n",
        epoch=1,
        choices=[DecisionChoice(name="ok", description="d")],
        at=_T0,
        artifacts=[],
        proposals=[],
        admission=EpochAdmission.AT_OR_ABOVE,
    )
    return store


def _transition(store: ChunkStores, *, to_node_id: str, decision_id: str | None) -> None:
    store.movement.record_transition(
        transition_id="tr_1",
        chunk_id="ch_1",
        from_node_id="nd_1",
        to_node_id=to_node_id,
        choice_name="ok",
        epoch=1,
        runner_id="r1",
        at=_T0,
        artifacts=[],
        proposals=[],
        decision_id=decision_id,
        admission=EpochAdmission.AT_OR_ABOVE,
    )


def _open_ids(store: ChunkStores) -> list[str]:
    return [d.decision_id for d in store.decisions.list_open_decisions()]


def test_an_unresolved_gate_on_a_live_chunk_is_open(tmp_path: Path) -> None:
    assert _open_ids(_store_with_gate(tmp_path)) == ["dec_1"]


def test_a_gate_a_transition_closed_undecided_leaves_the_open_list(tmp_path: Path) -> None:
    store = _store_with_gate(tmp_path)
    _transition(store, to_node_id="nd_2", decision_id="dec_1")

    assert _open_ids(store) == []


def test_a_gate_on_a_stopped_chunk_leaves_the_open_list(tmp_path: Path) -> None:
    store = _store_with_gate(tmp_path)
    with store.exclusive.locked(["ch_1"]) as handle:
        store.lifecycle.record_stop_locked(handle, "ch_1", by="op", at=_T0)

    assert _open_ids(store) == []


def test_a_gate_on_a_chunk_done_by_a_terminal_transition_leaves_the_open_list(tmp_path: Path) -> None:
    store = _store_with_gate(tmp_path)
    _transition(store, to_node_id=RESERVED_TERMINAL, decision_id=None)

    assert _open_ids(store) == []
