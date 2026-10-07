"""Every SQL form of "the chunk is terminal" agrees with :meth:`ChunkFacts.status` (component tier).

The open-question list, the open-decision list, the write fence and the live prefilter each ask the one
``chunk_is_terminal`` builder, so over a world seeded across every terminal branch they all read the
chunk the way the derivation does. The prefilter may only keep extra residue — never drop a chunk the
derivation calls live."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import insert, select

from blizzard.foundation.chunk_status import TERMINAL_STATUSES
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import fence, lock_chunk_row
from blizzard.hub.store.internal.chunk_terminal_predicates import maybe_live
from tests.support import HubHarness
from tests.support_chunk_world import every_branch, hub_with_graph

pytestmark = pytest.mark.component

_EPOCH = 99


def _park_a_question_and_a_decision_on_every_chunk(hub: HubHarness, chunk_ids: list[str]) -> None:
    at = hub.clock.now()
    with hub.engine.begin() as conn:
        for chunk_id in chunk_ids:
            conn.execute(
                insert(s.questions).values(
                    question_id=f"qn_{chunk_id}",
                    chunk_id=chunk_id,
                    node_id="nd_1",
                    runner_id="r",
                    epoch=_EPOCH,
                    question="continue?",
                    options="[]",
                    asked_at=at,
                )
            )
            conn.execute(
                insert(s.decisions).values(
                    decision_id=f"dec_{chunk_id}",
                    chunk_id=chunk_id,
                    node_id="nd_1",
                    node_name="gate",
                    epoch=_EPOCH,
                    choices=json.dumps([{"name": "ok", "description": "go"}]),
                    submitted_at=at,
                )
            )


def _fence_refuses_as_terminal(hub: HubHarness, chunk_id: str) -> bool:
    with hub.engine.begin() as conn:
        lock_chunk_row(conn, chunk_id)
        refusal = fence(conn, chunk_id, epoch=_EPOCH, admission=EpochAdmission.AT_OR_ABOVE)
    return refusal is not None and refusal.latest is None


def test_every_terminal_form_agrees_with_the_derived_status(tmp_path: Path) -> None:
    hub = hub_with_graph(tmp_path)
    every_branch(hub)
    facts = hub.services.chunks.facts.load_all_facts()
    # A terminal and a non-terminal transition at an identical instant and epoch derive by fact order
    # and no write path records it, so the predicate leaves it live — the one residue.
    residue = {chunk_id for chunk_id in facts if chunk_id.startswith("ch_mixed_tie_")}
    decided = {chunk_id: f.status() for chunk_id, f in facts.items() if chunk_id not in residue}
    live_ids = {chunk_id for chunk_id, status in decided.items() if status not in TERMINAL_STATUSES}
    terminal_ids = set(decided) - live_ids
    assert "ch_migrated_old" in live_ids  # a migration wins its tie with a terminal transition by kind rank
    assert {"ch_tie_old", "ch_done_transition_old", "ch_stopped_old"} <= terminal_ids
    _park_a_question_and_a_decision_on_every_chunk(hub, sorted(facts))

    open_question_chunks = {q.chunk_id for q in hub.services.chunks.questions.list_open_questions()} - residue
    open_decision_chunks = {d.chunk_id for d in hub.services.chunks.decisions.list_open_decisions()} - residue
    with hub.engine.connect() as conn:
        kept = set(conn.execute(select(s.chunks.c.chunk_id).where(maybe_live())).scalars())

    assert open_question_chunks == live_ids
    assert open_decision_chunks == live_ids
    assert live_ids | residue <= kept
    for chunk_id in sorted(decided):
        assert _fence_refuses_as_terminal(hub, chunk_id) is (chunk_id in terminal_ids), chunk_id
