"""Gate decision resolution rules, pinned by value — no repository, no clock."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunk.model import DecisionChoice, DecisionClosed, GateDecision, NotADecisionChoice

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_OPEN = GateDecision(
    decision_id="dec_1",
    chunk_id="chk_1",
    node_id="nd_gate",
    node_name="gate",
    epoch=1,
    choices=[DecisionChoice(name="yes", description=""), DecisionChoice(name="no", description="")],
    submitted_at=_T0,
)
_RESOLVED = replace(_OPEN, resolved_choice="yes", resolved_by="op", resolved_at=_T0)
_CLOSED_BY_RESTART = replace(_OPEN, transitioned=True)


def test_an_open_gate_takes_one_of_its_choices() -> None:
    _OPEN.require_resolvable(choice="no", chunk_status=ChunkStatus.WAITING_ON_HUMAN)
    _OPEN.require_resolvable(choice="yes", chunk_status=ChunkStatus.PAUSED)


def test_a_choice_outside_the_gates_own_is_refused_first() -> None:
    for decision in (_OPEN, _RESOLVED, _CLOSED_BY_RESTART):
        with pytest.raises(NotADecisionChoice, match="one of: yes, no"):
            decision.require_resolvable(choice="maybe", chunk_status=ChunkStatus.STOPPED)


def test_a_gate_a_restart_moved_the_chunk_off_is_closed_undecided() -> None:
    assert _CLOSED_BY_RESTART.closed_undecided and not _CLOSED_BY_RESTART.is_open
    with pytest.raises(DecisionClosed, match="restart"):
        _CLOSED_BY_RESTART.require_resolvable(choice="yes", chunk_status=ChunkStatus.RUNNING)


@pytest.mark.parametrize("status", [ChunkStatus.STOPPED, ChunkStatus.DONE])
def test_the_chunk_ending_closes_its_open_gate(status: ChunkStatus) -> None:
    with pytest.raises(DecisionClosed, match=status.value):
        _OPEN.require_resolvable(choice="yes", chunk_status=status)


def test_a_resolved_gate_lets_every_retry_through_to_the_first_write_wins_race() -> None:
    assert not _RESOLVED.closed_undecided
    for status in (ChunkStatus.STOPPED, ChunkStatus.DONE, ChunkStatus.RUNNING):
        _RESOLVED.require_resolvable(choice="no", chunk_status=status)
    replace(_RESOLVED, transitioned=True).require_resolvable(choice="yes", chunk_status=ChunkStatus.DONE)


def test_a_resolving_transition_needs_this_gates_resolution_to_the_same_choice() -> None:
    def refusal(
        decision: GateDecision, *, chunk_id: str = "chk_1", node_id: str = "nd_gate", choice: str = "yes"
    ) -> str | None:
        return decision.resolving_refusal(chunk_id=chunk_id, node_id=node_id, node_name="gate", choice=choice)

    assert refusal(_RESOLVED) is None
    assert refusal(_RESOLVED, chunk_id="chk_2") == "decision dec_1 does not match node `gate`"
    assert refusal(_RESOLVED, node_id="nd_other") == "decision dec_1 does not match node `gate`"
    assert refusal(_OPEN) == "decision dec_1 is not yet resolved"
    assert refusal(_RESOLVED, choice="no") == "choice `no` is not the resolved choice `yes`"
