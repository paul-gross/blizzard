"""Lifecycle's decisions, pinned by value — plain leases, chunk views and queue entries, no store,
no hub, no clock (``blizzard.runner.lifecycle.model``)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.hub.client import RouteClaimOutcome
from blizzard.runner.leases import Lease
from blizzard.runner.leases.closure import ESCALATION_MINT, cause_of
from blizzard.runner.leases.elicitation import PendingElicitation
from blizzard.runner.leases.escalations import ParkedEscalation
from blizzard.runner.lifecycle.model import (
    COMPLETION_CLOSURES,
    LEASE_MOVES,
    AdvanceMove,
    ApplyMove,
    ClaimVerdict,
    CompletionClosure,
    CompletionMove,
    DecisionMove,
    FailureMove,
    Fenced,
    HeldChunkMove,
    InterruptedClaimMove,
    LeaseMove,
    LeaseReconcileMove,
    MintOwnerSource,
    OwnerUnresolvableMove,
    ReapMove,
    RestartDisposition,
    TakeoverHolds,
    UnpauseMove,
    adopt_enters_node,
    advance_move,
    answer_ready,
    apply_move,
    brake_defers,
    claim_disposition,
    claim_verdict,
    completion_move,
    crash_orphaned,
    decision_move,
    escalation_mint_admitted,
    failure_move,
    gate_resolution_lease_id,
    held_chunk_move,
    held_chunk_reads_local_epoch,
    interrupted_claim_move,
    lease_move_legal,
    lease_reconcile_move,
    mint_owner_source,
    next_lease_epoch,
    open_slots,
    owner_unresolvable_closure,
    owner_unresolvable_move,
    owns_node_entry,
    park_names_elicitation,
    pause_park_drain_expired,
    pick_claim_entry,
    reap_move,
    reclaim_verdict,
    recovery_owner,
    resolved_retries_max,
    restart_disposition,
    resumable,
    retry_owner_admitted,
    routed_away,
    spend_cap_detail,
    spend_cap_reached,
    unpause_move,
)
from blizzard.runner.lifecycle.session import (
    ResumedSession,
    member_skip_reason,
    model_drifted,
    rotation_breach,
    selection_is_strict,
)
from blizzard.runner.lifecycle.shutdown_drain import SHUTDOWN_DRAIN_DEADLINE
from blizzard.runner.lifecycle.takeover import OpenTakeover
from blizzard.runner.throttle.pause import PausePark
from blizzard.wire.chunk import BlockedView, ChunkDecisionStatusView, ChunkStatusView, ChunkUsageTotalView, PauseView
from blizzard.wire.envelope import ApplyOutcome, NodeConfig, RotatePolicyView
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekEntry
from blizzard.wire.route import (
    RouteClaimConflict,
    RouteClaimDependencyDenial,
    RouteClaimIncompatibleDenial,
    RouteClaimPausedDenial,
    RouteClaimResponse,
    RouteClaimTerminalDenial,
)
from tests.runner_fakes import make_envelope

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
_ME = "r1"
_PAUSE = PauseView(by="operator", set_at="2026-10-04T12:00:00Z")


def _lease(*, lease_id: str = "lease_1", epoch: int = 1, spawned: bool = True, harness_id: str | None = "cc") -> Lease:
    return Lease(
        lease_id=lease_id,
        chunk_id="ch_1",
        graph_id="g_1",
        node_id="nd_build",
        node_name="build",
        epoch=epoch,
        runner_id=_ME,
        retries_max=2,
        created_at=_NOW,
        pid=100 if spawned else None,
        process_start_time="start-100" if spawned else None,
        session_id="sess-a" if spawned else None,
        harness_id=harness_id,
    )


def _view(
    status: ChunkStatus = ChunkStatus.RUNNING,
    *,
    route: str | None = _ME,
    epoch: int | None = 1,
    pause: PauseView | None = None,
    restart_epochs: list[int] | None = None,
    decision: ChunkDecisionStatusView | None = None,
) -> ChunkStatusView:
    return ChunkStatusView(
        chunk_id="ch_1",
        status=status,
        route_runner_id=route,
        latest_epoch=epoch,
        pause=pause,
        restart_epochs=restart_epochs or [],
        decision=decision,
    )


def _takeover(*, reference_epoch: int | None = 1, fence_epoch: int | None = None) -> OpenTakeover:
    return OpenTakeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=fence_epoch,
        opened_at=_NOW,
        harness_id="cc",
        reference_epoch=reference_epoch,
    )


def _entry(chunk_id: str, *, blocked: bool = False) -> QueuePeekEntry:
    return QueuePeekEntry(
        chunk_id=chunk_id,
        graph_id="g_1",
        position=0,
        blocked=BlockedView(prerequisite_chunk_id="ch_pre") if blocked else None,
    )


def _won() -> RouteClaimOutcome:
    return RouteClaimOutcome(
        claimed=RouteClaimResponse(
            chunk_id="ch_1",
            runner_id=_ME,
            workspace_id="ws",
            environment_ids=["e1"],
            route_token="tok",
            envelope=make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")]),
        )
    )


_OUTCOMES = {
    ClaimVerdict.WON: _won(),
    ClaimVerdict.PAUSED: RouteClaimOutcome(denied_paused=RouteClaimPausedDenial(chunk_id="ch_1", runner_id=_ME)),
    ClaimVerdict.NOT_CLAIMABLE: RouteClaimOutcome(
        denied_terminal=RouteClaimTerminalDenial(chunk_id="ch_1", status="not_ready")
    ),
    ClaimVerdict.DEPENDENCY: RouteClaimOutcome(
        denied_dependency=RouteClaimDependencyDenial(chunk_id="ch_1", prerequisite_chunk_id="ch_pre")
    ),
    ClaimVerdict.INCOMPATIBLE: RouteClaimOutcome(
        denied_incompatible=RouteClaimIncompatibleDenial(chunk_id="ch_1", incompatible_runner_id=_ME)
    ),
    ClaimVerdict.LOST: RouteClaimOutcome(conflict=RouteClaimConflict(chunk_id="ch_1", held_by_runner_id="r2")),
}


# --- lease moves ------------------------------------------------------------------------------ #


def test_lease_moves_declare_a_closed_lease_final_and_an_unspawned_one_unparkable() -> None:
    assert LEASE_MOVES["closed"] == frozenset()
    assert not lease_move_legal("spawning", LeaseMove.PARK_PAUSED)
    assert not lease_move_legal("spawning", LeaseMove.START_PROCESS)
    assert lease_move_legal("running", LeaseMove.PARK_PAUSED)
    assert lease_move_legal("parked", LeaseMove.START_PROCESS)
    assert lease_move_legal("exited", LeaseMove.CLOSE_APPLIED)
    assert not lease_move_legal("running", LeaseMove.CLOSE_APPLIED)


def test_preempt_kills_under_brake() -> None:
    assert not brake_defers(LeaseMove.PREEMPT)
    assert brake_defers(LeaseMove.START_PROCESS)  # the re-entry spawn is what waits
    assert not brake_defers(LeaseMove.ABANDON)
    assert not brake_defers(LeaseMove.PARK_PAUSED)


# --- claiming --------------------------------------------------------------------------------- #


def test_pick_claim_entry_strict_vs_pass_over() -> None:
    entries = [_entry("ch_a", blocked=True), _entry("ch_b"), _entry("ch_c")]
    assert pick_claim_entry(entries, strict=True) is None
    picked = pick_claim_entry(entries, strict=False)
    assert picked is not None and picked.chunk_id == "ch_b"
    assert pick_claim_entry([], strict=False) is None
    head = pick_claim_entry([_entry("ch_b")], strict=True)
    assert head is not None and head.chunk_id == "ch_b"


@pytest.mark.parametrize(
    ("verdict", "strict", "drop", "release", "keep_filling"),
    [
        (ClaimVerdict.WON, False, True, False, True),
        (ClaimVerdict.PAUSED, False, True, True, False),
        (ClaimVerdict.NOT_CLAIMABLE, False, True, True, True),
        (ClaimVerdict.DEPENDENCY, False, True, True, True),
        (ClaimVerdict.DEPENDENCY, True, False, True, False),
        (ClaimVerdict.INCOMPATIBLE, True, True, True, True),
        (ClaimVerdict.LOST, True, True, True, True),
    ],
)
def test_claim_disposition_per_outcome(
    verdict: ClaimVerdict, strict: bool, drop: bool, release: bool, keep_filling: bool
) -> None:
    assert claim_verdict(_OUTCOMES[verdict]) is verdict
    disposition = claim_disposition(verdict, strict=strict)
    assert (disposition.drop_entry, disposition.release, disposition.keep_filling) == (drop, release, keep_filling)


def test_reclaim_collapses_denials_to_release() -> None:
    assert reclaim_verdict(_OUTCOMES[ClaimVerdict.WON]) is ClaimVerdict.WON
    assert reclaim_verdict(_OUTCOMES[ClaimVerdict.PAUSED]) is ClaimVerdict.PAUSED
    for verdict in (ClaimVerdict.NOT_CLAIMABLE, ClaimVerdict.DEPENDENCY, ClaimVerdict.INCOMPATIBLE, ClaimVerdict.LOST):
        assert reclaim_verdict(_OUTCOMES[verdict]) is ClaimVerdict.LOST


def test_interrupted_claim_move_table() -> None:
    def move(view: ChunkStatusView, *, requeued: bool = False, braked: bool = False) -> InterruptedClaimMove:
        return interrupted_claim_move(view, runner_id=_ME, requeued=requeued, braked=braked)

    assert move(_view(), requeued=True) is InterruptedClaimMove.RESUME_REQUEUED
    assert move(_view(route="r2"), requeued=True) is InterruptedClaimMove.RELEASE_REQUEUED_ELSEWHERE
    decided = ChunkDecisionStatusView(decision_id="dc_1", node_id="nd_gate", epoch=1)
    assert move(_view(decision=decided)) is InterruptedClaimMove.HOLD
    assert move(_view()) is InterruptedClaimMove.ADOPT  # FILL's when it owns the node entry
    assert move(_view(ChunkStatus.READY, route=None)) is InterruptedClaimMove.RECLAIM
    assert move(_view(ChunkStatus.READY, route=None), braked=True) is InterruptedClaimMove.HOLD
    assert move(_view(route="r2")) is InterruptedClaimMove.RELEASE_OTHER_RUNNER
    assert move(_view(ChunkStatus.NEEDS_HUMAN, route=None)) is InterruptedClaimMove.RELEASE_NO_ROUTE
    assert move(_view(ChunkStatus.NEEDS_HUMAN)) is InterruptedClaimMove.HOLD


def test_requeued_resume_waits_out_pause_and_terminal() -> None:
    def move(view: ChunkStatusView) -> InterruptedClaimMove:
        return interrupted_claim_move(view, runner_id=_ME, requeued=True, braked=False)

    assert move(_view(pause=_PAUSE)) is InterruptedClaimMove.HOLD
    assert move(_view(ChunkStatus.PAUSED)) is InterruptedClaimMove.HOLD
    assert move(_view(ChunkStatus.STOPPED)) is InterruptedClaimMove.HOLD
    assert move(_view(ChunkStatus.DONE)) is InterruptedClaimMove.HOLD
    assert move(_view()) is InterruptedClaimMove.RESUME_REQUEUED


def test_owns_node_entry_epochs() -> None:
    def owns(view: ChunkStatusView, *, local: int, tenure: bool = True) -> bool:
        return owns_node_entry(view, local_epoch=local, open_escalation_epoch=None, lease_in_binding_tenure=tenure)

    assert owns(_view(epoch=3), local=3)  # the runner's own epoch
    assert not owns(_view(epoch=4), local=3)  # a newer hub epoch is ADVANCE's
    assert owns(_view(epoch=4, restart_epochs=[4]), local=3)  # the current restart entry
    assert owns(_view(epoch=None), local=0, tenure=False)  # a first claim, no lease in tenure


def test_same_epoch_adopt_holds_over_unflushed_escalation() -> None:
    view = _view(epoch=3)
    assert not owns_node_entry(view, local_epoch=3, open_escalation_epoch=3, lease_in_binding_tenure=True)
    assert owns_node_entry(view, local_epoch=3, open_escalation_epoch=2, lease_in_binding_tenure=True)
    # A newer hub epoch is still ADVANCE's, escalation or not.
    assert not owns_node_entry(_view(epoch=4), local_epoch=3, open_escalation_epoch=3, lease_in_binding_tenure=True)


def test_recovery_owner() -> None:
    assert recovery_owner(None) is None
    assert recovery_owner(_lease(harness_id="oc")) == "oc"


def test_adopt_enters_a_node_the_latest_lease_did_not_run_unless_it_is_a_restart_entry() -> None:
    held = ChunkStatusView(chunk_id="ch_1", status=ChunkStatus.RUNNING, route_runner_id=_ME, latest_epoch=1)
    restarted = ChunkStatusView(
        chunk_id="ch_1", status=ChunkStatus.RUNNING, route_runner_id=_ME, latest_epoch=2, restart_epochs=[2]
    )
    assert adopt_enters_node(_lease(), held, "nd_review") is True
    assert adopt_enters_node(_lease(), held, "nd_build") is False
    assert adopt_enters_node(None, held, "nd_build") is False
    assert adopt_enters_node(_lease(), restarted, "nd_review") is False


# --- minting ---------------------------------------------------------------------------------- #


def _node(*, harnesses: list[str] | None = None) -> NodeConfig:
    node = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")]).node
    return node.model_copy(update={"session_harnesses": harnesses or []})


def test_mint_owner_source_precedence() -> None:
    resume = SessionReference("cc", "sess-a")
    select = _node(harnesses=["cc", "oc"])
    assert mint_owner_source(resume, "oc", select) is MintOwnerSource.RESUME
    assert mint_owner_source(None, "oc", select) is MintOwnerSource.EXPLICIT
    assert mint_owner_source(None, None, select) is MintOwnerSource.SELECT
    assert mint_owner_source(None, None, _node()) is MintOwnerSource.DEFAULT


def test_next_lease_epoch_above_hub_floor() -> None:
    assert next_lease_epoch(0, 5) == 6  # a chunk this runner never drove
    assert next_lease_epoch(7, 5) == 8
    assert next_lease_epoch(3, 3) == 4


def test_resolved_retries_max() -> None:
    assert resolved_retries_max(0, 4, 2) == 0
    assert resolved_retries_max(None, 4, 2) == 4
    assert resolved_retries_max(None, None, 2) == 2


def test_escalation_mint_refused_while_open() -> None:
    open_escalation = ParkedEscalation(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", epoch=1, session_id=None, closed_at=_NOW
    )
    assert not escalation_mint_admitted(open_escalation)
    assert escalation_mint_admitted(None)


def test_retry_owner_admitted() -> None:
    assert retry_owner_admitted("cc", [])
    assert retry_owner_admitted("cc", ["oc", "cc"])
    assert not retry_owner_admitted("cc", ["oc"])


# --- failing and escalating ------------------------------------------------------------------- #


def test_failure_move_precedence() -> None:
    def move(*, retried: int = 0, blocked: bool = False, detached: bool = False, braked: bool = False) -> FailureMove:
        return failure_move(retried=retried, retries_max=2, owner_blocked=blocked, detached=detached, braked=braked)

    assert move() is FailureMove.RETRY
    assert move(retried=2) is FailureMove.ESCALATE_EXHAUSTED
    assert move(retried=2, braked=True) is FailureMove.DEFER  # an exhausted escalation waits out the brake
    assert move(blocked=True) is FailureMove.ESCALATE_OWNER_UNRESOLVABLE
    assert move(blocked=True, braked=True) is FailureMove.DEFER
    assert move(retried=2, detached=True, braked=True) is FailureMove.ABANDON


def test_routed_away_abandons_before_retry() -> None:
    assert failure_move(retried=0, retries_max=2, owner_blocked=False, detached=True, braked=False) is (
        FailureMove.ABANDON
    )


def test_routed_away() -> None:
    assert not routed_away(_view(), _ME)
    assert routed_away(_view(route="r2"), _ME)
    assert routed_away(_view(route=None), _ME)


def test_owner_unresolvable_closure() -> None:
    minted = owner_unresolvable_closure(_lease(spawned=False), unavailable=True)
    assert (minted.owner_status, minted.closure_reason) == ("unavailable", ESCALATION_MINT)
    ran = owner_unresolvable_closure(_lease(), unavailable=False)
    assert (ran.owner_status, ran.closure_reason) == ("unknown", None)


def test_owner_unresolvable_move() -> None:
    assert owner_unresolvable_move(detached=True, braked=True) is OwnerUnresolvableMove.ABANDON
    assert owner_unresolvable_move(detached=False, braked=True) is OwnerUnresolvableMove.DEFER
    assert owner_unresolvable_move(detached=False, braked=False) is OwnerUnresolvableMove.ESCALATE


# --- dormant sessions ------------------------------------------------------------------------- #


def test_restart_disposition() -> None:
    assert restart_disposition(_view(pause=_PAUSE), _ME, fenced=True) is RestartDisposition.PARK
    assert restart_disposition(_view(), _ME, fenced=True) is RestartDisposition.PREEMPT
    assert restart_disposition(_view(), _ME, fenced=False) is RestartDisposition.RESTART
    assert restart_disposition(_view(route="r2", pause=_PAUSE), _ME, fenced=True) is RestartDisposition.ABANDON
    assert restart_disposition(_view(ChunkStatus.NEEDS_HUMAN), _ME, fenced=False) is RestartDisposition.ABANDON


def test_fenced_out_by_newer_epoch_or_level_restart_unless_held() -> None:
    lease = _lease(epoch=2)
    free = Fenced(TakeoverHolds())
    assert free.out(_view(epoch=3), lease)
    assert free.out(_view(epoch=2, restart_epochs=[2]), lease)
    assert not free.out(_view(epoch=2, restart_epochs=[1]), lease)
    held = Fenced(TakeoverHolds.of([_takeover(reference_epoch=2)]))
    assert not held.out(_view(epoch=3), lease)
    # A restart defers for a taken-over open lease, but not for one above the takeover's reach.
    assert Fenced(TakeoverHolds.of([_takeover(reference_epoch=1)])).out(_view(epoch=3), lease)


def test_pause_park_settled() -> None:
    park = PausePark(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW, interrupted_elicitation_id=7)
    deadline = _NOW + timedelta(seconds=SHUTDOWN_DRAIN_DEADLINE)
    assert not pause_park_drain_expired(park, now=deadline - timedelta(seconds=1))
    assert pause_park_drain_expired(park, now=deadline)
    named = PendingElicitation(
        id=7,
        lease_id="lease_1",
        epoch=1,
        pid=None,
        process_start_time=None,
        pgid=None,
        output_path="/tmp/out",
        first_launched_at=_NOW,
        relaunch_count=0,
    )
    assert park_names_elicitation(park, named)
    assert not park_names_elicitation(park, None)
    other = PendingElicitation(**{**named.__dict__, "id": 8})
    assert not park_names_elicitation(park, other)
    unnamed = PausePark(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW, interrupted_elicitation_id=None)
    assert not park_names_elicitation(unnamed, named)


def test_unpause_move_ladder() -> None:
    def move(view: ChunkStatusView, *, ask: bool = False, judge: bool = False, warm: bool = True) -> UnpauseMove:
        return unpause_move(view, _ME, ask_parked=ask, judge_parked=judge, has_env_and_session=warm)

    assert move(_view(pause=_PAUSE)) is UnpauseMove.WAIT
    assert move(_view(route="r2")) is UnpauseMove.WAIT
    assert move(_view(), ask=True, judge=True) is UnpauseMove.CLEAR_AWAIT_ANSWER
    assert move(_view(), judge=True, warm=False) is UnpauseMove.RELAUNCH_JUDGE
    assert move(_view(), warm=False) is UnpauseMove.CANNOT_RESUME
    assert move(_view()) is UnpauseMove.WAKE


def test_answer_ready() -> None:
    base = QuestionView(
        question_id="qn_1", chunk_id="ch_1", runner_id=_ME, epoch=1, question="?", asked_at="2026-10-04T12:00:00Z"
    )
    assert not answer_ready(base)
    assert not answer_ready(base.model_copy(update={"answered": True}))
    assert answer_ready(base.model_copy(update={"answered": True, "answer": "yes"}))


# --- held chunks ------------------------------------------------------------------------------ #


def test_apply_move() -> None:
    nxt = make_envelope("ch_1", "review", node_id="nd_review", choices=[("pass", "ok")])
    assert apply_move(ApplyOutcome.NEXT, nxt, chunk_paused=False) is ApplyMove.ENTER_NEXT
    assert apply_move(ApplyOutcome.NEXT, None, chunk_paused=False) is ApplyMove.NONE
    assert apply_move(ApplyOutcome.HUB_NODE_TAKEN, None, chunk_paused=True) is ApplyMove.HOLD_FOR_HUB_NODE
    assert apply_move(ApplyOutcome.MIGRATED, None, chunk_paused=False) is ApplyMove.RELEASE_MIGRATED
    assert apply_move(ApplyOutcome.DONE, None, chunk_paused=True) is ApplyMove.RELEASE_DONE
    assert apply_move(ApplyOutcome.PARKED_AT_GATE, None, chunk_paused=False) is ApplyMove.HOLD_AT_GATE


def test_next_under_chunk_pause_holds() -> None:
    assert (
        apply_move(
            ApplyOutcome.NEXT,
            make_envelope("ch_1", "review", node_id="nd_review", choices=[("pass", "ok")]),
            chunk_paused=True,
        )
        is ApplyMove.HOLD_PAUSED
    )


def test_held_chunk_reads_local_epoch_only_for_a_running_chunk() -> None:
    assert held_chunk_reads_local_epoch(_view(epoch=2))
    assert not held_chunk_reads_local_epoch(_view(epoch=None))
    assert not held_chunk_reads_local_epoch(_view(ChunkStatus.DELIVERING, epoch=2))


def test_held_chunk_move_table() -> None:
    def move(view: ChunkStatusView, *, local: int = 1) -> HeldChunkMove:
        return held_chunk_move(view, runner_id=_ME, local_latest_epoch=local, taken_over=False)

    resolved = ChunkDecisionStatusView(decision_id="dc_1", node_id="nd_gate", epoch=1, resolved_choice="ok")
    assert move(_view(ChunkStatus.DONE)) is HeldChunkMove.RELEASE_DONE
    assert move(_view(ChunkStatus.STOPPED)) is HeldChunkMove.RELEASE_STOPPED
    assert move(_view(ChunkStatus.WAITING_ON_HUMAN, route="r2", decision=resolved)) is (
        HeldChunkMove.RELEASE_DETACHED_GATE
    )
    assert move(_view(ChunkStatus.WAITING_ON_HUMAN, decision=resolved)) is HeldChunkMove.RESOLVE_GATE
    transitioned = resolved.model_copy(update={"transitioned": True})
    assert move(_view(ChunkStatus.WAITING_ON_HUMAN, decision=transitioned)) is HeldChunkMove.HOLD
    assert move(_view(epoch=2), local=1) is HeldChunkMove.SPAWN_ADVANCED
    assert move(_view(epoch=1), local=1) is HeldChunkMove.HOLD  # same epoch: a just-escalated chunk
    assert move(_view(ChunkStatus.DELIVERING)) is HeldChunkMove.POLL_HUB_NODE


def test_takeover_suppresses_only_session_arms() -> None:
    def move(view: ChunkStatusView) -> HeldChunkMove:
        return held_chunk_move(view, runner_id=_ME, local_latest_epoch=1, taken_over=True)

    resolved = ChunkDecisionStatusView(decision_id="dc_1", node_id="nd_gate", epoch=1, resolved_choice="ok")
    assert move(_view(ChunkStatus.WAITING_ON_HUMAN, decision=resolved)) is HeldChunkMove.HOLD
    assert move(_view(epoch=2)) is HeldChunkMove.HOLD
    assert move(_view(ChunkStatus.DONE)) is HeldChunkMove.RELEASE_DONE
    assert move(_view(ChunkStatus.STOPPED)) is HeldChunkMove.RELEASE_STOPPED
    assert move(_view(ChunkStatus.DELIVERING)) is HeldChunkMove.POLL_HUB_NODE


def test_gate_resolution_lease_id() -> None:
    decision = ChunkDecisionStatusView(decision_id="dc_1", node_id="nd_gate", epoch=2, resolved_choice="ok")
    assert gate_resolution_lease_id(_lease(epoch=2), decision) == "lease_1"
    assert gate_resolution_lease_id(_lease(epoch=1), decision) is None
    assert gate_resolution_lease_id(None, decision) is None


# --- the outbound drain ----------------------------------------------------------------------- #


def test_completion_move() -> None:
    assert completion_move(ApplyOutcome.FAILURE, capped=True) is CompletionMove.FAIL
    assert completion_move(ApplyOutcome.NEXT, capped=False) is CompletionMove.CLOSE_AND_APPLY
    assert completion_move(ApplyOutcome.DONE, capped=True) is CompletionMove.CLOSE_AND_APPLY


def test_spend_cap_escalation_is_local() -> None:
    # Capped at an advancing step: closed escalated — the escalation reads open locally — and the
    # next node is not entered.
    assert completion_move(ApplyOutcome.NEXT, capped=True) is CompletionMove.ESCALATE_SPEND_CAP
    closure = COMPLETION_CLOSURES[CompletionMove.ESCALATE_SPEND_CAP]
    assert (closure.reason, closure.escalation_cause) == ("escalated", "spend-cap")
    assert cause_of(closure.reason, closure.escalation_cause) == "spend-cap"
    assert COMPLETION_CLOSURES[CompletionMove.CLOSE_AND_APPLY] == CompletionClosure(LeaseClosureReason.TRANSITIONED)


def test_decision_move() -> None:
    assert decision_move(ApplyOutcome.FAILURE) is DecisionMove.FAIL
    assert decision_move(ApplyOutcome.PARKED_AT_GATE) is DecisionMove.CLOSE_PARKED


def test_spend_cap_reached_and_detail() -> None:
    cost = ChunkUsageTotalView.zero().model_copy(update={"cost_usd": 7.0})
    assert spend_cap_reached(cost, 5.0)
    assert spend_cap_reached(cost, 7.0)
    assert not spend_cap_reached(cost, 7.01)
    assert not spend_cap_reached(cost, None)
    assert spend_cap_detail(cost, 5.0) == "spend cap $5.00 reached (spend $7.00)"
    partial = cost.model_copy(update={"billed_partial": True})
    assert (
        spend_cap_detail(partial, 5.0) == "spend cap $5.00 reached (spend $7.00 (PARTIAL — true spend may be higher))"
    )


# --- loop steps ------------------------------------------------------------------------------- #


def test_reap_move_table() -> None:
    def move(lease: Lease, **kw: bool) -> ReapMove:
        flags = {"taken_over": False, "parked": False, "alive": True, "stale": False, "braked": False, **kw}
        return reap_move(lease, **flags)

    assert move(_lease(), taken_over=True, stale=True) is ReapMove.SKIP
    assert move(_lease(), parked=True, stale=True) is ReapMove.SKIP  # the reap clock is stopped
    assert move(_lease(spawned=False)) is ReapMove.REAP_UNSPAWNED
    assert move(_lease(spawned=False), braked=True) is ReapMove.REAP_UNSPAWNED  # crash residue, not deferred
    assert move(_lease(), alive=False) is ReapMove.LEAVE_EXITED
    assert move(_lease()) is ReapMove.RUN_ON
    assert move(_lease(), stale=True) is ReapMove.REAP_STALLED
    assert move(_lease(), stale=True, braked=True) is ReapMove.DEFER


def test_resumable_exclusions() -> None:
    def ok(lease: Lease, **sets: set[str]) -> bool:
        base: dict[str, set[str]] = {
            "parked": set(),
            "pending_submission": set(),
            "eliciting": set(),
            "backing_off": set(),
        }
        return resumable(lease, **{**base, **sets}, taken_over=False)

    assert ok(_lease())
    assert not ok(_lease(spawned=False))
    for name in ("parked", "pending_submission", "eliciting", "backing_off"):
        assert not ok(_lease(), **{name: {"lease_1"}})


def test_takeover_chunk_never_resumed() -> None:
    assert not resumable(
        _lease(), parked=set(), pending_submission=set(), eliciting=set(), backing_off=set(), taken_over=True
    )


def test_crash_orphaned() -> None:
    assert crash_orphaned(_lease(), session_ended=False, alive=False, stale=False)
    assert not crash_orphaned(_lease(), session_ended=True, alive=False, stale=False)
    assert not crash_orphaned(_lease(), session_ended=False, alive=True, stale=False)
    assert not crash_orphaned(_lease(), session_ended=False, alive=False, stale=True)


def test_lease_reconcile_move() -> None:
    def move(
        view: ChunkStatusView, *, lease: Lease | None = None, parked: set[str] | None = None, fenced: bool = False
    ):  # type: ignore[no-untyped-def]
        return lease_reconcile_move(view, lease or _lease(), runner_id=_ME, pause_parked=parked or set(), fenced=fenced)

    assert move(_view(ChunkStatus.STOPPED)) is LeaseReconcileMove.ABANDON
    assert move(_view(route="r2"), fenced=True) is LeaseReconcileMove.ABANDON
    assert move(_view(pause=_PAUSE), fenced=True) is LeaseReconcileMove.PARK  # a pause outranks a move
    assert move(_view(pause=_PAUSE), parked={"lease_1"}) is LeaseReconcileMove.NONE  # parks once
    assert move(_view(), fenced=True) is LeaseReconcileMove.PREEMPT
    assert move(_view()) is LeaseReconcileMove.NONE


def test_park_paused_skips_unspawned() -> None:
    assert (
        lease_reconcile_move(
            _view(pause=_PAUSE), _lease(spawned=False), runner_id=_ME, pause_parked=set(), fenced=False
        )
        is LeaseReconcileMove.NONE
    )


def test_advance_move_precedence() -> None:
    def move(lease: Lease | None = None, /, **kw: bool) -> AdvanceMove:
        flags = {
            "taken_over": False,
            "resume_marked": False,
            "pending_submission": False,
            "pause_parked": False,
            "ask_parked": False,
            "backing_off": False,
            "alive": False,
            **kw,
        }
        return advance_move(lease or _lease(), **flags)

    every: dict[str, bool] = {
        "resume_marked": True,
        "pending_submission": True,
        "pause_parked": True,
        "ask_parked": True,
    }
    assert move(taken_over=True, **every) is AdvanceMove.SKIP_TAKEN_OVER
    assert move(_lease(spawned=False), **every) is AdvanceMove.SKIP_UNSPAWNED
    assert move(**every) is AdvanceMove.SKIP_RESUME_MARKED
    assert move(pending_submission=True, pause_parked=True) is AdvanceMove.SKIP_PENDING_SUBMISSION
    # An operator's resume goes ahead of the rest of an overload wait.
    assert move(pause_parked=True, ask_parked=True, backing_off=True) is AdvanceMove.ON_UNPAUSE
    assert move(ask_parked=True, backing_off=True) is AdvanceMove.ON_ANSWER
    assert move(backing_off=True, alive=True) is AdvanceMove.ON_OVERLOAD_BACKOFF
    assert move(alive=True) is AdvanceMove.RUNNING
    assert move() is AdvanceMove.EXITED


def test_open_slots() -> None:
    assert open_slots(4, 1) == 3
    assert open_slots(4, 4) == 0
    assert open_slots(2, 5) == 0


# --- sessions and harness selection ----------------------------------------------------------- #


def test_resume_inherits_stamps() -> None:
    lease = _lease().__class__(
        **{**_lease().__dict__, "resolved_model": "opus", "resolved_effort": "high", "resolved_compaction_window": "1m"}
    )
    resumed = ResumedSession(session=SessionReference("cc", "sess-a"), lease=lease)
    assert resumed.inherited_stamps() == ("opus", "high", "1m")
    assert ResumedSession(session=SessionReference("cc", "sess-a"), lease=None).inherited_stamps() == (None, None, None)


def test_select_harness_strict_and_skips() -> None:
    node = _node(harnesses=["cc"])
    assert not selection_is_strict(node)
    assert not selection_is_strict(node.model_copy(update={"session_model": ["opus"]}))
    assert selection_is_strict(node.model_copy(update={"session_model": ["blizzard:deep"]}))
    assert selection_is_strict(node.model_copy(update={"session_harnesses": ["cc", "oc"], "session_model": ["opus"]}))
    assert member_skip_reason(healthy=False, maps_authored_tier=False) == "unhealthy"
    assert member_skip_reason(healthy=True, maps_authored_tier=False) == "no-authored-tier"
    assert member_skip_reason(healthy=True, maps_authored_tier=True) is None


def test_rotation_breach_order() -> None:
    rotate = RotatePolicyView(max_context_tokens=100, max_invocations=3, max_transcript_bytes=1000)
    assert rotation_breach(rotate, context_tokens=101, invocations=4, transcript_bytes=1001) == "max_context_tokens"
    assert rotation_breach(rotate, context_tokens=100, invocations=4, transcript_bytes=1001) == "max_invocations"
    assert rotation_breach(rotate, context_tokens=None, invocations=3, transcript_bytes=1001) == "max_transcript_bytes"
    # An unreadable signal is never a breach.
    assert rotation_breach(rotate, context_tokens=None, invocations=None, transcript_bytes=None) is None
    assert rotation_breach(RotatePolicyView(), context_tokens=10**9, invocations=10**9, transcript_bytes=10**9) is None
    assert model_drifted("opus", "sonnet")
    assert not model_drifted(None, "sonnet")
    assert not model_drifted("opus", None)
    assert not model_drifted("opus", "opus")
