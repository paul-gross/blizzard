"""Claim admission rules, pinned by value — no repository, no clock."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

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
    ClaimService,
    RekeyDeniedTerminal,
    first_unmet_prerequisite,
    refuse_paused_runner,
    refuse_rekey,
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
    runner_id="runner-a",
    name="runner-a",
    added_at=_T0,
    workspace_id="ws-a",
    registered_at=_T0,
    last_seen_at=_T0,
    hub_paused=False,
    capabilities=(RunnerCapability(harness_id="claude_code", default=True),),
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


_HELD = replace(_READY, routes_created=[RouteCreatedFact(created_at=_T0)])


@pytest.mark.parametrize(
    ("facts", "status"),
    [
        (replace(_HELD, stopped=True, stopped_at=_T0), ChunkStatus.STOPPED),
        (replace(_HELD, operator_completed=True, operator_completed_at=_T0), ChunkStatus.DONE),
    ],
)
def test_a_route_left_on_an_ended_chunk_is_not_rekeyed(facts: ChunkFacts, status: ChunkStatus) -> None:
    with pytest.raises(RekeyDeniedTerminal) as refused:
        refuse_rekey(_route(), facts)
    assert (refused.value.chunk_id, refused.value.status) == ("chk_1", status)
    assert str(refused.value) == f"chunk chk_1 is {status.value}, its route confers no tenure"


def test_a_route_on_a_live_chunk_is_rekeyed() -> None:
    assert _HELD.status() is ChunkStatus.RUNNING
    refuse_rekey(_route(), _HELD)


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
    with pytest.raises(RunnerRetired) as refused:
        _admit(_READY, registration=replace(_REGISTERED, retired=True, retired_at=_T0, retired_by="op"))
    assert str(refused.value) == "runner runner-a is retired — claim refused; `reinstate` it first"


def test_a_runner_whose_capabilities_cannot_run_the_chunk_is_refused() -> None:
    withdrawn = replace(_REGISTERED, capabilities=(RunnerCapability(harness_id="claude_code", available=False),))
    with pytest.raises(ClaimDeniedIncompatible):
        _admit(_READY, registration=withdrawn)


def test_an_unregistered_runner_is_refused_before_any_chunk_refusal() -> None:
    ended = ChunkFacts(minted=True, operator_completed=True, operator_completed_at=_T0)
    with pytest.raises(ClaimDeniedUnregistered) as refused:
        _admit(_READY, registration=None)
    assert refused.value.runner_id == "runner-a"
    assert str(refused.value) == "runner runner-a is not registered at the hub"
    with pytest.raises(ClaimDeniedUnregistered):
        _admit(ChunkFacts(minted=True), registration=None)
    with pytest.raises(ClaimDeniedUnregistered):
        _admit(ended, registration=None)
    with pytest.raises(ClaimDeniedUnregistered):
        _admit(_READY, route=_route(), registration=None)


def test_a_retired_runner_is_refused_before_any_chunk_refusal() -> None:
    ended = ChunkFacts(minted=True, operator_completed=True, operator_completed_at=_T0)
    retired = replace(_REGISTERED, retired=True, retired_at=_T0, retired_by="op")
    with pytest.raises(RunnerRetired):
        _admit(ended, registration=retired)
    with pytest.raises(RunnerRetired):
        _admit(ChunkFacts(minted=True), registration=retired)
    with pytest.raises(RunnerRetired):
        _admit(_READY, unmet="chk_pre", registration=retired)


def test_a_registration_reporting_no_capabilities_is_refused() -> None:
    with pytest.raises(ClaimDeniedIncompatible):
        _admit(_READY, registration=replace(_REGISTERED, capabilities=()))


def test_a_registration_with_a_default_capability_is_admitted() -> None:
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


class _RegistryOf:
    """Only ``get_runner`` is live — the pre-lock peek."""

    def __init__(self, registration: RunnerRegistration | None) -> None:
        self._registration = registration

    def get_runner(self, runner_id: str) -> RunnerRegistration | None:
        return self._registration


class _UnenterableExclusiveWrites:
    @contextmanager
    def locked(self, chunk_ids: object) -> Iterator[object]:
        raise AssertionError("the claim entered the lock for a retired runner")
        yield


def test_a_retired_runner_is_refused_before_the_claim_lock_is_entered() -> None:
    retired = replace(_REGISTERED, retired=True, retired_at=_T0, retired_by="op")
    service = ClaimService(
        route=cast(Any, None),
        artifacts=cast(Any, None),
        graphs=cast(Any, None),
        registry=cast(Any, _RegistryOf(retired)),
        retired=cast(Any, None),
        exclusive=cast(Any, _UnenterableExclusiveWrites()),
        clock=cast(Any, None),
        label=cast(Any, None),
    )
    with pytest.raises(RunnerRetired) as refused:
        service.claim(_CHUNK, _GRAPH, runner_id="runner-a", workspace_id="ws-a", environment_ids=["e1"])
    assert refused.value.runner_id == "runner-a"
    assert str(refused.value) == "runner runner-a is retired — claim refused; `reinstate` it first"
