"""Which chunk a hub-advance may drive — parked at a generic hub command node, at a status the
hub-advance verb is legal from — pinned by value on loaded facts."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import CHUNK_VERB_LEGALITY, ChunkFacts, ChunkVerb, TransitionFact
from blizzard.hub.domain.graph.model import Node, RunStep
from tests.support import make_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _node(node_id: str, executor: Executor, run: list[RunStep]) -> Node:
    return Node(
        node_id=node_id,
        graph_id="gr_a",
        name=node_id.removeprefix("nd_"),
        executor=executor,
        prompt="p",
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        choices=[],
        run=run,
    )


_BUILD = _node("nd_build", Executor.RUNNER, [])
_SHIP = _node("nd_ship", Executor.HUB, [RunStep(command="make ship")])
_GRAPH = make_graph("gr_a", "work", entry_node_id="nd_build", nodes=[_BUILD, _SHIP])


def _at(node: Node, **facts: object) -> ChunkFacts:
    moved = TransitionFact(to_node_id=node.node_id, to_node_executor=node.executor, epoch=1, recorded_at=_T0)
    return replace(ChunkFacts(minted=True, promoted=True, transitions=[moved]), **facts)


def test_hub_advance_is_legal_from_every_live_status_but_not_ready() -> None:
    assert CHUNK_VERB_LEGALITY[ChunkVerb.HUB_ADVANCE] == frozenset(ChunkStatus) - {
        ChunkStatus.NOT_READY,
        ChunkStatus.STOPPED,
        ChunkStatus.DONE,
    }


def test_a_chunk_delivering_at_a_hub_command_node_is_driven() -> None:
    facts = _at(_SHIP)
    assert facts.status() is ChunkStatus.DELIVERING
    assert facts.hub_advance_node(_GRAPH) == _SHIP


def test_a_chunk_at_a_runner_node_or_unmoved_is_not_driven() -> None:
    assert _at(_BUILD).hub_advance_node(_GRAPH) is None
    assert ChunkFacts(minted=True, promoted=True).hub_advance_node(_GRAPH) is None


@pytest.mark.parametrize(
    ("facts", "status"),
    [
        (_at(_SHIP, promoted=False), ChunkStatus.NOT_READY),
        (_at(_SHIP, stopped=True, stopped_at=_T0), ChunkStatus.STOPPED),
        (_at(_SHIP, operator_completed=True, operator_completed_at=_T0), ChunkStatus.DONE),
    ],
)
def test_a_chunk_resting_unpromoted_or_ended_at_a_hub_command_node_is_not_driven(
    facts: ChunkFacts, status: ChunkStatus
) -> None:
    assert facts.status() is status
    assert facts.hub_advance_node(_GRAPH) is None
