"""The chunk model's own rules (unit tier) — the verb legality table and the small derivations the
routes read, pinned by value over loaded facts: no repository, no clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunk.dependencies import BlockedMarking
from blizzard.hub.domain.chunk.model import (
    CHUNK_VERB_LEGALITY,
    ChunkFacts,
    ChunkVerb,
    EscalationOpen,
    GateDecision,
    RestartFact,
    RouteCreatedFact,
    TransitionFact,
    holds_work_refs,
    verb_legal_from,
)
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(minutes: int) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _done_at(minutes: int) -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        promoted=True,
        transitions=[
            TransitionFact(
                to_node_id=RESERVED_TERMINAL, to_node_executor=Executor.HUB, epoch=1, recorded_at=_at(minutes)
            )
        ],
    )


# --- The verb legality table --------------------------------------------------


def test_every_verb_declares_its_legal_statuses() -> None:
    assert set(CHUNK_VERB_LEGALITY) == set(ChunkVerb)


def test_declaring_a_dependency_is_legal_only_pre_claim() -> None:
    for status in ChunkStatus:
        assert verb_legal_from(ChunkVerb.DECLARE_DEPENDENCY, status) is (status in PRE_CLAIM_STATUSES)


@pytest.mark.parametrize("verb", [ChunkVerb.NAME_AS_PREREQUISITE, ChunkVerb.RELEASE_DEPENDENCY])
def test_naming_a_prerequisite_and_releasing_an_edge_have_no_status_window(verb: ChunkVerb) -> None:
    assert all(verb_legal_from(verb, status) for status in ChunkStatus)


def test_a_held_work_ref_is_ingestable_again_only_once_its_holder_is_terminal() -> None:
    for status in ChunkStatus:
        assert verb_legal_from(ChunkVerb.INGEST_HELD_WORK_REF, status) is (status in TERMINAL_STATUSES)
        assert holds_work_refs(status) is (status not in TERMINAL_STATUSES)


def test_facts_admit_a_verb_by_their_derived_status() -> None:
    running = ChunkFacts(minted=True, promoted=True, routes_created=[RouteCreatedFact(created_at=_T0)])
    assert ChunkFacts(minted=True).admits(ChunkVerb.DECLARE_DEPENDENCY)
    assert not running.admits(ChunkVerb.DECLARE_DEPENDENCY)
    assert running.admits(ChunkVerb.RELEASE_DEPENDENCY)


# --- Derivations the routes read ---------------------------------------------


def test_finished_before_is_a_done_chunk_completed_strictly_before_the_instant() -> None:
    facts = _done_at(10)
    assert facts.finished_before(_at(11))
    assert not facts.finished_before(_at(10))
    assert not facts.finished_before(_at(9))


def test_a_stopped_or_live_chunk_is_never_finished_before() -> None:
    stopped = ChunkFacts(minted=True, stopped=True, stopped_at=_at(1))
    assert not stopped.finished_before(_at(60))
    assert not ChunkFacts(minted=True).finished_before(_at(60))


def test_transition_graph_off_pin_names_the_graph_a_cross_graph_move_left() -> None:
    def facts(graph_id: str | None) -> ChunkFacts:
        transition = TransitionFact(
            to_node_id="nd_b", to_node_executor=Executor.RUNNER, epoch=1, recorded_at=_T0, graph_id=graph_id
        )
        return ChunkFacts(minted=True, transitions=[transition])

    assert facts("gr_old").transition_graph_off_pin("gr_new") == "gr_old"
    assert facts("gr_new").transition_graph_off_pin("gr_new") is None
    assert facts(None).transition_graph_off_pin("gr_new") is None
    assert ChunkFacts(minted=True).transition_graph_off_pin("gr_new") is None


def test_restart_history_is_oldest_first_with_epoch_breaking_a_tie() -> None:
    def restart(epoch: int, minutes: int) -> RestartFact:
        return RestartFact(to_node_id="nd_a", from_node_id=None, graph_id="gr_1", epoch=epoch, recorded_at=_at(minutes))

    facts = ChunkFacts(minted=True, restarts=[restart(3, 5), restart(2, 5), restart(1, 9)])
    assert [r.epoch for r in facts.restart_history()] == [2, 3, 1]


def test_a_gate_decision_is_open_until_resolved_or_transitioned() -> None:
    def decision(*, resolved_choice: str | None = None, transitioned: bool = False) -> GateDecision:
        return GateDecision(
            decision_id="dec_1",
            chunk_id="chk_a",
            node_id="nd_gate",
            node_name="gate",
            epoch=1,
            choices=[],
            submitted_at=_T0,
            resolved_choice=resolved_choice,
            transitioned=transitioned,
        )

    assert decision().is_open
    assert not decision(resolved_choice="approve").is_open
    assert not decision(transitioned=True).is_open


def test_an_open_escalation_matches_the_event_filters_as_its_critical_runnerless_row() -> None:
    escalation = EscalationOpen(chunk_id="chk_a", recorded_at=_at(5), takeover_command="")

    assert escalation.matches()
    assert escalation.matches(severity="critical", chunk_id="chk_a", since=_at(5))
    assert not escalation.matches(severity="warning")
    assert not escalation.matches(runner_id="r_1")
    assert not escalation.matches(chunk_id="chk_b")
    assert not escalation.matches(since=_at(6))


def test_the_blocked_marking_names_the_earliest_declared_unmet_prerequisite_and_counts_them_all() -> None:
    assert BlockedMarking.of(["chk_first", "chk_second"]) == BlockedMarking(
        prerequisite_chunk_id="chk_first", unmet_count=2
    )
    assert BlockedMarking.of(["chk_only"]) == BlockedMarking(prerequisite_chunk_id="chk_only", unmet_count=1)


@pytest.mark.parametrize("unmet", [None, []])
def test_no_unmet_prerequisite_is_no_marking(unmet: list[str] | None) -> None:
    assert BlockedMarking.of(unmet) is None
