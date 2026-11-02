"""``node_progress`` and ``order_nodes`` (unit tier) — a chunk's progress through its graph, one reading per node in
start-to-finish order, read from its visits alone; and ``FlowFact.verdict``, a turn's verdict on its submission."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.graph_progress_state import GraphProgressState
from blizzard.foundation.hub_event_types import FlowChangeKind
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.delivery.flows import FlowFact
from blizzard.hub.domain.graph.model import Edge, Graph, Node
from blizzard.hub.domain.graph.progress import NodeVisit, node_progress, order_nodes
from tests.support import make_graph

pytestmark = pytest.mark.unit

_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _node(name: str, prefix: str = "") -> Node:
    return Node(
        node_id=f"nd_{prefix}{name}",
        graph_id="gr_1",
        name=name,
        executor=Executor.RUNNER,
        prompt=None,
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
    )


def _graph(names: list[str], edges: list[str], prefix: str = "") -> Graph:
    """A graph over ``names``, entered at the first, with an edge per ``from>to`` pair."""
    return make_graph(
        "gr_1",
        "dev",
        entry_node_id=f"nd_{prefix}{names[0]}",
        nodes=[_node(name, prefix) for name in names],
        edges=[
            Edge(from_node_id=f"nd_{prefix}{pair.split('>')[0]}", choice_id=f"c{i}", to_node_name=pair.split(">")[1])
            for i, pair in enumerate(edges)
        ],
    )


#: build, review (which can send it back to build), land, and an orphan the entry never reaches.
_DEV = _graph(["build", "review", "land", "orphan"], ["build>review", "review>build", "review>land", "land>done"])


def _step(epoch: int, src: str | None, dst: str | None) -> NodeVisit:
    return NodeVisit(epoch=epoch, from_name=src, to_name=dst)


def _states(graph: Graph, visits: list[NodeVisit], current: str | None = None) -> dict[str, str]:
    return {node.name: node.state.value for node in node_progress(graph, visits, current)}


def test_order_runs_start_to_finish_whatever_order_the_graph_lists_its_nodes() -> None:
    shuffled = make_graph("gr_1", "dev", entry_node_id="nd_build", nodes=list(reversed(_DEV.nodes)), edges=_DEV.edges)
    assert order_nodes(shuffled) == ["nd_build", "nd_review", "nd_land", "nd_orphan"]


def test_a_rework_loop_never_pushes_its_target_later() -> None:
    looped = _graph(["build", "test", "review"], ["build>test", "test>review", "test>build", "review>build"])
    assert order_nodes(looped) == ["nd_build", "nd_test", "nd_review"]


def test_a_node_ranks_by_its_longest_forward_path() -> None:
    shortcut = _graph(["build", "review", "test"], ["build>review", "build>test", "test>review"])
    assert order_nodes(shortcut) == ["nd_build", "nd_test", "nd_review"]


def test_every_node_reads_unvisited_before_any_visit() -> None:
    assert set(_states(_DEV, []).values()) == {"unvisited"}


def test_a_node_left_forward_passes_and_one_routed_back_fails() -> None:
    assert _states(_DEV, [_step(1, "build", "review")]) == {
        "build": "passed",
        "review": "unvisited",
        "land": "unvisited",
        "orphan": "unvisited",
    }
    assert _states(_DEV, [_step(1, "build", "build"), _step(2, "review", "build")])["review"] == "failed"
    assert _states(_DEV, [_step(1, "land", None)])["land"] == "passed"


def test_only_a_nodes_last_visit_by_epoch_counts_and_the_current_node_reads_current() -> None:
    visits = [_step(4, "review", "land"), _step(2, "review", "build"), _step(3, "build", "review")]
    assert _states(_DEV, visits, "nd_land") == {
        "build": "passed",
        "review": "passed",
        "land": "current",
        "orphan": "unvisited",
    }


def test_visits_match_by_name_and_skip_a_node_the_graph_no_longer_names() -> None:
    v2 = _graph(["build", "review"], ["build>review"], prefix="v2_")
    progress = node_progress(v2, [_step(1, "build", "review"), _step(2, "retired", "build")], "nd_v2_review")
    assert [(n.node_id, n.state) for n in progress] == [
        ("nd_v2_build", GraphProgressState.PASSED),
        ("nd_v2_review", GraphProgressState.CURRENT),
    ]


@pytest.mark.parametrize(
    ("kind", "outcome", "verdict"),
    [
        ("turn-ended", "landed", "landed"),
        ("turn-ended", "conflict", "bounced"),
        ("turn-ended", "failure", "bounced"),
        ("turn-ended", "pending", None),
        ("turn-ended", "awaiting-merge", None),
        ("turn-ended", "cut-off", None),
        ("turn-ended", "escalated", None),
        ("turn-ended", "superseded", None),
        ("withdrawn", "stopped", None),
        ("landed", None, None),
    ],
)
def test_a_turns_verdict_is_landed_bounced_or_none_for_a_turn_that_judged_nothing(
    kind: FlowChangeKind, outcome: str | None, verdict: str | None
) -> None:
    fact = FlowFact(id=1, flow_id="flw_1", submission_id="sub_1", chunk_id="ch_1", kind=kind, at=_AT, outcome=outcome)
    assert fact.verdict() == verdict
