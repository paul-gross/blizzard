"""OpenCode 2's orphan against an in-memory service: the running tree interrupted deepest first, then the root; a tree
running nothing left alone; the whole call held to one bound; and no service, an unknown session, or a failed
interrupt logged and ignored."""

from __future__ import annotations

from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from structlog.testing import capture_logs

from blizzard.foundation.clock import ManualMonotonicClock
from blizzard.runner.harness.opencode_v2.orphan import SessionOrphaner
from blizzard.runner.harness.opencode_v2.registration import ServiceRegistration
from blizzard.runner.harness.opencode_v2.transport import ServiceRequest, ServiceResponse
from tests.support.opencode_v2_session_service import REGISTRATION, SessionService, SessionTransport

ROOT = "ses_01JROOT0000000000000000000"
CHILD = "ses_01JCHILD000000000000000000"
GRANDCHILD = "ses_01JGRAND000000000000000000"


def _service() -> SessionService:
    service = SessionService()
    service.add_session(ROOT)
    service.add_session(CHILD, parent=ROOT)
    service.add_session(GRANDCHILD, parent=CHILD)
    service.running |= {ROOT, CHILD, GRANDCHILD}
    return service


def _warnings(logs: Sequence[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [entry for entry in logs if entry["event"] == "could not interrupt an orphaned OpenCode 2 session"]


def test_the_running_tree_is_interrupted_deepest_first_then_the_root() -> None:
    service = _service()
    transport = SessionTransport(service)
    assert SessionOrphaner(lambda: REGISTRATION, transport, monotonic_clock=ManualMonotonicClock()).orphan(ROOT) is True
    assert service.interrupted == [GRANDCHILD, CHILD, ROOT]
    assert service.running == set()


def test_a_tree_running_nothing_is_left_alone() -> None:
    service = _service()
    service.running.clear()
    assert (
        SessionOrphaner(lambda: REGISTRATION, SessionTransport(service), monotonic_clock=ManualMonotonicClock()).orphan(
            ROOT
        )
        is False
    )
    assert service.interrupted == []


@dataclass
class _SlowTransport(SessionTransport):
    """Answers like the service, each answer taking half a second on ``clock``."""

    clock: ManualMonotonicClock = field(default_factory=ManualMonotonicClock)

    def send(self, request: ServiceRequest) -> ServiceResponse:
        self.clock.advance(0.5)
        return super().send(request)


def test_every_request_is_held_to_what_is_left_of_one_bound() -> None:
    transport = _SlowTransport(_service())
    with capture_logs() as logs:
        SessionOrphaner(lambda: REGISTRATION, transport, monotonic_clock=transport.clock, bound_seconds=2.0).orphan(
            ROOT
        )
    timeouts = [request.timeout_seconds for request in transport.requests]
    assert timeouts and timeouts == sorted(timeouts, reverse=True) and max(timeouts) <= 2.0
    assert len(_warnings(logs)) == 1  # the bound ran out before the tree was walked


def test_no_registered_service_is_logged_and_ignored() -> None:
    registration: ServiceRegistration | None = None
    with capture_logs() as logs:
        SessionOrphaner(
            lambda: registration, SessionTransport(_service()), monotonic_clock=ManualMonotonicClock()
        ).orphan(ROOT)
    assert len(_warnings(logs)) == 1


def test_an_unreachable_service_is_logged_and_ignored() -> None:
    with capture_logs() as logs:
        assert (
            SessionOrphaner(
                lambda: REGISTRATION, SessionTransport(None), monotonic_clock=ManualMonotonicClock()
            ).orphan(ROOT)
            is False
        )
    assert len(_warnings(logs)) == 1


def test_a_session_the_service_does_not_hold_is_logged_and_ignored() -> None:
    service = _service()
    with capture_logs() as logs:
        SessionOrphaner(lambda: REGISTRATION, SessionTransport(service), monotonic_clock=ManualMonotonicClock()).orphan(
            "ses_missing"
        )
    assert service.interrupted == [] and len(_warnings(logs)) == 1


def test_a_failed_interrupt_is_logged_and_ignored() -> None:
    service = _service()
    service.failing["/interrupt"] = 500
    with capture_logs() as logs:
        SessionOrphaner(lambda: REGISTRATION, SessionTransport(service), monotonic_clock=ManualMonotonicClock()).orphan(
            ROOT
        )
    assert len(_warnings(logs)) == 1
