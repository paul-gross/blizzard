"""One runner's next claim, explained over the matched peek's own walk."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import UTC, datetime
from typing import TypedDict

import pytest

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.foundation.queue_policy import QueueMatchPolicy
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, WorkRef
from blizzard.hub.domain.graph.model import Graph, Node
from blizzard.hub.domain.operations.claim_explanation import PASSED_CAP, ClaimExplanation, explain_next_claim
from blizzard.hub.domain.operations.queue import (
    PassedReason,
    matched_candidates,
    select_matched_entry,
    walk_matched_entries,
)
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.runners.registration import RunnerCapability

_A = ProjectId("proj_a")
_B = ProjectId("proj_b")
_C = ProjectId("proj_c")
_ELSEWHERE = ProjectId("proj_elsewhere")
_NO_GRAPH = "gr_absent"

_GRAPH = Graph(
    graph_id="gr_1",
    name="t",
    entry_node_id="nd_build",
    nodes=[
        Node(
            node_id="nd_build",
            graph_id="gr_1",
            name="build",
            executor=Executor.RUNNER,
            prompt=None,
            checks=[],
            produces=[],
            session=SessionMode.FRESH,
            judged_by=JudgedBy.WORKER,
            retries_max=None,
            retries_exhausted=None,
        )
    ],
    edges=[],
    created_at=datetime(2026, 7, 13, tzinfo=UTC),
)
_CAPABILITIES = (RunnerCapability(harness_id="claude", default=True),)


def _chunk(chunk_id: str, project: ProjectId = _A, *, graph_id: str = "gr_1") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        graph_id=graph_id,
        work_refs=[WorkRef(source="default", ref=chunk_id)],
        minted_at=datetime(2026, 7, 13, tzinfo=UTC),
        project_id=project,
    )


class _Inputs(TypedDict):
    served: Collection[str]
    graphs: Mapping[str, Graph]
    facts: Mapping[str, ChunkFacts]
    capabilities: Sequence[RunnerCapability]
    blocked: Mapping[str, list[str]]


def _inputs(chunks: list[Chunk], blocked: dict[str, list[str]], served: set[str]) -> _Inputs:
    return {
        "served": served,
        "graphs": {_GRAPH.graph_id: _GRAPH},
        "facts": {c.chunk_id: ChunkFacts(minted=True, promoted=True) for c in chunks},
        "capabilities": _CAPABILITIES,
        "blocked": blocked,
    }


def _explain(
    chunks: list[Chunk],
    *,
    blocked: dict[str, list[str]] | None = None,
    served: set[str] | None = None,
    excluded: frozenset[str] = frozenset(),
) -> ClaimExplanation:
    return explain_next_claim(chunks, **_inputs(chunks, blocked or {}, served or {_A}), excluded=excluded)


def test_a_blocked_chunk_is_passed_naming_its_first_prerequisite_and_the_count() -> None:
    chunks = [_chunk("d"), _chunk("p")]
    explained = _explain(chunks, blocked={"d": ["p", "q"]})
    assert explained.next is not None and explained.next.chunk.chunk_id == "p"
    assert [(e.chunk.chunk_id, e.reason, e.prerequisite, e.prerequisite_count) for e in explained.passed] == [
        ("d", PassedReason.BLOCKED, "p", 2)
    ]


def test_a_chunk_the_runner_cannot_run_is_passed_as_ineligible_without_a_prerequisite() -> None:
    explained = _explain([_chunk("x", graph_id=_NO_GRAPH), _chunk("y")])
    assert [(e.chunk.chunk_id, e.reason, e.prerequisite, e.prerequisite_count) for e in explained.passed] == [
        ("x", PassedReason.INELIGIBLE, None, 0)
    ]
    assert explained.next is not None and explained.next.position == 1


def test_held_at_names_each_served_projects_unusable_head_including_one_past_the_hold_choice() -> None:
    chunks = [
        _chunk("a1"),
        _chunk("b1", _B),
        _chunk("c1", _C),
        _chunk("a2"),
        _chunk("c2", _C, graph_id=_NO_GRAPH),
    ]
    # b1 is usable, so the per-project hold chooses it; c's head lies past that choice yet still stops c's hold.
    explained = _explain(chunks, blocked={"a1": ["pre"], "c1": ["pre"]}, served={_A, _B, _C})
    assert explained.if_holding is not None and explained.if_holding.chunk.chunk_id == "b1"
    assert [(e.chunk.chunk_id, e.chunk.project_id, e.position) for e in explained.held_at] == [
        ("a1", _A, 0),
        ("c1", _C, 2),
    ]


def test_a_project_with_a_usable_head_or_no_ready_chunk_has_no_held_at_entry() -> None:
    explained = _explain([_chunk("a1")], served={_A, _B})
    assert explained.held_at == ()


def test_passed_is_capped_while_passed_total_counts_every_passed_chunk() -> None:
    chunks = [_chunk(f"x{i:02}") for i in range(PASSED_CAP + 5)] + [_chunk("ok")]
    explained = _explain(chunks, blocked={c.chunk_id: ["pre"] for c in chunks[:-1]})
    assert PASSED_CAP == 20
    assert len(explained.passed) == PASSED_CAP
    assert [e.chunk.chunk_id for e in explained.passed] == [c.chunk_id for c in chunks[:PASSED_CAP]]
    assert explained.passed_total == PASSED_CAP + 5
    assert explained.next is not None and explained.next.chunk.chunk_id == "ok"


def test_unserved_counts_the_chunks_ahead_of_next_the_walk_never_examined() -> None:
    chunks = [_chunk("e1", _ELSEWHERE), _chunk("e2", _ELSEWHERE), _chunk("a1"), _chunk("e3", _ELSEWHERE)]
    explained = _explain(chunks)
    assert explained.unserved == 2
    assert explained.passed == ()


def test_nothing_claimable_explains_no_next_and_no_hold_choice() -> None:
    explained = _explain([_chunk("a1")], blocked={"a1": ["pre"]})
    assert explained.next is None
    assert explained.if_holding is None
    assert [e.chunk.chunk_id for e in explained.held_at] == ["a1"]


_FIXTURES = [
    pytest.param([_chunk("a1"), _chunk("a2")], {}, {_A}, id="usable-head"),
    pytest.param([_chunk("a1"), _chunk("b1", _B), _chunk("a2")], {"a1": ["p"]}, {_A, _B}, id="blocked-head"),
    pytest.param([_chunk("x", graph_id=_NO_GRAPH), _chunk("b1", _B)], {}, {_A, _B}, id="ineligible-head"),
    pytest.param([_chunk("e1", _ELSEWHERE), _chunk("a1")], {}, {_A}, id="unserved-ahead"),
    pytest.param([_chunk("a1")], {"a1": ["p"]}, {_A}, id="nothing-claimable"),
    pytest.param([_chunk("a1")], {}, set[str](), id="serving-nothing"),
]


@pytest.mark.parametrize(("chunks", "blocked", "served"), _FIXTURES)
def test_next_and_if_holding_are_what_the_matched_peek_selects_on_the_same_inputs(
    chunks: list[Chunk], blocked: dict[str, list[str]], served: set[str]
) -> None:
    inputs = _inputs(chunks, blocked, served)
    explained = explain_next_claim(chunks, **inputs)
    assert explained.next == select_matched_entry(chunks, **inputs, policy=QueueMatchPolicy.PASS_OVER)
    assert explained.if_holding == select_matched_entry(
        chunks,
        **inputs,
        policy=QueueMatchPolicy.HOLD,
        hold_per_project=True,
    )


@pytest.mark.parametrize(("chunks", "blocked", "served"), _FIXTURES)
def test_with_an_excluded_set_next_and_if_holding_are_still_the_matched_peeks_choice(
    chunks: list[Chunk], blocked: dict[str, list[str]], served: set[str]
) -> None:
    excluded = frozenset({chunks[0].chunk_id})
    inputs = _inputs(chunks, blocked, served)
    explained = explain_next_claim(chunks, **inputs, excluded=excluded)
    assert explained.next == select_matched_entry(
        chunks, **inputs, policy=QueueMatchPolicy.PASS_OVER, excluded=excluded
    )
    assert explained.if_holding == select_matched_entry(
        chunks, **inputs, policy=QueueMatchPolicy.HOLD, hold_per_project=True, excluded=excluded
    )


def test_an_excluded_blocked_chunk_is_counted_excluded_and_never_passed_or_held_at() -> None:
    chunks = [_chunk("a1"), _chunk("x", graph_id=_NO_GRAPH), _chunk("a2"), _chunk("a3")]
    explained = _explain(chunks, blocked={"a1": ["pre"]}, excluded=frozenset({"a1", "x"}))
    assert explained.next is not None and (explained.next.chunk.chunk_id, explained.next.position) == ("a2", 2)
    assert explained.passed == ()
    assert explained.held_at == ()
    assert explained.excluded == 2
    assert explained.if_holding is not None and explained.if_holding.chunk.chunk_id == "a2"


def test_an_excluded_chunk_never_stops_a_hold_and_blocked_still_precedes_ineligible() -> None:
    chunks = [_chunk("a1"), _chunk("a2", graph_id=_NO_GRAPH), _chunk("a3")]
    explained = _explain(chunks, blocked={"a2": ["pre"]}, excluded=frozenset({"a1"}))
    assert [(e.chunk.chunk_id, e.reason, e.position) for e in explained.held_at] == [("a2", PassedReason.BLOCKED, 1)]
    assert explained.if_holding is None
    assert explained.next is not None and explained.next.position == 2


def test_excluded_counts_only_served_chunks_ahead_of_next() -> None:
    chunks = [_chunk("e1", _ELSEWHERE), _chunk("a1"), _chunk("a2"), _chunk("a3")]
    explained = _explain(chunks, excluded=frozenset({"e1", "a1", "a3"}))
    assert (explained.unserved, explained.excluded) == (1, 1)
    assert explained.next is not None and explained.next.position == 2


@pytest.mark.parametrize(
    ("policy", "per_project"),
    [(QueueMatchPolicy.PASS_OVER, False), (QueueMatchPolicy.HOLD, False), (QueueMatchPolicy.HOLD, True)],
)
def test_a_walk_reaches_the_first_unexcluded_chunk_at_its_whole_order_position(
    policy: QueueMatchPolicy, per_project: bool
) -> None:
    chunks = [_chunk("e1", _ELSEWHERE), _chunk("a1"), _chunk("b1", _B), _chunk("a2"), _chunk("b2", _B)]
    walk = walk_matched_entries(
        chunks,
        **_inputs(chunks, {}, {_A, _B}),
        policy=policy,
        hold_per_project=per_project,
        excluded={"a1", "b1"},
    )
    assert walk.choice is not None and (walk.choice.chunk.chunk_id, walk.choice.position) == ("a2", 3)
    assert (walk.passed, walk.unserved, walk.excluded) == ((), 1, 2)


def test_every_chunk_excluded_or_unserved_chooses_nothing() -> None:
    chunks = [_chunk("a1"), _chunk("e1", _ELSEWHERE)]
    for served in (set[str]({_A}), set[str]()):
        walk = walk_matched_entries(
            chunks, **_inputs(chunks, {}, served), policy=QueueMatchPolicy.PASS_OVER, excluded={"a1"}
        )
        assert walk.choice is None and walk.passed == ()


def test_the_candidate_plan_selects_heads_after_exclusion() -> None:
    chunks = [_chunk("a1"), _chunk("e1", _ELSEWHERE), _chunk("b1", _B), _chunk("a2"), _chunk("b2", _B)]

    def plan(policy: QueueMatchPolicy, per_project: bool) -> list[str]:
        return [
            c.chunk_id
            for c in matched_candidates(
                chunks, served={_A, _B}, excluded={"a1"}, policy=policy, hold_per_project=per_project
            )
        ]

    assert plan(QueueMatchPolicy.PASS_OVER, False) == ["b1", "a2", "b2"]
    assert plan(QueueMatchPolicy.HOLD, False) == ["b1"]
    assert plan(QueueMatchPolicy.HOLD, True) == ["b1", "a2"]
    assert (
        matched_candidates(
            chunks, served=set[str](), excluded=(), policy=QueueMatchPolicy.PASS_OVER, hold_per_project=False
        )
        == []
    )
