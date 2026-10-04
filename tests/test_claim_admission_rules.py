"""Claim admission rules, pinned by value — no repository, no clock."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import (
    CHUNK_VERB_LEGALITY,
    Chunk,
    ChunkFacts,
    ChunkVerb,
    DependencyEdge,
    PauseFact,
    RouteCreatedFact,
)
from blizzard.hub.domain.execution.claim import (
    ClaimAdmission,
    ClaimConflict,
    ClaimDeniedDependency,
    ClaimDeniedIncompatible,
    ClaimDeniedNotReady,
    ClaimDeniedPaused,
    ClaimDeniedTerminal,
    ClaimDeniedUnregistered,
    first_unmet_prerequisite,
    refuse_paused_runner,
)
from blizzard.hub.domain.graph.model import Choice, Edge, Node
from blizzard.hub.domain.runners.registration import RunnerCapability, RunnerRegistration, RunnerRetired
from blizzard.hub.domain.runners.route import Route
from tests.support import make_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_NON_TERMINAL = frozenset(ChunkStatus) - TERMINAL_STATUSES

_NODE = Node(
    node_id="nd_build",
    graph_id="gr_a",
    name="build",
    executor=Executor.RUNNER,
    prompt="p",
    checks=[],
    produces=[],
    session=SessionMode.FRESH,
    judged_by=JudgedBy.WORKER,
    retries_max=None,
    retries_exhausted=None,
    choices=[Choice("ch_ship", "ship", "")],
)
_GRAPH = make_graph(
    "gr_a", "work", entry_node_id="nd_build", nodes=[_NODE], edges=[Edge("nd_build", "ch_ship", "done")]
)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_a", work_refs=[], minted_at=_T0)
_READY = ChunkFacts(minted=True, promoted=True)
_REGISTERED = RunnerRegistration(
    runner_id="runner-a", workspace_id="ws-a", registered_at=_T0, last_seen_at=_T0, hub_paused=False
)


def _admit(
    facts: ChunkFacts | None,
    *,
    route: Route | None = None,
    unmet: str | None = None,
    registration: RunnerRegistration | None = _REGISTERED,
) -> Node:
    return ClaimAdmission(_CHUNK, _GRAPH, facts).node(
        runner_id="runner-a", existing_route=route, unmet_prerequisite=unmet, registration=registration
    )


def _route(runner_id: str = "runner-b") -> Route:
    return Route(chunk_id="chk_1", runner_id=runner_id, workspace_id="ws-b", environment_ids=["e1"], created_at=_T0)


def _edge(prerequisite: str, dependent: str = "chk_1") -> DependencyEdge:
    return DependencyEdge(
        dependency_id=f"dep_{prerequisite}",
        dependent_chunk_id=dependent,
        prerequisite_chunk_id=prerequisite,
        declared_at=_T0,
        declared_by="op",
    )


# --- Verb legality -----------------------------------------------------------


def test_claim_is_legal_only_from_ready() -> None:
    assert CHUNK_VERB_LEGALITY[ChunkVerb.CLAIM] == frozenset({ChunkStatus.READY})


def test_requeue_is_legal_only_from_needs_human() -> None:
    assert CHUNK_VERB_LEGALITY[ChunkVerb.REQUEUE] == frozenset({ChunkStatus.NEEDS_HUMAN})


@pytest.mark.parametrize(
    "verb",
    [ChunkVerb.REKEY_ROUTE_TOKEN, ChunkVerb.READ_ENVELOPE, ChunkVerb.RESOLVE_DECISION, ChunkVerb.ANSWER_QUESTION],
)
def test_route_envelope_decision_and_question_verbs_are_refused_once_the_chunk_ends(verb: ChunkVerb) -> None:
    assert CHUNK_VERB_LEGALITY[verb] == _NON_TERMINAL


# --- Admission order ---------------------------------------------------------


def test_a_ready_chunk_lands_at_its_current_node() -> None:
    assert _admit(_READY) == _NODE


@pytest.mark.parametrize(
    ("facts", "status"),
    [
        (replace(_READY, stopped=True, stopped_at=_T0), ChunkStatus.STOPPED),
        (replace(_READY, operator_completed=True, operator_completed_at=_T0), ChunkStatus.DONE),
    ],
)
def test_an_ended_chunk_is_refused_as_terminal_even_while_a_route_is_held(
    facts: ChunkFacts, status: ChunkStatus
) -> None:
    with pytest.raises(ClaimDeniedTerminal) as refused:
        _admit(facts, route=_route())
    assert refused.value.status is status


def test_a_held_route_is_a_lost_race_before_the_status_window() -> None:
    with pytest.raises(ClaimConflict) as refused:
        _admit(ChunkFacts(minted=True), route=_route())
    assert refused.value.held_by_runner_id == "runner-b"


@pytest.mark.parametrize(
    ("facts", "status"),
    [
        (None, ChunkStatus.NOT_READY),
        (ChunkFacts(minted=True), ChunkStatus.NOT_READY),
        (replace(_READY, pauses=[PauseFact(paused=True, set_at=_T0, set_by="op")]), ChunkStatus.PAUSED),
        (replace(_READY, routes_created=[RouteCreatedFact(created_at=_T0)]), ChunkStatus.RUNNING),
    ],
)
def test_a_chunk_that_is_not_ready_is_refused(facts: ChunkFacts | None, status: ChunkStatus) -> None:
    with pytest.raises(ClaimDeniedNotReady) as refused:
        _admit(facts)
    assert refused.value.status is status


def test_an_unmet_prerequisite_is_refused_after_the_status_window() -> None:
    with pytest.raises(ClaimDeniedDependency) as refused:
        _admit(_READY, unmet="chk_pre")
    assert refused.value.prerequisite_chunk_id == "chk_pre"
    with pytest.raises(ClaimDeniedNotReady):
        _admit(ChunkFacts(minted=True), unmet="chk_pre")


def test_a_retired_runner_is_refused() -> None:
    with pytest.raises(RunnerRetired):
        _admit(_READY, registration=replace(_REGISTERED, retired=True, retired_at=_T0, retired_by="op"))


def test_a_runner_whose_capabilities_cannot_run_the_chunk_is_refused() -> None:
    withdrawn = replace(_REGISTERED, capabilities=(RunnerCapability(harness_id="claude_code", available=False),))
    with pytest.raises(ClaimDeniedIncompatible):
        _admit(_READY, registration=withdrawn)


def test_an_unregistered_runner_is_refused_after_the_chunk_admits_it() -> None:
    with pytest.raises(ClaimDeniedUnregistered) as refused:
        _admit(_READY, registration=None)
    assert refused.value.runner_id == "runner-a"
    assert str(refused.value) == "runner runner-a is not registered at the hub"
    with pytest.raises(ClaimDeniedNotReady):
        _admit(ChunkFacts(minted=True), registration=None)


def test_a_registration_reporting_no_capabilities_is_not_capability_checked() -> None:
    assert _admit(_READY, registration=_REGISTERED) == _NODE


# --- Runner brake ------------------------------------------------------------


def test_a_paused_retired_or_unregistered_runner_is_refused_before_the_race() -> None:
    with pytest.raises(ClaimDeniedPaused):
        refuse_paused_runner(replace(_REGISTERED, hub_paused=True), runner_id="runner-a")
    with pytest.raises(RunnerRetired):
        refuse_paused_runner(replace(_REGISTERED, retired=True, retired_at=_T0, retired_by="op"), runner_id="runner-a")
    with pytest.raises(ClaimDeniedUnregistered) as refused:
        refuse_paused_runner(None, runner_id="runner-a")
    assert refused.value.runner_id == "runner-a"
    assert refuse_paused_runner(_REGISTERED, runner_id="runner-a") is _REGISTERED


# --- Prerequisites -----------------------------------------------------------


def test_the_first_unmet_prerequisite_is_the_earliest_declared_edge_not_done() -> None:
    done = ChunkFacts(minted=True, operator_completed=True, operator_completed_at=_T0)
    edges = [_edge("chk_done"), _edge("chk_other", dependent="chk_2"), _edge("chk_open"), _edge("chk_absent")]
    facts = {"chk_done": done, "chk_open": ChunkFacts(minted=True)}

    assert first_unmet_prerequisite("chk_1", edges, facts) == "chk_open"
    assert first_unmet_prerequisite("chk_1", [_edge("chk_absent")], facts) == "chk_absent"
    assert first_unmet_prerequisite("chk_1", [_edge("chk_done")], facts) is None
    assert first_unmet_prerequisite("chk_1", [], facts) is None
