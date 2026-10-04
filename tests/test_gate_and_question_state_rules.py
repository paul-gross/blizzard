"""Gate-decision and question state tables (unit tier, by value): each derives its state
from its own rows, and the declared table says how a resolution or a delivery lands there."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.chunk.model import (
    GATE_RESOLVE_LEGALITY,
    QUESTION_DELIVERY_LEGALITY,
    DecisionChoice,
    GateDecision,
    GateState,
    GateVerdict,
    NodeQuestion,
    QuestionDelivery,
    QuestionState,
)

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_OPEN_GATE = GateDecision(
    decision_id="dec_1",
    chunk_id="chk_1",
    node_id="nd_gate",
    node_name="gate",
    epoch=1,
    choices=[DecisionChoice(name="yes", description="")],
    submitted_at=_T0,
)
_RESOLVED_GATE = replace(_OPEN_GATE, resolved_choice="yes", resolved_by="op", resolved_at=_T0)
_CLOSED_GATE = replace(_OPEN_GATE, transitioned=True)
_CARRIED_RESOLVED_GATE = replace(_RESOLVED_GATE, transitioned=True)

_ASKED = NodeQuestion(
    question_id="qn_1",
    chunk_id="chk_1",
    node_id="nd_build",
    session_id="sess-1",
    runner_id="runner-a",
    epoch=1,
    question="Which API?",
    options=["rest"],
    asked_at=_T0,
)
_ANSWERED = replace(_ASKED, answered=True, answer="rest", answered_by="op", answered_at=_T0)
_DELIVERED = replace(_ANSWERED, delivered=True, delivered_at=_T0)


def test_the_gate_table_covers_every_state() -> None:
    assert set(GATE_RESOLVE_LEGALITY) == set(GateState)


@pytest.mark.parametrize(
    ("gate", "state", "verdict"),
    [
        (_OPEN_GATE, GateState.OPEN, GateVerdict.APPLY),
        (_RESOLVED_GATE, GateState.RESOLVED, GateVerdict.REPLAY),
        (_CARRIED_RESOLVED_GATE, GateState.RESOLVED, GateVerdict.REPLAY),
        (_CLOSED_GATE, GateState.CLOSED_UNDECIDED, GateVerdict.REFUSE),
    ],
)
def test_a_gate_derives_its_state_and_the_table_gives_the_resolution_verdict(
    gate: GateDecision, state: GateState, verdict: GateVerdict
) -> None:
    assert gate.state() is state
    assert gate.resolve_verdict() is verdict


def test_the_question_table_covers_every_state() -> None:
    assert set(QUESTION_DELIVERY_LEGALITY) == set(QuestionState)


@pytest.mark.parametrize(
    ("question", "superseded", "state", "delivery"),
    [
        (_ASKED, False, QuestionState.OPEN, QuestionDelivery.REFUSE),
        (_ANSWERED, False, QuestionState.ANSWERED, QuestionDelivery.RECORD),
        (_DELIVERED, False, QuestionState.DELIVERED, QuestionDelivery.REPLAY),
        (_ANSWERED, True, QuestionState.SUPERSEDED, QuestionDelivery.REFUSE),
        (_DELIVERED, True, QuestionState.SUPERSEDED, QuestionDelivery.REFUSE),
    ],
)
def test_a_question_derives_its_state_and_the_table_gives_the_delivery(
    question: NodeQuestion, superseded: bool, state: QuestionState, delivery: QuestionDelivery
) -> None:
    assert question.state(superseded_by_restart=superseded) is state
    assert QUESTION_DELIVERY_LEGALITY[state] is delivery
    assert question.delivery(chunk_id="chk_1", superseded_by_restart=superseded)[0] is delivery


def test_a_refused_delivery_names_why() -> None:
    assert _ASKED.delivery(chunk_id="chk_1", superseded_by_restart=False) == (
        QuestionDelivery.REFUSE,
        "question qn_1 is not answered",
    )
    assert _ANSWERED.delivery(chunk_id="chk_1", superseded_by_restart=True) == (
        QuestionDelivery.REFUSE,
        "question qn_1 was superseded by a restart",
    )
