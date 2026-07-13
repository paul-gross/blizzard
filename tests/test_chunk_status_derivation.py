"""Chunk status derivation (unit tier) — the D-067 precedence, facts only.

The derivation is a pure function of :class:`ChunkFacts`, so these tests build the
facts directly — no store, no tokens (``bzh:facts-not-status`` / ``bzh:domain-takes-objects``).
They walk the P6 live path (ready -> running -> delivering -> done) and pin the
precedence edges the design specifies: an escalation superseded by a later lease is
no longer ``needs_human``, and a released route re-derives ``ready``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.graph import Executor
from blizzard.hub.domain.work import (
    ChunkFacts,
    ChunkStatus,
    DecisionFact,
    EscalationFact,
    LeaseFact,
    QuestionFact,
    RouteCreatedFact,
    RouteReleasedFact,
    TransitionFact,
    current_node_id,
    derive_chunk_status,
    latest_epoch,
    open_questions,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def test_minted_with_no_facts_is_ready() -> None:
    assert derive_chunk_status(ChunkFacts(minted=True)) is ChunkStatus.READY


def test_live_route_is_running() -> None:
    facts = ChunkFacts(minted=True, routes_created=[RouteCreatedFact(created_at=_at(1))])
    assert derive_chunk_status(facts) is ChunkStatus.RUNNING


def test_released_route_re_derives_ready() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        routes_released=[RouteReleasedFact(released_at=_at(2))],
    )
    assert derive_chunk_status(facts) is ChunkStatus.READY


def test_reclaimed_after_release_is_running_again() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1)), RouteCreatedFact(created_at=_at(3))],
        routes_released=[RouteReleasedFact(released_at=_at(2))],
    )
    assert derive_chunk_status(facts) is ChunkStatus.RUNNING


def test_newest_transition_into_hub_node_is_delivering() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        transitions=[
            TransitionFact(to_node_id="nd_deliver", to_node_executor=Executor.HUB, epoch=1, recorded_at=_at(5)),
        ],
    )
    assert derive_chunk_status(facts) is ChunkStatus.DELIVERING


def test_runner_node_transition_stays_running_not_delivering() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        transitions=[
            TransitionFact(to_node_id="nd_build", to_node_executor=Executor.RUNNER, epoch=1, recorded_at=_at(5)),
        ],
    )
    assert derive_chunk_status(facts) is ChunkStatus.RUNNING


def test_delivery_landed_is_done_over_a_live_route() -> None:
    facts = ChunkFacts(
        minted=True,
        delivery_landed=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
    )
    assert derive_chunk_status(facts) is ChunkStatus.DONE


def test_stopped_wins_over_everything() -> None:
    facts = ChunkFacts(minted=True, stopped=True, delivery_landed=True)
    assert derive_chunk_status(facts) is ChunkStatus.STOPPED


def test_open_escalation_is_needs_human() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        escalations=[EscalationFact(epoch=1, recorded_at=_at(4))],
    )
    assert derive_chunk_status(facts) is ChunkStatus.NEEDS_HUMAN


def test_escalation_closed_by_later_lease_is_no_longer_needs_human() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        escalations=[EscalationFact(epoch=1, recorded_at=_at(4))],
        leases=[LeaseFact(epoch=2, minted_at=_at(6))],  # requeue + re-lease supersedes (D-067)
    )
    assert derive_chunk_status(facts) is ChunkStatus.RUNNING


def test_open_question_is_waiting_on_human_over_a_live_route() -> None:
    # An open ask parks the chunk (ask-answer.md): a live route would derive running,
    # but the unanswered question wins the higher-precedence waiting_on_human slot.
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        questions=[QuestionFact(question_id="qn_1", asked_at=_at(3), answered=False)],
    )
    assert derive_chunk_status(facts) is ChunkStatus.WAITING_ON_HUMAN
    assert [q.question_id for q in open_questions(facts)] == ["qn_1"]


def test_answered_question_flips_back_to_running() -> None:
    # The answer row alone flips the chunk out of waiting_on_human (D-004).
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        questions=[QuestionFact(question_id="qn_1", asked_at=_at(3), answered=True)],
    )
    assert derive_chunk_status(facts) is ChunkStatus.RUNNING
    assert open_questions(facts) == []


def test_open_decision_is_waiting_on_human() -> None:
    # The gate track writes DecisionFacts against this shape; an unresolved one parks.
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        decisions=[DecisionFact(decision_id="dec_1", submitted_at=_at(3), resolved=False)],
    )
    assert derive_chunk_status(facts) is ChunkStatus.WAITING_ON_HUMAN


def test_resolved_decision_no_longer_waits() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        decisions=[DecisionFact(decision_id="dec_1", submitted_at=_at(3), resolved=True)],
    )
    assert derive_chunk_status(facts) is ChunkStatus.RUNNING


def test_needs_human_wins_over_an_open_question() -> None:
    # Precedence (events.md): needs_human sits above waiting_on_human.
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        escalations=[EscalationFact(epoch=1, recorded_at=_at(4))],
        questions=[QuestionFact(question_id="qn_1", asked_at=_at(5), answered=False)],
    )
    assert derive_chunk_status(facts) is ChunkStatus.NEEDS_HUMAN


def test_open_question_wins_over_a_hub_node_transition() -> None:
    # waiting_on_human sits above delivering: a chunk that asked at a hub node still parks.
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        transitions=[
            TransitionFact(to_node_id="nd_deliver", to_node_executor=Executor.HUB, epoch=1, recorded_at=_at(5)),
        ],
        questions=[QuestionFact(question_id="qn_1", asked_at=_at(6), answered=False)],
    )
    assert derive_chunk_status(facts) is ChunkStatus.WAITING_ON_HUMAN


def test_current_node_and_latest_epoch_derive_from_facts() -> None:
    facts = ChunkFacts(
        minted=True,
        leases=[LeaseFact(epoch=1, minted_at=_at(1)), LeaseFact(epoch=3, minted_at=_at(3))],
        transitions=[
            TransitionFact(to_node_id="nd_build", to_node_executor=Executor.RUNNER, epoch=1, recorded_at=_at(2)),
            TransitionFact(to_node_id="nd_deliver", to_node_executor=Executor.HUB, epoch=3, recorded_at=_at(4)),
        ],
    )
    assert current_node_id(facts) == "nd_deliver"
    assert latest_epoch(facts) == 3


def test_current_node_and_epoch_none_before_any_fact() -> None:
    facts = ChunkFacts(minted=True)
    assert current_node_id(facts) is None
    assert latest_epoch(facts) is None
