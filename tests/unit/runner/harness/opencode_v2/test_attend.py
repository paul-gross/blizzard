"""Handing an OpenCode 2 session to the operator against an in-memory service: the tree stopped before the rules are
replaced, the stamped model reasserted, a failed stop only warned, a failed rule swap or a missing or foreign session
refused, and the argv and model option that open it."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from blizzard.runner.harness.opencode_v2.attend import (
    AttendError,
    SessionHandover,
    attended_argv,
    model_option,
    parse_model_option,
)
from blizzard.runner.harness.opencode_v2.client import ModelReference
from blizzard.runner.harness.opencode_v2.driver import DriverCore
from blizzard.runner.harness.opencode_v2.request import FIXED_DENIES
from blizzard.runner.harness.opencode_v2.transport import ServiceRequest, ServiceResponse
from tests.support.opencode_v2_session_service import MODEL, REGISTRATION, SessionService, SessionTransport

ROOT = "ses_01JROOT0000000000000000000"
CHILD = "ses_01JCHILD000000000000000000"
_DENIES = [rule.payload() for rule in FIXED_DENIES]
_FLEET = {"blizzard": {"runner": "run_1", "lease": "lease_1"}}
LUNA = ModelReference("openai", "gpt-6-luna", "high")


@dataclass
class _RefusingPatch(SessionTransport):
    """Answers like the service, except every ``PATCH`` fails."""

    refused: list[str] = field(default_factory=list[str])

    def send(self, request: ServiceRequest) -> ServiceResponse:
        if request.method == "PATCH":
            self.refused.append(request.url)
            return ServiceResponse(500, b'{"error": "refused"}')
        return super().send(request)


def _service() -> SessionService:
    service = SessionService()
    service.add_session(ROOT, permissions=_DENIES, metadata=_FLEET)
    service.add_session(CHILD, parent=ROOT, permissions=_DENIES, metadata=_FLEET)
    service.running |= {ROOT, CHILD}
    return service


def _handover(transport: SessionTransport, warnings: list[str], *, stop_seconds: float = 1.0) -> SessionHandover:
    core = DriverCore(lambda: REGISTRATION, transport, poll_seconds=0.0)
    return SessionHandover(core, warn=warnings.append, stop_seconds=stop_seconds, poll_seconds=0.0)


def test_the_tree_is_stopped_and_the_rules_replaced_with_the_attended_set() -> None:
    service = _service()
    transport = SessionTransport(service)
    warnings: list[str] = []
    _handover(transport, warnings).prepare(ROOT)
    assert service.interrupted == [CHILD, ROOT]
    assert service.sessions[ROOT]["permissions"] == []
    assert service.sessions[CHILD]["permissions"] == _DENIES
    patch = transport.calls("PATCH", ROOT)
    assert len(patch) == 1 and json.loads(patch[0].body or b"{}") == {"permissions": []}
    order = [r.method for r in transport.requests if r.url.endswith(("/interrupt", ROOT)) and r.method != "GET"]
    assert order == ["POST", "POST", "PATCH"]
    assert service.switched == [] and warnings == []


def test_a_session_on_another_model_is_switched_back_to_the_stamped_one() -> None:
    service = _service()
    service.sessions[ROOT]["model"] = {"providerID": "openai", "id": "gpt-6-mini"}
    _handover(SessionTransport(service), []).prepare(ROOT, LUNA)
    assert service.switched == [("model", MODEL)]
    _handover(SessionTransport(service), []).prepare(ROOT, None)
    assert len(service.switched) == 1


def test_a_failed_interrupt_only_warns_and_the_rules_are_still_replaced() -> None:
    service = _service()
    service.failing["/interrupt"] = 500
    warnings: list[str] = []
    _handover(SessionTransport(service), warnings).prepare(ROOT)
    assert service.sessions[ROOT]["permissions"] == []
    assert len(warnings) == 1 and "could not stop" in warnings[0]


def test_a_tree_that_keeps_running_is_warned_of_and_handed_over() -> None:
    service = _service()
    service.failing["/interrupt"] = 200
    warnings: list[str] = []
    _handover(SessionTransport(service), warnings, stop_seconds=0.0).prepare(ROOT)
    assert service.sessions[ROOT]["permissions"] == []
    assert len(warnings) == 1 and "still runs" in warnings[0]


def test_rules_that_cannot_be_replaced_refuse_the_handover() -> None:
    service = _service()
    transport = _RefusingPatch(service)
    with pytest.raises(AttendError, match="attended set"):
        _handover(transport, []).prepare(ROOT)
    assert transport.refused and service.sessions[ROOT]["permissions"] == _DENIES


def test_a_session_the_service_does_not_hold_is_refused() -> None:
    service = _service()
    with pytest.raises(AttendError, match="no session ses_missing"):
        _handover(SessionTransport(service), []).prepare("ses_missing")
    assert service.interrupted == []


def test_a_session_no_blizzard_runner_minted_is_refused_untouched() -> None:
    service = _service()
    service.sessions[ROOT]["metadata"] = {}
    with pytest.raises(AttendError, match="not minted by a blizzard runner"):
        _handover(SessionTransport(service), []).prepare(ROOT)
    assert service.interrupted == [] and service.sessions[ROOT]["permissions"] == _DENIES


def test_an_unreachable_service_is_refused() -> None:
    transport = SessionTransport(_service())
    handover = _handover(transport, [])
    transport.service = None
    with pytest.raises(AttendError, match="cannot read session"):
        handover.prepare(ROOT)


def test_the_operators_opencode_opens_the_session_alone() -> None:
    assert attended_argv("opencode", ROOT) == ["opencode", "--session", ROOT]


def test_the_model_option_round_trips() -> None:
    assert model_option(LUNA) == "openai/gpt-6-luna#high"
    assert parse_model_option("openai/gpt-6-luna#high") == LUNA
    assert parse_model_option("openai/gpt-6-luna") == ModelReference("openai", "gpt-6-luna", None)
    with pytest.raises(ValueError, match="provider/model"):
        parse_model_option("luna")
