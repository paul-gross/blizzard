"""Unit tier: the graph's rules, pinned by value — no repository, no clock.

The lifecycle table and the standing that consults it, the default-graph decision, the
cross-graph warning and target, the follow-latest drift target, the mint-if-changed test,
and the validation refusal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.graph.authoring import DefaultGraphRetired, resolve_default
from blizzard.hub.domain.graph.model import (
    GRAPH_TRANSITIONS,
    Choice,
    Edge,
    FollowLatest,
    Graph,
    GraphDoc,
    GraphLifecycleFact,
    GraphPolicyFact,
    GraphStanding,
    GraphState,
    GraphVerb,
    GraphVerdict,
    Node,
    TargetGraphRetired,
)
from blizzard.hub.domain.graph.validation import GraphValidationError, Validator, cross_graph_warnings
from tests.support import make_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_AT = datetime(2026, 2, 1, tzinfo=UTC)


def _node(node_id: str, name: str, choices: list[Choice]) -> Node:
    return Node(
        node_id=node_id,
        graph_id="gr_a",
        name=name,
        executor=Executor.RUNNER,
        prompt="p",
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        choices=choices,
    )


def _cross_graph_graph() -> Graph:
    choices = [Choice("ch_ship", "ship", ""), Choice("ch_hand", "hand-off", ""), Choice("ch_again", "again", "")]
    edges = [
        Edge("nd_build", "ch_ship", "done"),
        Edge("nd_build", "ch_hand", "graph:delivery", target_graph="delivery"),
        Edge("nd_build", "ch_again", "graph:delivery", target_graph="delivery"),
        Edge("nd_review", "ch_x", "graph:triage", target_graph="triage"),
    ]
    nodes = [_node("nd_build", "build", choices), _node("nd_review", "review", [Choice("ch_x", "x", "")])]
    return make_graph("gr_a", "work", entry_node_id="nd_build", nodes=nodes, edges=edges)


# --- Lifecycle table ----------------------------------------------------------


def test_every_graph_verb_is_declared_from_every_state() -> None:
    for state in GraphState:
        assert set(GRAPH_TRANSITIONS[state]) == set(GraphVerb)


@pytest.mark.parametrize("verb", [GraphVerb.RETIRE, GraphVerb.ENABLE, GraphVerb.SET_FOLLOW_LATEST])
@pytest.mark.parametrize("state", list(GraphState))
def test_retire_enable_and_follow_latest_are_legal_from_either_state(state: GraphState, verb: GraphVerb) -> None:
    assert GRAPH_TRANSITIONS[state][verb] is GraphVerdict.LEGAL


def test_a_retired_graph_refuses_new_targeting_and_name_resolution_but_runs_its_pinned_chunks() -> None:
    retired = GRAPH_TRANSITIONS[GraphState.RETIRED]
    assert retired[GraphVerb.TARGET] is GraphVerdict.REFUSED
    assert retired[GraphVerb.RESOLVE_BY_NAME] is GraphVerdict.REFUSED
    assert retired[GraphVerb.RUN_PINNED] is GraphVerdict.LEGAL


def test_an_enabled_graph_is_targetable() -> None:
    standing = GraphStanding(make_graph("gr_a", "work"), retired=False)
    assert standing.state is GraphState.ENABLED
    assert standing.targetable()
    standing.require_targetable()


def test_a_retired_graph_refuses_being_named_as_a_new_target() -> None:
    standing = GraphStanding(make_graph("gr_a", "work"), retired=True)
    assert not standing.targetable()
    with pytest.raises(TargetGraphRetired) as caught:
        standing.require_targetable()
    assert caught.value.graph_id == "gr_a"


def test_a_lifecycle_fact_carries_the_graph_and_direction() -> None:
    graph = make_graph("gr_a", "work")
    assert graph.lifecycle_fact(retired=True, at=_AT, by="op") == GraphLifecycleFact("gr_a", True, _AT, "op")
    assert graph.lifecycle_fact(retired=False, at=_AT, by="op") == GraphLifecycleFact("gr_a", False, _AT, "op")


@pytest.mark.parametrize("policy", [True, False, None])
def test_a_policy_fact_carries_the_tri_state(policy: bool | None) -> None:
    fact = make_graph("gr_a", "work").policy_fact(follow_latest=policy, at=_AT, by="op")
    assert fact == GraphPolicyFact("gr_a", policy, _AT, "op")


# --- Default graph ------------------------------------------------------------


def test_the_default_resolves_to_its_newest_enabled_mint() -> None:
    enabled = make_graph("gr_a", "default")
    assert resolve_default("default", enabled=enabled, any_minted=True) is enabled


def test_a_default_never_minted_is_minted() -> None:
    assert resolve_default("default", enabled=None, any_minted=False) is None


def test_a_default_whose_every_mint_is_retired_is_refused() -> None:
    with pytest.raises(DefaultGraphRetired) as caught:
        resolve_default("default", enabled=None, any_minted=True)
    assert caught.value.name == "default"


# --- Cross-graph targets ------------------------------------------------------


def test_cross_graph_targets_are_distinct_in_edge_order() -> None:
    assert _cross_graph_graph().cross_graph_targets() == ["delivery", "triage"]


def test_a_cross_graph_target_naming_no_enabled_graph_is_a_warning() -> None:
    warnings = cross_graph_warnings(_cross_graph_graph(), enabled_names={"delivery"})
    assert len(warnings) == 1
    assert "`triage`" in warnings[0]


def test_every_cross_graph_target_enabled_warns_nothing() -> None:
    assert cross_graph_warnings(_cross_graph_graph(), enabled_names={"delivery", "triage"}) == []


def test_the_cross_graph_target_name_follows_the_submitted_choice() -> None:
    graph = _cross_graph_graph()
    assert graph.cross_graph_target_name("nd_build", "hand-off") == "delivery"
    assert graph.cross_graph_target_name("nd_build", "ship") is None
    assert graph.cross_graph_target_name("nd_build", "unknown") is None
    assert graph.cross_graph_target_name("nd_missing", "hand-off") is None


# --- Follow-latest drift ------------------------------------------------------


def test_follow_latest_drifts_only_to_a_strictly_newer_mint() -> None:
    current = make_graph("gr_b", "work", created_at=_T0)
    newer = make_graph("gr_c", "work", created_at=_T0 + timedelta(days=1))
    older = make_graph("gr_a", "work", created_at=_T0 - timedelta(days=1))
    policy = FollowLatest(enabled=True)
    assert policy.target(current, newer) is newer
    assert policy.target(current, older) is None
    assert policy.target(current, current) is None
    assert policy.target(current, None) is None


def test_follow_latest_off_never_drifts() -> None:
    current = make_graph("gr_b", "work", created_at=_T0)
    newer = make_graph("gr_c", "work", created_at=_T0 + timedelta(days=1))
    assert FollowLatest(enabled=False).target(current, newer) is None


# --- Mint-if-changed and validation -------------------------------------------


def test_a_definition_differs_only_from_another_parsed_definition() -> None:
    doc = GraphDoc(name="work", entry="build", nodes=[])
    assert doc.differs_from(None)
    assert not doc.differs_from(GraphDoc(name="work", entry="build", nodes=[]))
    assert doc.differs_from(GraphDoc(name="work", entry="review", nodes=[]))


def test_an_invalid_definition_is_refused_with_its_errors() -> None:
    with pytest.raises(GraphValidationError) as caught:
        Validator.of(GraphDoc(name="work", entry="build", nodes=[])).require_valid()
    assert any("entry `build`" in e for e in caught.value.result.errors)
