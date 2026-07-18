"""Chunk status derivation (unit tier) — the status precedence ladder, facts only.

The derivation is a pure function of :class:`ChunkFacts`, so these tests build the
facts directly — no store, no tokens (``bzh:facts-not-status`` / ``bzh:domain-takes-objects``).
They walk the P6 live path (ready -> running -> delivering -> done) and pin the
precedence edges the design specifies: an escalation superseded by a later lease is
no longer ``needs_human``, and a released route re-derives ``ready``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.graph import RESERVED_TERMINAL, Executor
from blizzard.hub.domain.work import (
    BounceFact,
    ChunkFacts,
    ChunkStatus,
    DecisionFact,
    EscalationFact,
    LeaseFact,
    PauseFact,
    PrOpenedFact,
    QuestionFact,
    RouteCreatedFact,
    RouteReleasedFact,
    RouteTokenMintedFact,
    TransitionFact,
    awaiting_external_merge,
    bounce_count,
    bounces_over_cap,
    current_node_id,
    derive_chunk_status,
    has_landed_repos,
    latest_epoch,
    newest_live_route_token,
    open_pause,
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
    # requeue's job), so a detached, still-escalated chunk stays needs_human.
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


# --------------------------------------------------------------------------- #
# Route capability token derivation (issue #84a) — newest-fact-wins over the live
# acquisition's window, mirroring the route liveness derivation above.
# --------------------------------------------------------------------------- #


def test_unclaimed_chunk_has_no_live_token() -> None:
    assert newest_live_route_token([], [], []) is None


def test_live_route_with_no_token_facts_has_no_live_token() -> None:
    # Should not happen in practice (the hub mints the token atomically with the
    # route), but the derivation must not fabricate one.
    routes_created = [RouteCreatedFact(created_at=_at(1))]
    assert newest_live_route_token(routes_created, [], []) is None


def test_freshly_claimed_route_has_its_own_live_token() -> None:
    routes_created = [RouteCreatedFact(created_at=_at(1), seq=1)]
    tokens = [RouteTokenMintedFact(token_hash="hash-a", minted_at=_at(1), seq=2)]
    live = newest_live_route_token(routes_created, [], tokens)
    assert live is not None
    assert live.token_hash == "hash-a"


def test_released_route_has_no_live_token() -> None:
    routes_created = [RouteCreatedFact(created_at=_at(1), seq=1)]
    routes_released = [RouteReleasedFact(released_at=_at(2), seq=3)]
    tokens = [RouteTokenMintedFact(token_hash="hash-a", minted_at=_at(1), seq=2)]
    assert newest_live_route_token(routes_created, routes_released, tokens) is None


def test_earlier_acquisitions_token_is_excluded_after_reclaim() -> None:
    """A released acquisition's token must not leak as the live token of the chunk's
    *next* acquisition — only a token minted at/after the live route's own seq counts."""
    routes_created = [
        RouteCreatedFact(created_at=_at(1), seq=1),  # first acquisition
        RouteCreatedFact(created_at=_at(3), seq=4),  # reclaim, now live
    ]
    routes_released = [RouteReleasedFact(released_at=_at(2), seq=3)]
    tokens = [
        RouteTokenMintedFact(token_hash="hash-old", minted_at=_at(1), seq=2),  # first acquisition's
        RouteTokenMintedFact(token_hash="hash-new", minted_at=_at(3), seq=5),  # the reclaim's
    ]
    live = newest_live_route_token(routes_created, routes_released, tokens)
    assert live is not None
    assert live.token_hash == "hash-new"


def test_a_rekey_fact_supersedes_the_original_token() -> None:
    """Phase 6 re-key: a second token_minted fact for the same live route, minted
    later, must win — newest-fact-wins, no separate revocation needed."""
    routes_created = [RouteCreatedFact(created_at=_at(1), seq=1)]
    tokens = [
        RouteTokenMintedFact(token_hash="hash-original", minted_at=_at(1), seq=2),
        RouteTokenMintedFact(token_hash="hash-rekeyed", minted_at=_at(5), seq=6),
    ]
    live = newest_live_route_token(routes_created, [], tokens)
    assert live is not None
    assert live.token_hash == "hash-rekeyed"


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
    # DONE derives from *reaching* the terminal transition (#63), not the landed fact
    # alone — ``finalize_delivery`` always writes both atomically in the no-authored-edge
    # (default graph) case, so this pins that real shape.
    facts = ChunkFacts(
        minted=True,
        delivery_landed=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        transitions=[
            TransitionFact(to_node_id=RESERVED_TERMINAL, to_node_executor=Executor.HUB, epoch=2, recorded_at=_at(5)),
        ],
    )
    assert derive_chunk_status(facts) is ChunkStatus.DONE


def test_delivery_landed_without_reaching_terminal_is_not_done() -> None:
    # The delicate #63 edit: a whole-chunk ``delivery.landed`` fact with no terminal
    # transition (a synthetic shape unreachable via ``finalize_delivery``, but the one
    # the derivation must not key on) must NOT derive DONE — it falls through to the
    # live route below, exactly what makes a post-merge node's worker completion
    # legal (``apply.py:91`` would otherwise reject it as terminal).
    facts = ChunkFacts(
        minted=True,
        delivery_landed=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
    )
    assert derive_chunk_status(facts) is ChunkStatus.RUNNING


def test_merged_but_running_derives_running_not_done() -> None:
    # An authored ``merged -> <node>`` edge: the coordinator recorded per-repo landed
    # facts and a NON-terminal transition into the post-merge node, retaining the route.
    # The chunk must derive its live running state, not DONE — "merged but running".
    facts = ChunkFacts(
        minted=True,
        landed_repos=frozenset({"acme/widget"}),
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        transitions=[
            TransitionFact(to_node_id="nd_postmerge", to_node_executor=Executor.RUNNER, epoch=2, recorded_at=_at(5)),
        ],
    )
    assert derive_chunk_status(facts) is ChunkStatus.RUNNING
    assert has_landed_repos(facts) is True


def test_merged_but_escalated_derives_needs_human_with_landed_detail() -> None:
    # The post-merge node exhausted retries: normal escalation, no un-merge path. The
    # chunk derives BOTH needs_human (status) and landed (detail) — "merged but stuck",
    # represented honestly rather than hidden or un-merged.
    facts = ChunkFacts(
        minted=True,
        landed_repos=frozenset({"acme/widget"}),
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        transitions=[
            TransitionFact(to_node_id="nd_postmerge", to_node_executor=Executor.RUNNER, epoch=2, recorded_at=_at(5)),
        ],
        escalations=[EscalationFact(epoch=2, recorded_at=_at(6))],
    )
    assert derive_chunk_status(facts) is ChunkStatus.NEEDS_HUMAN
    assert has_landed_repos(facts) is True


def test_has_landed_repos_false_with_no_landed_facts_at_all() -> None:
    assert has_landed_repos(ChunkFacts(minted=True)) is False


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
    # An open ask parks the chunk: a live route would derive running,
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
    # Precedence: needs_human sits above waiting_on_human.
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


# --- Pause (issue #46) --------------------------------------------------------


def test_paused_wins_over_delivering() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        transitions=[
            TransitionFact(to_node_id="nd_deliver", to_node_executor=Executor.HUB, epoch=1, recorded_at=_at(5)),
        ],
        pauses=[PauseFact(paused=True, set_at=_at(6), set_by="operator")],
    )
    assert derive_chunk_status(facts) is ChunkStatus.PAUSED


def test_paused_wins_over_running() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        pauses=[PauseFact(paused=True, set_at=_at(2), set_by="operator")],
    )
    assert derive_chunk_status(facts) is ChunkStatus.PAUSED


def test_paused_wins_over_not_ready() -> None:
    facts = ChunkFacts(minted=True, pauses=[PauseFact(paused=True, set_at=_at(1), set_by="operator")])
    assert derive_chunk_status(facts) is ChunkStatus.PAUSED


def test_paused_wins_over_ready() -> None:
    facts = ChunkFacts(minted=True, promoted=True, pauses=[PauseFact(paused=True, set_at=_at(1), set_by="operator")])
    assert derive_chunk_status(facts) is ChunkStatus.PAUSED


def test_waiting_on_human_wins_over_paused() -> None:
    # An operator may pause a chunk already parked on a question (§0.3): the chunk is
    # first, still, waiting on a human — pausing does not preempt the human gate.
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        questions=[QuestionFact(question_id="qn_1", asked_at=_at(2), answered=False)],
        pauses=[PauseFact(paused=True, set_at=_at(3), set_by="operator")],
    )
    assert derive_chunk_status(facts) is ChunkStatus.WAITING_ON_HUMAN


def test_needs_human_wins_over_paused() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        escalations=[EscalationFact(epoch=1, recorded_at=_at(2))],
        pauses=[PauseFact(paused=True, set_at=_at(3), set_by="operator")],
    )
    assert derive_chunk_status(facts) is ChunkStatus.NEEDS_HUMAN


def test_done_wins_over_paused() -> None:
    facts = ChunkFacts(
        minted=True,
        delivery_landed=True,
        transitions=[
            TransitionFact(to_node_id=RESERVED_TERMINAL, to_node_executor=Executor.HUB, epoch=1, recorded_at=_at(1)),
        ],
        pauses=[PauseFact(paused=True, set_at=_at(1), set_by="operator")],
    )
    assert derive_chunk_status(facts) is ChunkStatus.DONE


def test_stopped_wins_over_paused() -> None:
    facts = ChunkFacts(minted=True, stopped=True, pauses=[PauseFact(paused=True, set_at=_at(1), set_by="operator")])
    assert derive_chunk_status(facts) is ChunkStatus.STOPPED


def test_resumed_chunk_no_longer_paused() -> None:
    # Newest-fact-wins: a resume appended after the pause flips it back.
    facts = ChunkFacts(
        minted=True,
        promoted=True,
        pauses=[
            PauseFact(paused=True, set_at=_at(1), set_by="operator"),
            PauseFact(paused=False, set_at=_at(2), set_by="operator"),
        ],
    )
    assert derive_chunk_status(facts) is ChunkStatus.READY


def test_re_pause_after_resume_derives_paused_again() -> None:
    # Pause -> resume -> pause again: the newest fact (a second pause) must win.
    facts = ChunkFacts(
        minted=True,
        promoted=True,
        pauses=[
            PauseFact(paused=True, set_at=_at(1), set_by="operator"),
            PauseFact(paused=False, set_at=_at(2), set_by="operator"),
            PauseFact(paused=True, set_at=_at(3), set_by="operator"),
        ],
    )
    assert derive_chunk_status(facts) is ChunkStatus.PAUSED


# --- open_pause: the FACT, deliberately not the derived status (issue #46 §4) ---
#
# `open_pause` is the sole source of the wire's `ChunkDetail.pause`, which is in turn the
# only thing the runner (P4) may key its kill-and-park on. PAUSED sits *below* the
# human-gated states in the precedence above, so `status == PAUSED` is a **lossy** read of
# "is this chunk paused" — the tests here are what forbid `open_pause` from ever being
# rewritten in terms of `derive_chunk_status`. Without them that rewrite is invisible: it
# passes every other test in the suite, and P4 then silently never learns a
# paused-and-parked chunk is paused, resuming its worker on the answer (§3.3).


def _paused_and_asking() -> ChunkFacts:
    """Paused **and** parked on an open question — the overlap §0.3 deliberately allows."""
    return ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        questions=[QuestionFact(question_id="qn_1", asked_at=_at(2), answered=False)],
        pauses=[PauseFact(paused=True, set_at=_at(3), set_by="alice")],
    )


def test_open_pause_survives_a_status_that_hides_the_pause() -> None:
    """THE keystone: status is waiting_on_human, yet the pause fact is still legible.

    This is the single property P4's correctness rests on. If it ever regresses, a paused
    chunk that is also parked on a question reads as un-paused to the runner.
    """
    facts = _paused_and_asking()
    assert derive_chunk_status(facts) is ChunkStatus.WAITING_ON_HUMAN  # the status hides it...
    pause = open_pause(facts)
    assert pause is not None, "a status-keyed open_pause would return None here — the P4 trap"
    assert pause.set_by == "alice"
    assert pause.set_at == _at(3)


def test_open_pause_survives_needs_human_too() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        escalations=[EscalationFact(epoch=1, recorded_at=_at(2))],
        pauses=[PauseFact(paused=True, set_at=_at(3), set_by="operator")],
    )
    assert derive_chunk_status(facts) is ChunkStatus.NEEDS_HUMAN
    assert open_pause(facts) is not None


def test_open_pause_is_none_without_any_pause_fact() -> None:
    assert open_pause(ChunkFacts(minted=True, promoted=True)) is None


def test_open_pause_is_none_after_a_resume() -> None:
    # Newest-fact-wins: the tail reads paused=False, so no pause is open — even though a
    # pause fact exists in the list.
    facts = ChunkFacts(
        minted=True,
        promoted=True,
        pauses=[
            PauseFact(paused=True, set_at=_at(1), set_by="operator"),
            PauseFact(paused=False, set_at=_at(2), set_by="operator"),
        ],
    )
    assert open_pause(facts) is None


def test_open_pause_returns_the_newest_pause_after_a_re_pause() -> None:
    # Pause(alice) -> resume -> pause(bob): the view must carry *bob*, the newest setter,
    # not the first one. Reading the head of the list would answer "alice".
    facts = ChunkFacts(
        minted=True,
        promoted=True,
        pauses=[
            PauseFact(paused=True, set_at=_at(1), set_by="alice"),
            PauseFact(paused=False, set_at=_at(2), set_by="alice"),
            PauseFact(paused=True, set_at=_at(3), set_by="bob"),
        ],
    )
    pause = open_pause(facts)
    assert pause is not None
    assert pause.set_by == "bob"
    assert pause.set_at == _at(3)


def _bounce(epoch: int, *, at: datetime, cause: str = "conflict") -> BounceFact:
    return BounceFact(epoch=epoch, cause=cause, envelope=f'{{"cause": "{cause}"}}', recorded_at=at)


def test_bounce_count_zero_with_no_bounces() -> None:
    assert bounce_count(ChunkFacts(minted=True)) == 0


def test_bounce_count_counts_every_recorded_bounce() -> None:
    facts = ChunkFacts(minted=True, bounces=[_bounce(2, at=_at(1)), _bounce(4, at=_at(2)), _bounce(6, at=_at(3))])
    assert bounce_count(facts) == 3


def test_bounces_over_cap_false_at_the_cap() -> None:
    # A cap of 5 tolerates exactly 5 bounces — the cap counts bounces survived, not a
    # zero-indexed budget, so the 5th does not cross it.
    facts = ChunkFacts(minted=True, bounces=[_bounce(n, at=_at(n)) for n in range(5)])
    assert bounce_count(facts) == 5
    assert bounces_over_cap(facts, 5) is False


def test_bounces_over_cap_true_once_crossed() -> None:
    # The 6th bounce crosses a cap of 5.
    facts = ChunkFacts(minted=True, bounces=[_bounce(n, at=_at(n)) for n in range(6)])
    assert bounce_count(facts) == 6
    assert bounces_over_cap(facts, 5) is True


def test_bounce_is_informational_never_a_status() -> None:
    # A bounced-but-still-routed chunk (live route, newest transition into a plain
    # runner node) derives `running` regardless of how many bounces it carries — a
    # bounce is contention, not failure, and never itself drives the status ladder.
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_at(1))],
        transitions=[
            TransitionFact(to_node_id="nd_build", to_node_executor=Executor.RUNNER, epoch=2, recorded_at=_at(2)),
        ],
        bounces=[_bounce(n, at=_at(n)) for n in range(5)],
    )
    assert derive_chunk_status(facts) is ChunkStatus.RUNNING
    assert bounce_count(facts) == 5


def test_open_pause_reads_the_fact_on_a_done_chunk() -> None:
    # `done` outranks `paused` in the derivation, so a status-keyed read loses the pause
    # here too — the same lossiness as the human-gated states, one rank further up.
    facts = ChunkFacts(
        minted=True,
        delivery_landed=True,
        transitions=[
            TransitionFact(to_node_id=RESERVED_TERMINAL, to_node_executor=Executor.HUB, epoch=1, recorded_at=_at(1)),
        ],
        pauses=[PauseFact(paused=True, set_at=_at(1), set_by="operator")],
    )
    assert derive_chunk_status(facts) is ChunkStatus.DONE
    assert open_pause(facts) is not None
