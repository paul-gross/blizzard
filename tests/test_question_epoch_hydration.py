"""The chunk-facts read carries each question's attempt epoch, so the open-question
completion guard sees the question the attempt at that epoch asked (component tier)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from tests.support import chunk_stores, migrate_to, seed_chunk_record, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_a_loaded_question_carries_the_epoch_it_was_asked_at(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    store = chunk_stores(engine, FixedClock(_T0))
    seed_chunk_record(store, Chunk(chunk_id="ch_1", graph_id="gr_1", work_refs=[], minted_at=_T0))
    store.queue.record_promote("ch_1", at=_T0)
    store.questions.record_question(
        question_id="qn_1",
        chunk_id="ch_1",
        node_id=None,
        session_id=None,
        runner_id="r",
        epoch=3,
        question="which branch?",
        options=[],
        asked_at=_T0,
        admission=EpochAdmission.AT_OR_ABOVE,
    )

    facts = store.facts.load_facts("ch_1")

    assert facts is not None
    assert [q.epoch for q in facts.questions] == [3]
    assert [q.question_id for q in facts.open_questions_at(3)] == ["qn_1"]
    assert facts.open_questions_at(2) == []
