"""``list_open_questions`` lists only questions still awaiting an answer: an answered question,
or one whose chunk is stopped or done, has left the open list (component tier)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb, verb_legal_from
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from tests.support import chunk_stores, migrate_to, seed_chunk_record, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store_with_question(tmp_path: Path, chunk_id: str = "ch_1") -> ChunkStores:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    store = chunk_stores(engine, FixedClock(_T0))
    seed_chunk_record(store, Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0))
    store.questions.record_question(
        question_id="qn_1",
        chunk_id=chunk_id,
        node_id="nd_1",
        session_id=None,
        runner_id="r1",
        epoch=1,
        question="continue?",
        options=[],
        asked_at=_T0,
        admission=EpochAdmission.AT_OR_ABOVE,
    )
    return store


def _open_ids(store: ChunkStores) -> list[str]:
    return [q.question_id for q in store.questions.list_open_questions()]


def _stop(store: ChunkStores) -> None:
    with store.exclusive.locked(["ch_1"]) as handle:
        store.lifecycle.record_stop_locked(handle, "ch_1", by="op", at=_T0)


def _finish(store: ChunkStores) -> None:
    store.movement.record_transition(
        transition_id="tr_1",
        chunk_id="ch_1",
        from_node_id="nd_1",
        to_node_id=RESERVED_TERMINAL,
        choice_name="ok",
        epoch=1,
        runner_id="r1",
        at=_T0,
        artifacts=[],
        decision_id=None,
        admission=EpochAdmission.AT_OR_ABOVE,
    )


def test_an_unanswered_question_on_a_live_chunk_is_open(tmp_path: Path) -> None:
    assert _open_ids(_store_with_question(tmp_path)) == ["qn_1"]


def test_an_answered_question_leaves_the_open_list(tmp_path: Path) -> None:
    store = _store_with_question(tmp_path)
    store.questions.answer_question("qn_1", answer="yes", answered_by="op", at=_T0)

    assert _open_ids(store) == []


def test_a_question_on_a_stopped_chunk_leaves_the_open_list(tmp_path: Path) -> None:
    store = _store_with_question(tmp_path)
    _stop(store)

    assert _open_ids(store) == []


def test_a_question_on_a_chunk_done_by_a_terminal_transition_leaves_the_open_list(tmp_path: Path) -> None:
    store = _store_with_question(tmp_path)
    _finish(store)

    assert _open_ids(store) == []


@pytest.mark.parametrize("end", [None, _stop, _finish], ids=["live", "stopped", "done"])
def test_the_open_lists_ended_chunk_filter_agrees_with_the_models_answer_window(
    tmp_path: Path, end: Callable[[ChunkStores], None] | None
) -> None:
    store = _store_with_question(tmp_path)
    if end is not None:
        end(store)

    status = ChunkFacts.or_default(store.facts.load_facts("ch_1")).status()

    assert ("qn_1" in _open_ids(store)) is verb_legal_from(ChunkVerb.ANSWER_QUESTION, status)
