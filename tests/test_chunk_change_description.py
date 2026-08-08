"""``ChunkChange.of`` (unit tier) — the pure derivation behind a ``chunk-changed``
frame's prev/current node names and graph id (issue #212). Built straight from
:class:`ChunkFacts` + :class:`Graph` literals — no store, no hub harness.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.graph import Executor, Graph, JudgedBy, Node, SessionMode
from blizzard.hub.domain.work import (
    Chunk,
    ChunkChange,
    ChunkFacts,
    ChunkStatus,
    RouteCreatedFact,
    TransitionFact,
    WorkRef,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _node(node_id: str, name: str, *, graph_id: str = "gr_1") -> Node:
    return Node(
        node_id=node_id,
        graph_id=graph_id,
        name=name,
        executor=Executor.RUNNER,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        mode=None,
    )


def _graph(graph_id: str = "gr_1", *, entry_node_id: str = "nd_a") -> Graph:
    return Graph(
        graph_id=graph_id,
        name="t",
        entry_node_id=entry_node_id,
        nodes=[_node("nd_a", "build", graph_id=graph_id), _node("nd_b", "review", graph_id=graph_id)],
        edges=[],
        created_at=_T0,
    )


def _chunk(graph_id: str = "gr_1") -> Chunk:
    return Chunk(chunk_id="ch_1", graph_id=graph_id, work_refs=[WorkRef(source="default", ref="1")], minted_at=_T0)


def test_no_transitions_omits_prev_node_and_reads_entry_node() -> None:
    graph = _graph()
    change = ChunkChange.of(_chunk(), graph, ChunkFacts(minted=True), prev_status=None, runner_id=None, cause="minted")
    assert change.prev_node is None
    assert change.node == "build"  # graph.entry_node_id resolves to nd_a
    assert change.status == ChunkStatus.NOT_READY.value
    assert change.graph_id == "gr_1"


def test_normal_transition_reports_from_and_to_node_names() -> None:
    graph = _graph()
    facts = ChunkFacts(
        minted=True,
        promoted=True,
        routes_created=[RouteCreatedFact(created_at=_T0)],
        transitions=[
            TransitionFact(
                to_node_id="nd_b",
                to_node_executor=Executor.RUNNER,
                epoch=1,
                recorded_at=_T0,
                from_node_id="nd_a",
                graph_id="gr_1",
            )
        ],
    )
    change = ChunkChange.of(_chunk(), graph, facts, prev_status="running", runner_id="runner-a", cause="node-completed")
    assert change.prev_node == "build"
    assert change.node == "review"
    assert change.runner_id == "runner-a"
    assert change.prev_status == "running"
    assert change.cause == "node-completed"


def test_transition_from_a_different_graph_resolves_against_from_graph() -> None:
    """A post-migration chunk's newest movement is on the new graph, but the transition
    that carried it there was recorded under the old graph's id — ``prev_node`` must
    resolve against that old graph, not the chunk's current pin."""
    old_graph = _graph("gr_old")
    new_graph = _graph("gr_new")
    facts = ChunkFacts(
        minted=True,
        transitions=[
            TransitionFact(
                to_node_id="nd_b",
                to_node_executor=Executor.RUNNER,
                epoch=1,
                recorded_at=_T0,
                from_node_id="nd_a",
                graph_id="gr_old",
            )
        ],
    )
    change = ChunkChange.of(
        _chunk("gr_new"), new_graph, facts, prev_status=None, runner_id=None, cause="migrated", from_graph=old_graph
    )
    assert change.prev_node == "build"  # resolved against gr_old, not gr_new
    assert change.graph_id == "gr_new"


def test_unresolvable_from_node_id_omits_prev_node_without_raising() -> None:
    graph = _graph()
    facts = ChunkFacts(
        minted=True,
        transitions=[
            TransitionFact(
                to_node_id="nd_b",
                to_node_executor=Executor.RUNNER,
                epoch=1,
                recorded_at=_T0,
                from_node_id="nd_ghost",
                graph_id="gr_1",
            )
        ],
    )
    change = ChunkChange.of(_chunk(), graph, facts, prev_status=None, runner_id=None, cause="node-completed")
    assert change.prev_node is None
    assert change.node == "review"
