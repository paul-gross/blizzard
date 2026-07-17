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
    PrOpenedFact,
    QuestionFact,
    RouteCreatedFact,
    RouteReleasedFact,
    TransitionFact,
    awaiting_external_merge,
    current_node_id,
    derive_chunk_status,
    latest_epoch,
    open_questions,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def test_minted_with_no_facts_is_not_ready() -> None:
    # Ingest mints a chunk in the not-ready resting state: visible, never claimed
    # until promoted. Un-promoted with no other facts falls through to not_ready.
    assert derive_chunk_status(ChunkFacts(minted=True)) is ChunkStatus.NOT_READY


def test_promoted_chunk_with_no_route_is_ready() -> None:
    # The ``chunk.promoted`` fact flips the resting state to ready — now claimable.
    assert derive_chunk_status(ChunkFacts(minted=True, promoted=True)) is ChunkStatus.READY


def test_live_route_wins_over_not_ready() -> None:
    # A live route derives running even without promotion: the route check sits above the
    # not_ready fall-through, so a claimed chunk always reads its post-claim state.
    facts = ChunkFacts(minted=True, routes_created=[RouteCreatedFact(created_at=_at(1))])
    assert derive_chunk_status(facts) is ChunkStatus.RUNNING


def test_released_route_on_promoted_chunk_re_derives_ready() -> None:
    facts = ChunkFacts(
        minted=True,
        promoted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        routes_released=[RouteReleasedFact(released_at=_at(2))],
    )
    assert derive_chunk_status(facts) is ChunkStatus.READY


def _detached_route(**extra: object) -> ChunkFacts:
    """A route created then released — detach's fact shape: one write, no
    supersession, no epoch bump. Used to pin that detach clears only the running
    branch and leaves every higher-precedence branch untouched."""
    return ChunkFacts(
        minted=True,
        promoted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        routes_released=[RouteReleasedFact(released_at=_at(2))],
        **extra,  # type: ignore[arg-type]
    )


def test_detached_route_with_no_other_facts_re_derives_ready() -> None:
    assert derive_chunk_status(_detached_route()) is ChunkStatus.READY


def test_detached_route_with_an_open_escalation_still_derives_needs_human() -> None:
    # Detach releases the route only; it does not supersede the escalation (that is
    # requeue's job, D-067), so a detached, still-escalated chunk stays needs_human.
    facts = _detached_route(escalations=[EscalationFact(epoch=1, recorded_at=_at(3))])
    assert derive_chunk_status(facts) is ChunkStatus.NEEDS_HUMAN


def test_detached_route_with_an_open_question_still_derives_waiting_on_human() -> None:
    # Same shape, an open ask instead of an escalation — explicit acceptance criterion
    # of the detach issue: the ask/answer park survives a detach.
    facts = _detached_route(questions=[QuestionFact(question_id="qn_1", asked_at=_at(3), answered=False)])
    assert derive_chunk_status(facts) is ChunkStatus.WAITING_ON_HUMAN


def test_stopped_wins_over_not_ready() -> None:
    # An operator can abandon a chunk before promoting it — stopped still wins.
    assert derive_chunk_status(ChunkFacts(minted=True, stopped=True)) is ChunkStatus.STOPPED


def test_reclaimed_after_release_is_running_again() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1)), RouteCreatedFact(created_at=_at(3))],
        routes_released=[RouteReleasedFact(released_at=_at(2))],
    )
    assert derive_chunk_status(facts) is ChunkStatus.RUNNING


def test_same_instant_detach_takes_effect() -> None:
    """Issue #41: a release recorded after the create in real write order — its
    ``seq`` is higher — wins a same-instant tie, so the chunk derives out of
    ``running`` even though ``created_at == released_at``."""
    facts = ChunkFacts(
        minted=True,
        promoted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1), seq=1)],
        routes_released=[RouteReleasedFact(released_at=_at(1), seq=2)],
    )
    assert derive_chunk_status(facts) is ChunkStatus.READY


def test_same_instant_reclaim_still_derives_running() -> None:
    """Issue #41's other half: a fresh ``route.created`` recorded after a prior release
    in real write order — its ``seq`` is higher than that release's — must still win
    the tie, so no live route is lost (generalizes
    ``test_reclaimed_after_release_is_running_again`` to the same-instant case)."""
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1), seq=1), RouteCreatedFact(created_at=_at(2), seq=3)],
        routes_released=[RouteReleasedFact(released_at=_at(2), seq=2)],
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


def _parked_on_open_pr(**extra: object) -> ChunkFacts:
    """A chunk in open-pr mode: its newest transition entered the deliver hub node, a PR
    was opened, and no ``pr.closed`` yet — the environments still held."""
    return ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        transitions=[
            TransitionFact(to_node_id="nd_deliver", to_node_executor=Executor.HUB, epoch=1, recorded_at=_at(5))
        ],
        pr_opened=[
            PrOpenedFact(repo="acme/widget", number=7, url="http://forge/pr/7", commit_hash="abc", opened_at=_at(5))
        ],
        **extra,  # type: ignore[arg-type]
    )


def test_open_pr_park_is_delivering_awaiting_external_merge() -> None:
    facts = _parked_on_open_pr()
    # Still ``delivering`` (the newest transition entered the deliver hub node), with the
    # awaiting-external-merge detail set — not a distinct status.
    assert derive_chunk_status(facts) is ChunkStatus.DELIVERING
    assert awaiting_external_merge(facts) is True


def test_pr_closed_is_done() -> None:
    # The terminal ``pr.closed`` fact flips the chunk to done, the open-pr counterpart of
    # ``delivery.landed``, and clears the awaiting-external-merge detail.
    facts = _parked_on_open_pr(pr_closed=True)
    assert derive_chunk_status(facts) is ChunkStatus.DONE
    assert awaiting_external_merge(facts) is False


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
        leases=[LeaseFact(epoch=2, minted_at=_at(6))],  # requeue + re-lease supersedes
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
    # The answer row alone flips the chunk out of waiting_on_human.
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
