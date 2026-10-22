"""The runner's decisions on a response value this build does not know (``bzh:open-response-vocabulary``), pinned
by value: an unknown ``ApplyOutcome`` is accepted, and an unknown ``ChunkStatus`` satisfies no status test, so no
decision abandons, resumes, spawns into, or releases a lease on it."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.node_steps import ApplyOutcome
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.runner.cli.control import _echo_identity
from blizzard.runner.cli.traces import harness_telemetry_lines
from blizzard.runner.hub.client import IncompatibleDenial, RouteClaimOutcome
from blizzard.runner.leases.escalations import ParkedEscalation
from blizzard.runner.leases.model import Lease
from blizzard.runner.lifecycle.model import (
    ApplyMove,
    CompletionMove,
    DecisionMove,
    HeldChunkMove,
    InterruptedClaimMove,
    LeaseReconcileMove,
    RestartDisposition,
    SpawnHold,
    apply_move,
    chunk_paused,
    claim_denial_fields,
    completion_move,
    decision_move,
    held_chunk_move,
    held_chunk_reads_local_epoch,
    interrupted_claim_move,
    lease_reconcile_move,
    restart_disposition,
    spawn_hold,
)
from blizzard.runner.lifecycle.takeover import OpenTakeover
from blizzard.runner.node_steps.chunk_state import ChunkGate, ChunkPause, ChunkState
from blizzard.wire.runner_status import IdentityVerdictView
from tests.runner_fakes import make_envelope

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
_ME = "r1"
_GAINED = Unrecognized("gained")
_PAUSE = ChunkPause(by="operator", set_at="2026-10-21T12:00:00Z")


def _view(*, route: str | None = _ME, pause: ChunkPause | None = None, decision: ChunkGate | None = None) -> ChunkState:
    return ChunkState(
        chunk_id="ch_1", status=_GAINED, route_runner_id=route, latest_epoch=2, pause=pause, decision=decision
    )


def _lease() -> Lease:
    return Lease(
        lease_id="lease_1",
        chunk_id="ch_1",
        graph_id="g_1",
        node_id="nd_build",
        node_name="build",
        epoch=1,
        retries_max=2,
        created_at=_NOW,
        pid=100,
        process_start_time="start-100",
        session_id="sess-a",
        harness_id="cc",
    )


# --- An unknown ApplyOutcome is accepted ------------------------------------------------------------------------------


def test_an_unknown_completion_outcome_never_fails_and_still_meets_the_spend_cap() -> None:
    assert completion_move(_GAINED, capped=False) is CompletionMove.CLOSE_AND_APPLY
    assert completion_move(_GAINED, capped=True) is CompletionMove.ESCALATE_SPEND_CAP


def test_an_unknown_decision_outcome_closes_parked() -> None:
    assert decision_move(_GAINED) is DecisionMove.CLOSE_PARKED


def test_an_unknown_apply_outcome_takes_no_local_move() -> None:
    nxt = make_envelope("ch_1", "review", node_id="nd_review", choices=[("pass", "ok")])
    assert apply_move(_GAINED, nxt, hold=None) is ApplyMove.NONE
    assert apply_move(_GAINED, None, hold=None) is ApplyMove.NONE


# --- An unknown ChunkStatus satisfies no status test ------------------------------------------------------------------


def test_a_restart_on_an_unknown_status_holds_its_mark() -> None:
    assert restart_disposition(_view(), _ME, fenced=False) is RestartDisposition.HOLD


def test_route_pause_and_fence_still_decide_a_restart_on_an_unknown_status() -> None:
    assert restart_disposition(_view(pause=_PAUSE), _ME, fenced=False) is RestartDisposition.PARK
    assert restart_disposition(_view(), _ME, fenced=True) is RestartDisposition.PREEMPT
    assert restart_disposition(_view(route="r2"), _ME, fenced=False) is RestartDisposition.ABANDON


def test_a_requeue_still_routed_here_holds_on_an_unknown_status() -> None:
    move = interrupted_claim_move(_view(), runner_id=_ME, requeued=True, braked=False)
    assert move is InterruptedClaimMove.HOLD_STATUS_UNRECOGNIZED


def test_a_pause_or_decision_hold_on_an_unknown_status_is_not_labelled_unrecognized() -> None:
    paused = interrupted_claim_move(_view(pause=_PAUSE), runner_id=_ME, requeued=True, braked=False)
    assert paused is InterruptedClaimMove.HOLD
    gate = ChunkGate(decision_id="dc_1", node_id="nd_gate", epoch=1)
    decided = interrupted_claim_move(_view(decision=gate), runner_id=_ME, requeued=False, braked=False)
    assert decided is InterruptedClaimMove.HOLD


def test_an_unrequeued_interrupted_claim_moves_only_on_a_route_fact() -> None:
    def move(view: ChunkState) -> InterruptedClaimMove:
        return interrupted_claim_move(view, runner_id=_ME, requeued=False, braked=False)

    assert move(_view()) is InterruptedClaimMove.HOLD
    assert move(_view(route="r2")) is InterruptedClaimMove.RELEASE_OTHER_RUNNER
    assert move(_view(route=None)) is InterruptedClaimMove.RELEASE_NO_ROUTE


def test_an_unknown_status_holds_a_spawn_without_reading_as_paused() -> None:
    assert not chunk_paused(_view())
    assert spawn_hold(_view()) is SpawnHold.STATUS_UNRECOGNIZED
    assert spawn_hold(_view(pause=_PAUSE)) is SpawnHold.PAUSED


def test_a_next_node_waits_on_an_unknown_status() -> None:
    nxt = make_envelope("ch_1", "review", node_id="nd_review", choices=[("pass", "ok")])
    move = apply_move(ApplyOutcome.NEXT, nxt, hold=SpawnHold.STATUS_UNRECOGNIZED)
    assert move is ApplyMove.HOLD_STATUS_UNRECOGNIZED
    assert apply_move(ApplyOutcome.NEXT, nxt, hold=SpawnHold.PAUSED) is ApplyMove.HOLD_PAUSED


def test_a_held_chunk_on_an_unknown_status_holds() -> None:
    def move(view: ChunkState) -> HeldChunkMove:
        return held_chunk_move(
            view, runner_id=_ME, local_latest_epoch=1, lease_in_binding_tenure=True, taken_over=False
        )

    assert move(_view()) is HeldChunkMove.HOLD
    assert not held_chunk_reads_local_epoch(_view())
    resolved = ChunkGate(decision_id="dc_1", node_id="nd_gate", epoch=2, resolved_choice="ok")
    assert move(_view(decision=resolved)) is HeldChunkMove.RESOLVE_GATE


def test_a_lease_on_an_unknown_status_is_kept_unless_a_route_pause_or_fence_moves_it() -> None:
    def move(view: ChunkState, *, fenced: bool = False) -> LeaseReconcileMove:
        return lease_reconcile_move(view, _lease(), runner_id=_ME, pause_parked=set(), fenced=fenced)

    assert move(_view()) is LeaseReconcileMove.NONE
    assert move(_view(route="r2")) is LeaseReconcileMove.ABANDON
    assert move(_view(pause=_PAUSE)) is LeaseReconcileMove.PARK
    assert move(_view(), fenced=True) is LeaseReconcileMove.PREEMPT


def test_an_unknown_status_neither_supersedes_an_escalation_nor_ends_a_takeover() -> None:
    escalation = ParkedEscalation(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", epoch=1, session_id=None, closed_at=_NOW
    )
    takeover = OpenTakeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
        harness_id="cc",
        reference_epoch=1,
    )
    assert not escalation.superseded_by(_view(), runner_id=_ME, fenced_out=False)
    assert escalation.superseded_by(_view(route="r2"), runner_id=_ME, fenced_out=False)
    assert not takeover.ended_by(_view())


# --- An unknown refusal cause is carried raw --------------------------------------------------------------------------


def test_an_unknown_claim_denial_reason_is_logged_raw() -> None:
    denial = IncompatibleDenial(chunk_id="ch_1", incompatible_runner_id=_ME, detail="why", reason=_GAINED)
    assert claim_denial_fields(RouteClaimOutcome(denied_incompatible=denial)) == {"reason": "gained", "detail": "why"}


def test_the_status_identity_line_shows_an_unknown_state_raw_with_no_refusal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    view = IdentityVerdictView.model_validate(
        {
            "state": "gained",
            "store_runner_id": _ME,
            "token_runner_id": _ME,
            "checked_at": None,
            "reset_command": "blizzard runner init",
        }
    )
    _echo_identity(view)
    assert capsys.readouterr().out == "  identity: unknown identity state 'gained'\n"


def test_the_harness_telemetry_line_shows_an_unknown_outcome_raw() -> None:
    off = {"outcome": "off", "accepted": 0, "dropped": 0}
    lines = harness_telemetry_lines(
        {"traces": {"outcome": "gained", "accepted": 0, "dropped": 0}, "metrics": off, "logs": off}
    )
    assert lines == ["harness telemetry:", "  traces: gained", "  metrics: off", "  logs: off"]
