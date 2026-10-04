"""The local escalation's rules, pinned by value: why a closure escalated, when the hub's
view supersedes an escalation, and which commands an escalation can carry."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases.closure import ESCALATION_MINT, NO_ACCEPTABLE_HARNESS_MINT, cause_of
from blizzard.runner.leases.escalations import (
    ESCALATION_TRANSITIONS,
    EscalationState,
    EscalationVerb,
    ParkedEscalation,
    resume_workdir,
)
from blizzard.runner.lifecycle.takeover import TakeoverCommand
from blizzard.wire.chunk import ChunkStatusView

pytestmark = pytest.mark.unit

_AT = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
_SESSION = SessionReference("claude-code", "sess-a")


def _escalation() -> ParkedEscalation:
    return ParkedEscalation(
        lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", epoch=3, session_id="sess-a", closed_at=_AT
    )


def _view(status: ChunkStatus = ChunkStatus.NEEDS_HUMAN, *, route: str | None = "r1") -> ChunkStatusView:
    return ChunkStatusView(chunk_id="ch_1", status=status, route_runner_id=route, latest_epoch=3)


# --- cause_of ---------------------------------------------------------------------------


def test_each_mint_reason_implies_its_own_cause() -> None:
    assert cause_of(ESCALATION_MINT) == "owner-unresolvable"
    assert cause_of(NO_ACCEPTABLE_HARNESS_MINT) == "no-acceptable-harness"


def test_a_mint_reason_outranks_whatever_was_recorded() -> None:
    assert cause_of(NO_ACCEPTABLE_HARNESS_MINT, "retries-exhausted") == "no-acceptable-harness"


def test_an_ordinary_escalation_reads_its_recorded_cause() -> None:
    assert cause_of(LeaseClosureReason.ESCALATED, "retries-exhausted") == "retries-exhausted"
    assert cause_of(LeaseClosureReason.ESCALATED, "owner-unresolvable") == "owner-unresolvable"


def test_an_escalation_recorded_without_a_cause_has_none() -> None:
    assert cause_of(LeaseClosureReason.ESCALATED) is None


@pytest.mark.parametrize("reason", [LeaseClosureReason.FAILED, LeaseClosureReason.REAPED, "transitioned"])
def test_a_closure_that_escalated_nothing_has_no_cause(reason: str) -> None:
    assert cause_of(reason, "retries-exhausted") is None


# --- ParkedEscalation.superseded_by -----------------------------------------------------


@pytest.mark.parametrize("status", [ChunkStatus.STOPPED, ChunkStatus.DONE])
def test_a_terminal_chunk_supersedes(status: ChunkStatus) -> None:
    assert _escalation().superseded_by(_view(status), runner_id="r1", fenced_out=False) is True


@pytest.mark.parametrize("route", ["r2", None])
def test_a_route_moved_or_released_supersedes(route: str | None) -> None:
    assert _escalation().superseded_by(_view(route=route), runner_id="r1", fenced_out=False) is True


def test_a_fenced_out_epoch_supersedes() -> None:
    assert _escalation().superseded_by(_view(), runner_id="r1", fenced_out=True) is True


def test_a_chunk_still_routed_here_and_not_fenced_stays_open() -> None:
    assert _escalation().superseded_by(_view(), runner_id="r1", fenced_out=False) is False


def test_supersession_closes_the_escalation_and_a_requeue_does_not() -> None:
    assert ESCALATION_TRANSITIONS[EscalationState.OPEN] == frozenset(EscalationVerb)
    assert ESCALATION_TRANSITIONS[EscalationState.SUPERSEDED] == frozenset()


# --- resume_workdir / TakeoverCommand.wrapped_for --------------------------------------


def _binding(env: str) -> EnvBinding:
    return EnvBinding(chunk_id="ch_1", environment_id=env, workdir=f"/ws/{env}", bound_at=_AT)


def test_the_resume_lands_in_the_first_held_binding() -> None:
    assert resume_workdir(_SESSION, [_binding("e1"), _binding("e2")]) == "/ws/e1"


def test_no_session_or_no_held_binding_carries_no_resume() -> None:
    assert resume_workdir(None, [_binding("e1")]) is None
    assert resume_workdir(_SESSION, []) is None


def test_the_wrapped_command_needs_a_resume_command_and_a_runner_dir() -> None:
    assert TakeoverCommand.wrapped_for("ch_1", resume_command="claude --resume s", runner_dir="/opt/r") == (
        "blizzard runner takeover ch_1 --dir /opt/r"
    )
    assert TakeoverCommand.wrapped_for("ch_1", resume_command="", runner_dir="/opt/r") is None
    assert TakeoverCommand.wrapped_for("ch_1", resume_command="claude --resume s", runner_dir="") is None


def test_the_wrapped_command_quotes_its_operands() -> None:
    wrapped = TakeoverCommand.wrapped_for("ch_1", resume_command="x", runner_dir="/opt/runner dir")
    assert wrapped == "blizzard runner takeover ch_1 --dir '/opt/runner dir'"
