"""Pure rules of the hub-bound outbound buffer — which kinds are submissions, the payload each
fact kind takes, the ack transition table, and the claim outcome's one-arm invariant. No store,
no clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.runner.hub.client import RouteClaimOutcome
from blizzard.runner.hub.outbound_buffer import (
    COMPLETION_KIND,
    DECISION_KIND,
    OUTBOUND_TRANSITIONS,
    BufferedFact,
    answer_delivered_payload,
    command_failed_event,
    escalation_payload,
    lease_minted_payload,
    question_asked_payload,
    submission_payload,
    transcript_truncated_event,
)
from blizzard.runner.leases import Lease
from blizzard.runner.leases.asks import OpenAsk
from blizzard.wire.route import RouteClaimConflict, RouteClaimResponse

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _lease(**overrides: object) -> Lease:
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 3,
        "runner_id": "r1",
        "retries_max": 2,
        "created_at": _T0,
        "pid": 100,
        "process_start_time": "start-100",
        "session_id": "sess-lease",
        "harness_id": "claude-code",
    }
    fields.update(overrides)
    return Lease(**fields)  # type: ignore[arg-type]


def _fact(kind: str) -> BufferedFact:
    return BufferedFact(seq=1, kind=kind, chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_T0)


def test_completion_and_decision_are_submissions() -> None:
    assert _fact(COMPLETION_KIND).is_submission is True
    assert _fact(DECISION_KIND).is_submission is True
    assert _fact("event.recorded").is_submission is False
    assert _fact(COMPLETION_KIND).is_completion is True
    assert _fact(DECISION_KIND).is_completion is False


def test_ack_transition_table() -> None:
    assert {"pending": frozenset({"ack"}), "acked": frozenset()} == OUTBOUND_TRANSITIONS


def test_lease_minted_payload() -> None:
    assert lease_minted_payload("ch_1", "lease_1", epoch=3, route_token="tok") == {
        "chunk_id": "ch_1",
        "epoch": 3,
        "lease_id": "lease_1",
        "route_token": "tok",
    }


def test_escalation_payload() -> None:
    payload = escalation_payload(
        _lease(),
        takeover="claude --resume s",
        wrapped_takeover="cd /w && claude --resume s",
        cause=next(iter(EscalationCause)),
        detail="stuck",
        route_token=None,
    )
    assert payload == {
        "chunk_id": "ch_1",
        "epoch": 3,
        "lease_id": "lease_1",
        "takeover_command": "claude --resume s",
        "wrapped_takeover_command": "cd /w && claude --resume s",
        "cause": str(next(iter(EscalationCause))),
        "detail": "stuck",
        "route_token": None,
    }


def _ask(*, session_id: str | None, harness_id: str | None) -> OpenAsk:
    return OpenAsk(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="q_1",
        question="which?",
        options=["a", "b"],
        session_id=session_id,
        asked_at=_T0,
        harness_id=harness_id,
    )


def test_question_asked_payload_falls_back_to_lease_session() -> None:
    payload = question_asked_payload(_lease(), _ask(session_id=None, harness_id=None), route_token="tok")
    assert payload == {
        "question_id": "q_1",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "session_id": "sess-lease",
        "harness_id": "claude-code",
        "epoch": 3,
        "lease_id": "lease_1",
        "question": "which?",
        "options": ["a", "b"],
        "asked_at": "2026-01-01T00:00:00+00:00",
        "route_token": "tok",
    }


def test_question_asked_payload_prefers_the_asks_own_session() -> None:
    payload = question_asked_payload(_lease(), _ask(session_id="sess-ask", harness_id="opencode"), route_token=None)
    assert (payload["session_id"], payload["harness_id"]) == ("sess-ask", "opencode")


def test_answer_delivered_payload() -> None:
    assert answer_delivered_payload(_lease(), "q_1") == {"chunk_id": "ch_1", "question_id": "q_1"}


def test_submission_payload_wraps_the_rendered_submission() -> None:
    assert submission_payload({"choice": "done"}) == {"submission": {"choice": "done"}}


def test_command_failed_payload_caps_stderr_tail() -> None:
    fields = command_failed_event(command="make", stderr_tail="x" * 1990 + "y" * 20)
    assert fields.kind == "command-failed"
    assert fields.message == "command failed: make"
    assert fields.detail == {"command": "make", "stderr_tail": "x" * 1980 + "y" * 20}


def test_command_failed_payload_with_no_stderr() -> None:
    assert command_failed_event(command="make", stderr_tail="").detail == {"command": "make", "stderr_tail": ""}


def test_transcript_truncated_payload_text() -> None:
    fields = transcript_truncated_event(segment_id="seg_1", reason="hub-capped")
    assert fields.kind == "transcript-truncated"
    assert fields.message == "transcript segment seg_1 truncated — hub-capped"
    assert fields.detail == {"segment_id": "seg_1", "reason": "hub-capped"}


def test_route_claim_outcome_requires_exactly_one() -> None:
    with pytest.raises(ValueError, match="exactly one arm, not 0"):
        RouteClaimOutcome()
    with pytest.raises(ValueError, match="exactly one arm, not 2"):
        RouteClaimOutcome(
            claimed=RouteClaimResponse.model_construct(),
            conflict=RouteClaimConflict.model_construct(),
        )
    assert RouteClaimOutcome(conflict=RouteClaimConflict.model_construct()).won is False
