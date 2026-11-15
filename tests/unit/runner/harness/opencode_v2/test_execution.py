"""OpenCode 2 execution evidence over the recorded 2.2.6 answers: the lease's own tree in the active set is alive, with
its running tool, a pending request, or neither as the activity, and its newest completed step or call as progress;
an unreachable, restarted or impostor service, a malformed answer, and a running part in a session the service does
not report active are all unknown, and another lease's session never counts."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from blizzard.foundation.clock import ManualMonotonicClock
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.json_value import JsonValue
from blizzard.runner.harness.observation import ExecutionActivity, ExecutionLiveness, ExecutionObservation
from blizzard.runner.harness.opencode_v2.execution import PROGRESS_PAGE_LIMIT, ExecutionObserver
from blizzard.runner.harness.opencode_v2.registration import ServiceRegistration
from blizzard.runner.harness.opencode_v2.transport import ServiceRequest, ServiceResponse
from tests.support.opencode_v2_fakes import FakeService, FakeTransport

_RECORDED: dict[str, Any] = json.loads(
    (CommittedCorpus().root / "opencode" / "2.2.6" / "execution_observation.json").read_text()
)
ROOT: str = _RECORDED["root"]
CHILD: str = _RECORDED["child"]
OTHER = "ses_5a1e0c11affeOtherLeaseRoot01"
NOW = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)
REGISTRATION = ServiceRegistration(
    id="svc-226",
    version=_RECORDED["info"]["version"],
    url="http://127.0.0.1:49374",
    pid=_RECORDED["info"]["pid"],
    password="s3cret-password",
)


def _ms(stamp: int) -> datetime:
    return datetime.fromtimestamp(stamp / 1000, tz=UTC)


@dataclass
class _Recorded:
    """The recorded answers, served per session; a test edits them before it asks."""

    answers: dict[str, Any] = field(default_factory=lambda: copy.deepcopy(_RECORDED))
    active: str = "active"

    def service(self, *, pid: int | None = None) -> FakeService:
        service = FakeService(pid=REGISTRATION.pid if pid is None else pid, version=REGISTRATION.version)
        service.routes[("GET", "/api/session/active")] = lambda _: (200, self.answers[self.active])
        service.routes[("GET", "/api/session")] = self._children
        for sid in (ROOT, CHILD, OTHER):
            service.routes[("GET", f"/api/session/{sid}/message")] = self._per(sid, "messages_newest_first")
            service.routes[("GET", f"/api/session/{sid}/permission")] = self._per(sid, "permission_requests")
            service.routes[("GET", f"/api/session/{sid}/form")] = self._per(sid, "forms")
        return service

    def _children(self, request: ServiceRequest) -> tuple[int, JsonValue]:
        parent = parse_qs(urlsplit(request.url).query)["parentID"][0]
        return 200, self.answers["children"].get(parent, {"data": []})

    def _per(self, sid: str, key: str) -> Callable[[ServiceRequest], tuple[int, JsonValue]]:
        def answer(_: ServiceRequest) -> tuple[int, JsonValue]:
            return 200, self.answers[key].get(sid, {"data": []})

        return answer

    def tool_part(self, sid: str) -> dict[str, Any]:
        return self.answers["messages_newest_first"][sid]["data"][0]["content"][0]


def _observe(transport: FakeTransport, *, source: ServiceRegistration | None = REGISTRATION) -> ExecutionObservation:
    observer = ExecutionObserver(lambda: source, transport, monotonic_clock=ManualMonotonicClock())
    return observer.observe(ROOT, NOW)


def _paths(transport: FakeTransport) -> list[str]:
    return [urlsplit(request.url).path for request in transport.requests]


def test_a_running_child_tool_is_alive_tool_evidence_for_the_leases_root() -> None:
    recorded = _Recorded()
    transport = FakeTransport(recorded.service())
    observation = _observe(transport)
    assert observation.liveness is ExecutionLiveness.ALIVE
    assert observation.activity is ExecutionActivity.TOOL
    assert observation.evidence_at == NOW
    # The root's previous step is the newest completion across the tree; the running calls have none.
    assert observation.progress_at == _ms(1794500057160)
    assert "/api/info" in _paths(transport)


def test_a_root_idle_on_its_subagent_call_with_only_the_child_active_is_still_alive() -> None:
    recorded = _Recorded()
    recorded.answers["active"]["data"].pop(ROOT)
    observation = _observe(FakeTransport(recorded.service()))
    assert (observation.liveness, observation.activity) == (ExecutionLiveness.ALIVE, ExecutionActivity.TOOL)


def test_every_message_read_is_one_bounded_newest_first_page() -> None:
    transport = FakeTransport(_Recorded().service())
    _observe(transport)
    listings = [request.url for request in transport.requests if urlsplit(request.url).path.endswith("/message")]
    assert len(listings) == 2
    assert all(
        parse_qs(urlsplit(url).query) == {"order": ["desc"], "limit": [str(PROGRESS_PAGE_LIMIT)]} for url in listings
    )


def test_a_root_not_active_with_a_running_part_left_is_unknown_and_no_message_is_read() -> None:
    recorded = _Recorded(active="active_none")
    transport = FakeTransport(recorded.service())
    observation = _observe(transport)
    assert observation.liveness is ExecutionLiveness.UNKNOWN and observation.evidence_at is None
    assert recorded.tool_part(CHILD)["state"]["status"] == "running"
    assert not any(path.endswith("/message") for path in _paths(transport))


def test_another_leases_active_session_never_counts() -> None:
    recorded = _Recorded()
    recorded.answers["active"] = {"data": {OTHER: {"type": "running"}}}
    transport = FakeTransport(recorded.service())
    assert _observe(transport).liveness is ExecutionLiveness.UNKNOWN
    assert not any(OTHER in path for path in _paths(transport))


def test_another_leases_session_is_never_read_while_ours_runs() -> None:
    recorded = _Recorded()
    recorded.answers["active"]["data"][OTHER] = {"type": "running"}
    transport = FakeTransport(recorded.service())
    assert _observe(transport).liveness is ExecutionLiveness.ALIVE
    assert not any(OTHER in path for path in _paths(transport))


def test_progress_follows_a_completed_part() -> None:
    recorded = _Recorded()
    part = recorded.tool_part(CHILD)
    part["state"] = {**part["state"], "status": "completed", "content": [{"type": "text", "text": "CHECKED\n"}]}
    part["time"]["completed"] = 1794501023000
    observation = _observe(FakeTransport(recorded.service()))
    assert observation.progress_at == _ms(1794501023000)
    # The child's newest step no longer runs a tool, and the root's subagent call still does.
    assert observation.activity is ExecutionActivity.TOOL


def test_no_running_tool_in_any_active_sessions_newest_step_is_model() -> None:
    recorded = _Recorded()
    for sid in (ROOT, CHILD):
        recorded.tool_part(sid)["state"]["status"] = "completed"
    assert _observe(FakeTransport(recorded.service())).activity is ExecutionActivity.MODEL


@pytest.mark.parametrize("key", ["permission_requests", "forms"])
def test_a_pending_request_on_an_active_session_is_waiting(key: str) -> None:
    recorded = _Recorded()
    recorded.answers[key][CHILD] = {"data": [{"id": "per_1b52a1100001PendingRequest"}]}
    observation = _observe(FakeTransport(recorded.service()))
    assert (observation.liveness, observation.activity) == (ExecutionLiveness.ALIVE, ExecutionActivity.WAITING)


def test_an_unreachable_service_is_unknown() -> None:
    assert _observe(FakeTransport(None)).liveness is ExecutionLiveness.UNKNOWN


def test_no_registered_service_is_unknown() -> None:
    assert _observe(FakeTransport(_Recorded().service()), source=None).liveness is ExecutionLiveness.UNKNOWN


def test_a_restarted_service_answering_another_pid_is_unknown_without_reading_the_tree() -> None:
    transport = FakeTransport(_Recorded().service(pid=REGISTRATION.pid + 1))
    assert _observe(transport).liveness is ExecutionLiveness.UNKNOWN
    assert _paths(transport) == ["/api/info"]


def test_a_service_on_another_version_is_unknown() -> None:
    service = _Recorded().service()
    service.version = "2.2.7"
    assert _observe(FakeTransport(service)).liveness is ExecutionLiveness.UNKNOWN


def _malform(answers: dict[str, Any], which: str) -> None:
    pages = answers["messages_newest_first"]
    if which == "active-list":
        answers["active"] = {"data": [ROOT]}
    elif which == "page-not-list":
        pages[CHILD]["data"] = {"not": "a list"}
    elif which == "content-not-list":
        pages[CHILD]["data"][0]["content"] = "nope"
    elif which == "completed-not-int":
        pages[ROOT]["data"][1]["content"][0]["time"]["completed"] = "late"
    else:
        answers["children"][ROOT] = {"data": "nope"}


@pytest.mark.parametrize(
    "which", ["active-list", "page-not-list", "content-not-list", "completed-not-int", "children-not-list"]
)
def test_a_malformed_answer_is_unknown(which: str) -> None:
    recorded = _Recorded()
    _malform(recorded.answers, which)
    assert _observe(FakeTransport(recorded.service())).liveness is ExecutionLiveness.UNKNOWN


def test_a_failing_status_is_unknown() -> None:
    service = _Recorded().service()
    service.routes[("GET", f"/api/session/{CHILD}/message")] = lambda _: (500, {"error": "boom"})
    assert _observe(FakeTransport(service)).liveness is ExecutionLiveness.UNKNOWN


@dataclass
class _SlowTransport(FakeTransport):
    """Answers like the service, each answer taking a second on ``clock``."""

    clock: ManualMonotonicClock = field(default_factory=ManualMonotonicClock)

    def send(self, request: ServiceRequest) -> ServiceResponse:
        self.clock.advance(1.0)
        return super().send(request)


def test_the_whole_observation_is_held_to_one_bound() -> None:
    transport = _SlowTransport(_Recorded().service())
    observer = ExecutionObserver(lambda: REGISTRATION, transport, monotonic_clock=transport.clock, bound_seconds=3.0)
    assert observer.observe(ROOT, NOW).liveness is ExecutionLiveness.UNKNOWN
    assert max(request.timeout_seconds for request in transport.requests) <= 3.0
