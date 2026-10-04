"""Node question, envelope, and runner fact-intake rules, pinned by value — no repository, no clock."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    LeaseFact,
    NodeQuestion,
    QuestionClosed,
    QuestionDelivery,
    TransitionFact,
)
from blizzard.hub.domain.execution.envelope import Envelope, NoCurrentNode
from blizzard.hub.domain.execution.facts import (
    LocalPause,
    Payload,
    admitted_event_kind,
    requires_route_token,
    subscription_identity,
)
from blizzard.hub.domain.execution.questions import parse_instant
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL, Node
from blizzard.wire.facts import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    LEASE_MINTED,
    QUESTION_ASKED,
    RUNNER_LOCALLY_PAUSED,
    RUNNER_LOCALLY_RESUMED,
    USAGE_RECORDED,
)
from tests.support import make_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_NOW = _T0 + timedelta(hours=1)
_ASKED = NodeQuestion(
    question_id="qn_1",
    chunk_id="chk_1",
    node_id="nd_build",
    session_id="sess-1",
    runner_id="runner-a",
    epoch=1,
    question="Which API?",
    options=["rest", "graphql"],
    asked_at=_T0,
)
_ANSWERED = replace(_ASKED, answered=True, answer="rest", answered_by="op", answered_at=_T0)
_DELIVERED = replace(_ANSWERED, delivered=True, delivered_at=_T0)


# --- Answer ------------------------------------------------------------------


@pytest.mark.parametrize("status", [ChunkStatus.WAITING_ON_HUMAN, ChunkStatus.RUNNING, ChunkStatus.PAUSED])
def test_a_live_chunks_question_is_answerable(status: ChunkStatus) -> None:
    _ASKED.require_answerable(status)


@pytest.mark.parametrize("status", [ChunkStatus.STOPPED, ChunkStatus.DONE])
def test_an_ended_chunks_question_is_closed(status: ChunkStatus) -> None:
    with pytest.raises(QuestionClosed, match=status.value):
        _ASKED.require_answerable(status)


# --- Delivery ----------------------------------------------------------------


def test_an_answered_question_records_its_delivery_once() -> None:
    assert _ANSWERED.delivery(chunk_id="chk_1", superseded_by_restart=False) == (QuestionDelivery.RECORD, None)
    assert _DELIVERED.delivery(chunk_id="chk_1", superseded_by_restart=False) == (QuestionDelivery.REPLAY, None)


@pytest.mark.parametrize(
    ("question", "chunk_id", "superseded", "detail"),
    [
        (_ANSWERED, "chk_2", False, "question qn_1 belongs to chunk chk_1"),
        (_DELIVERED, "chk_2", False, "question qn_1 belongs to chunk chk_1"),
        (_ANSWERED, "chk_1", True, "question qn_1 was superseded by a restart"),
        (_ASKED, "chk_1", False, "question qn_1 is not answered"),
    ],
)
def test_a_delivery_the_question_cannot_take_is_refused(
    question: NodeQuestion, chunk_id: str, superseded: bool, detail: str
) -> None:
    assert question.delivery(chunk_id=chunk_id, superseded_by_restart=superseded) == (QuestionDelivery.REFUSE, detail)


# --- Instants ----------------------------------------------------------------


def test_an_instant_is_coerced_to_utc_or_falls_back() -> None:
    offset = datetime(2026, 1, 1, 3, tzinfo=timezone(timedelta(hours=3)))
    assert parse_instant(offset.isoformat(), _NOW) == _T0
    assert parse_instant("2026-01-01T00:00:00", _NOW) == _T0
    assert parse_instant("not a stamp", _NOW) == _NOW
    assert parse_instant(None, _NOW) == _NOW
    assert parse_instant(12, _NOW) == _NOW


# --- Envelope ----------------------------------------------------------------

_BUILD = Node(
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
    choices=[],
)
_GRAPH = make_graph("gr_a", "work", entry_node_id="nd_build", nodes=[_BUILD])
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_a", work_refs=[], minted_at=_T0)
_RUNNING = ChunkFacts(minted=True, promoted=True, leases=[LeaseFact(epoch=2, minted_at=_T0)])


def test_the_envelope_is_the_current_node_at_the_epoch_floor() -> None:
    envelope = Envelope.current(_CHUNK, _GRAPH, _RUNNING, [])
    assert (envelope.node, envelope.epoch) == (_BUILD, 2)


@pytest.mark.parametrize(
    "facts",
    [
        replace(_RUNNING, stopped=True, stopped_at=_T0),
        replace(_RUNNING, operator_completed=True, operator_completed_at=_T0),
        replace(
            _RUNNING,
            transitions=[
                TransitionFact(to_node_id=RESERVED_TERMINAL, to_node_executor=Executor.RUNNER, epoch=2, recorded_at=_T0)
            ],
        ),
    ],
)
def test_every_ended_chunk_has_no_envelope(facts: ChunkFacts) -> None:
    with pytest.raises(NoCurrentNode):
        Envelope.current(_CHUNK, _GRAPH, facts, [])


# --- Runner fact intake ------------------------------------------------------


@pytest.mark.parametrize("kind", [LEASE_MINTED, ESCALATION_RECORDED, QUESTION_ASKED])
def test_attempt_facts_are_route_token_gated_and_refused_without_a_chunk(kind: str) -> None:
    assert requires_route_token(kind, "chk_1") is True
    assert requires_route_token(kind, None) is None


@pytest.mark.parametrize("kind", [ANSWER_DELIVERED, USAGE_RECORDED, RUNNER_LOCALLY_PAUSED])
def test_other_facts_are_not_route_token_gated(kind: str) -> None:
    assert requires_route_token(kind, "chk_1") is False
    assert requires_route_token(kind, None) is False


def test_an_event_lands_only_in_its_kinds_own_severity() -> None:
    assert admitted_event_kind("needs-human", "critical") == "needs-human"
    assert admitted_event_kind("attempt-failed", "warning") == "attempt-failed"
    assert admitted_event_kind("needs-human", "warning") is None
    assert admitted_event_kind("not-a-kind", "critical") is None


def test_a_subscription_is_identified_by_its_slug_and_named_after_it_by_default() -> None:
    assert subscription_identity(Payload({"slug": "max", "name": "Claude Max"})) == ("max", "Claude Max")
    assert subscription_identity(Payload({"slug": "max"})) == ("max", "max")
    assert subscription_identity(Payload({"slug": ""})) is None
    assert subscription_identity(Payload({"slug": 3})) is None
    assert subscription_identity(Payload({})) is None


def test_a_local_pause_is_stamped_when_the_runner_decided_and_attributed_to_the_operator() -> None:
    paused = LocalPause.of(RUNNER_LOCALLY_PAUSED, Payload({"at": _T0.isoformat(), "reason": "spend"}), now=_NOW)
    assert paused == LocalPause(paused=True, at=_T0, by="operator", reason="spend")
    resumed = LocalPause.of(RUNNER_LOCALLY_RESUMED, Payload({"at": "garbled", "by": "op-b"}), now=_NOW)
    assert resumed == LocalPause(paused=False, at=_NOW, by="op-b", reason=None)
