"""``node_progress`` and ``order_nodes`` (unit tier) — a chunk's progress through its graph, one reading per node in
start-to-finish order, read from its visits alone, and as a chunk read renders it; ``FlowFact.verdict`` and
``FlowFact.is_wait``, what a turn judged of its submission; and ``bounce_withdrew``, whether a bounce's turn withdrew
its submission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from blizzard.foundation.graph_progress_state import GraphProgressState
from blizzard.foundation.hub_event_types import FlowChangeKind
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.api.chunk_views import ChunkView
from blizzard.hub.domain.chunk.model import BounceFact, ChunkFacts, MigrationFact, TransitionFact
from blizzard.hub.domain.delivery.flows import (
    FlowFact,
    SubmissionClosure,
    SubmissionRecord,
    bounce_is_rework,
    bounce_withdrew,
)
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


def test_discovery_order_breaks_a_tie_at_equal_depth() -> None:
    fork = _graph(["plan", "web", "api", "join"], ["plan>web", "plan>api", "web>join", "api>join"])
    assert order_nodes(fork) == ["nd_plan", "nd_web", "nd_api", "nd_join"]


def test_an_entry_missing_from_the_graph_leaves_every_node_in_the_graphs_own_order() -> None:
    missing = make_graph("gr_1", "dev", entry_node_id="nd_missing", nodes=_DEV.nodes, edges=_DEV.edges)
    assert order_nodes(missing) == ["nd_build", "nd_review", "nd_land", "nd_orphan"]


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
    fact = FlowFact(
        id=1,
        flow_id="flw_1",
        submission_id="sub_1",
        chunk_id="ch_1",
        kind=kind,
        at=_AT,
        outcome=outcome,
        closure=outcome,
    )
    assert fact.verdict() == verdict


@pytest.mark.parametrize(("outcome", "closure"), [("failure", None), ("landed", None), ("failure", "stopped")])
def test_a_turn_whose_answer_closed_nothing_judges_nothing(outcome: str, closure: str | None) -> None:
    fact = FlowFact(
        id=1,
        flow_id="flw_1",
        submission_id="sub_1",
        chunk_id="ch_1",
        kind=FlowChangeKind.TURN_ENDED,
        at=_AT,
        outcome=outcome,
        closure=closure,
    )
    assert fact.verdict() is None


@pytest.mark.parametrize(
    ("kind", "outcome", "wait"),
    [
        ("turn-ended", "pending", True),
        ("turn-ended", "awaiting-merge", True),
        ("turn-ended", "cut-off", False),
        ("turn-ended", "escalated", False),
        ("turn-ended", "interrupted", False),
        ("turn-ended", "failure", False),
        ("withdrawn", "pending", False),
    ],
)
def test_only_a_turn_ended_on_a_reserved_wait_is_a_wait(kind: FlowChangeKind, outcome: str, wait: bool) -> None:
    fact = FlowFact(id=1, flow_id="flw_1", submission_id="sub_1", chunk_id="ch_1", kind=kind, at=_AT, outcome=outcome)
    assert fact.is_wait() is wait


def _submission(epoch: int, closed: str | None) -> SubmissionRecord:
    return SubmissionRecord(
        submission_id=f"sub_{epoch}",
        node_id="nd_merge",
        epoch=epoch,
        generation=0,
        arrival=epoch,
        submitted_at=_AT,
        closure=SubmissionClosure(outcome=closed, closed_at=_AT) if closed is not None else None,
    )


@pytest.mark.parametrize(
    ("submissions", "withdrew"),
    [
        ([_submission(3, "failure")], True),
        ([_submission(3, "conflict")], True),
        ([_submission(3, None)], False),
        ([_submission(3, "stopped")], False),
        ([_submission(2, "failure")], False),
        ([], False),
    ],
)
def test_a_bounce_withdrew_only_the_submission_its_own_visit_closed_under_an_answer(
    submissions: list[SubmissionRecord], withdrew: bool
) -> None:
    bounce = BounceFact(epoch=4, cause="failure", envelope="{}", recorded_at=_AT)
    assert bounce_withdrew(bounce, submissions) is withdrew


@dataclass
class _Names:
    """The graph names a chunk read resolves against: one graph, ``None`` for any other id."""

    graph_: Graph | None

    def graph(self, graph_id: str | None) -> Graph | None:
        return self.graph_ if graph_id == "gr_1" else None

    def node_name(self, graph_id: str | None, node_id: str | None) -> str | None:
        return node_id.removeprefix("nd_").removeprefix("old_") if node_id is not None else None


def _chunk_progress(graph_id: str, facts: ChunkFacts, current: str | None = None) -> list[tuple[str, str]]:
    view = ChunkView(
        services=cast(Any, None),
        chunk=cast(Any, SimpleNamespace(graph_id=graph_id)),
        facts=facts,
        names=cast(Any, _Names(_DEV)),
    )
    # The public `summary()` reaches services this unit-tier view leaves unset; the progress projection alone is under test.
    progress = view._graph_progress(current)  # pyright: ignore[reportPrivateUsage]  # see the comment above
    return [(node.name, str(node.state)) for node in progress]


def _transition(epoch: int, src: str, dst: str) -> TransitionFact:
    return TransitionFact(
        to_node_id=f"nd_{dst}",
        to_node_executor=Executor.RUNNER,
        epoch=epoch,
        recorded_at=_AT,
        from_node_id=f"nd_{src}",
        graph_id="gr_1",
    )


def test_a_chunk_read_takes_a_migration_as_a_visit_interleaved_by_epoch_and_named_in_its_own_graph() -> None:
    facts = ChunkFacts(
        minted=True,
        transitions=[_transition(1, "build", "review"), _transition(4, "build", "review")],
        migrations=[
            MigrationFact(
                from_node_id="old_review",
                from_graph_id="gr_old",
                to_graph_id="gr_1",
                landed_node_id="nd_build",
                choice_name="rework",
                model=None,
                epoch=2,
                recorded_at=_AT,
            )
        ],
    )
    assert _chunk_progress("gr_1", facts) == [
        ("build", "passed"),
        ("review", "failed"),
        ("land", "unvisited"),
        ("orphan", "unvisited"),
    ]


def test_a_chunk_read_over_an_unresolvable_graph_has_no_progress() -> None:
    assert _chunk_progress("gr_gone", ChunkFacts(minted=True)) == []


@pytest.mark.parametrize(
    ("kind", "outcome", "closure", "rework"),
    [
        ("turn-ended", "conflict", "conflict", True),
        ("turn-ended", "conflict", None, False),
        ("turn-ended", "conflict", "stopped", False),
        ("turn-ended", "failure", "failure", False),
        ("turn-ended", "landed", "landed", False),
        ("turn-ended", "pending", None, False),
        ("withdrawn", "conflict", "conflict", False),
    ],
)
def test_only_a_turn_that_routed_its_submission_back_on_a_rework_answer_is_rework(
    kind: FlowChangeKind, outcome: str, closure: str | None, rework: bool
) -> None:
    fact = FlowFact(
        id=1,
        flow_id="flw_1",
        submission_id="sub_1",
        chunk_id="ch_1",
        kind=kind,
        at=_AT,
        outcome=outcome,
        closure=closure,
    )
    assert fact.is_rework() is rework


@pytest.mark.parametrize(
    ("cause", "closed", "rework"),
    [
        ("conflict", "conflict", True),
        ("conflict", None, False),
        ("failure", "failure", False),
        ("poll-timeout", "failure", False),
    ],
)
def test_only_a_bounce_routed_back_on_a_rework_answer_is_rework(cause: str, closed: str | None, rework: bool) -> None:
    bounce = BounceFact(epoch=4, cause=cause, envelope="{}", recorded_at=_AT)
    assert bounce_is_rework(bounce, [_submission(3, closed)]) is rework


def test_a_poll_timeout_bounce_withdrew_the_submission_its_failure_route_closed() -> None:
    bounce = BounceFact(epoch=4, cause="poll-timeout", envelope="{}", recorded_at=_AT)
    assert bounce_withdrew(bounce, [_submission(3, "failure")]) is True
