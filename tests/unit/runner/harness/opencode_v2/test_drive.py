"""The OpenCode 2 turn driver against an in-memory service: minting and reopening a session, the environment applied
to the root alone and the rules to every child, permission and form replies, heartbeat from tool completions,
compaction at the lease's window, completion only once the whole tree settles, signal-to-interrupt, a service
restart, polling with the event stream lost, and the result it writes."""

from __future__ import annotations

import io
import json
import threading
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

import pytest

from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.opencode_v2.client import ModelReference
from blizzard.runner.harness.opencode_v2.drive import TurnDriver
from blizzard.runner.harness.opencode_v2.driver import DriverCore
from blizzard.runner.harness.opencode_v2.errors import ErrorKind
from blizzard.runner.harness.opencode_v2.registration import ServiceRegistration
from blizzard.runner.harness.opencode_v2.request import FIXED_DENIES, DriveKind, DriveRequest
from blizzard.runner.harness.opencode_v2.result import DriverResult, Outcome, parse_result
from tests.unit.runner.harness.opencode_v2.service_fake import (
    MODEL,
    REGISTRATION,
    Record,
    SessionService,
    SessionTransport,
    assistant,
    idle,
    tool,
)

ROOT = "ses_01JROOT0000000000000000000"
CHILD = "ses_01JCHILD000000000000000000"
LUNA = ModelReference("openai", "gpt-6-luna", "high")
ENVIRONMENT = {"BLIZZARD_LEASE_ID": "lease_1", "PATH": "/usr/bin"}


def _request(kind: DriveKind = DriveKind.SPAWN, **changes: Any) -> DriveRequest:
    base = DriveRequest(
        kind=kind,
        session_id=ROOT,
        directory="/work",
        prompt="do the thing",
        runner_id="run_1",
        lease_id="lease_1",
        autonomy=Autonomy.Normal,
        model=LUNA,
    )
    return replace(base, **changes)


class Harness:
    def __init__(self, service: SessionService, request: DriveRequest, *, listen: bool = False) -> None:
        self.registration: ServiceRegistration | None = REGISTRATION
        self.transport = SessionTransport(service)
        self.service = service
        self.out = io.StringIO()
        self.heartbeats = 0
        self.restarted: SessionService | None = None
        self.stop = threading.Event()
        core = DriverCore(lambda: self.registration, self.transport, poll_seconds=0.0)
        self.driver = TurnDriver(
            core,
            request,
            ENVIRONMENT,
            self.out,
            heartbeat=self._beat,
            stop=self.stop,
            poll_seconds=0.001,
            grace_seconds=0.5,
            listen=listen,
        )

    def _beat(self) -> None:
        self.heartbeats += 1

    def run(self) -> DriverResult:
        result = self.driver.run()
        parsed = parse_result(self.out.getvalue())
        assert parsed == result
        return result

    def records(self, kind: str) -> list[Record]:
        lines = [json.loads(line) for line in self.out.getvalue().splitlines()]
        return [line for line in lines if line["record"] == kind]


def _finishes(text: str = "done", *tools: Record) -> SessionService:
    def prompt(service: SessionService, session_id: str, body: Record) -> None:
        service.messages[session_id] += [
            {"type": "user", "id": "msg_u", "time": {"created": 1}, "text": str(body["text"])},
            assistant("msg_a", text, tools=tools),
            idle("msg_i"),
        ]

    return SessionService(on_prompt=prompt)


def test_a_spawn_mints_the_named_session_with_rules_model_metadata_and_environment() -> None:
    harness = Harness(_finishes("all done", tool("call_1")), _request())
    result = harness.run()
    created = json.loads(harness.transport.calls("POST", "/api/session")[0].body or b"{}")
    assert created["id"] == ROOT and created["location"] == {"directory": "/work"}
    assert created["model"] == MODEL
    assert created["permissions"] == [rule.payload() for rule in FIXED_DENIES]
    assert created["metadata"] == {"blizzard": {"runner": "run_1", "lease": "lease_1"}}
    assert harness.service.environments == {ROOT: ENVIRONMENT}
    assert harness.service.prompts == [(ROOT, {"text": "do the thing"})]
    assert result.outcome is Outcome.SUCCEEDED and result.text == "all done"
    assert [(s.session_id, s.model, s.cost) for s in result.steps] == [(ROOT, "openai/gpt-6-luna", 0.25)]
    assert harness.heartbeats == 1


def test_an_unknown_variant_is_refused_before_anything_is_created() -> None:
    harness = Harness(_finishes(), _request(model=replace(LUNA, variant="ultra")))
    result = harness.run()
    assert result.outcome is Outcome.FAILED
    assert result.error is not None and result.error.kind is ErrorKind.CONFIGURATION
    assert "ultra" in result.error.message
    assert not harness.transport.calls("POST", "/api/session") and not harness.service.prompts


def test_an_unknown_model_is_refused() -> None:
    result = Harness(_finishes(), _request(model=ModelReference("openai", "nope"))).run()
    assert result.error is not None and result.error.kind is ErrorKind.CONFIGURATION


def test_a_resume_of_a_session_another_runner_minted_is_missing_and_never_prompted() -> None:
    service = _finishes()
    service.add_session(ROOT, metadata={"blizzard": {"runner": "run_other"}})
    result = Harness(service, _request(DriveKind.RESUME)).run()
    assert result.error is not None and result.error.kind is ErrorKind.SESSION_MISSING
    assert not service.prompts


def test_a_resume_of_an_absent_session_is_missing() -> None:
    result = Harness(_finishes(), _request(DriveKind.NUDGE)).run()
    assert result.error is not None and result.error.kind is ErrorKind.SESSION_MISSING


def test_a_resume_reapplies_rules_and_environment_and_queues_its_prompt() -> None:
    service = _finishes()
    service.add_session(ROOT, metadata={"blizzard": {"runner": "run_1"}}, model=MODEL, permissions=[])
    harness = Harness(service, _request(DriveKind.NUDGE))
    assert harness.run().outcome is Outcome.SUCCEEDED
    assert service.sessions[ROOT]["permissions"] == [rule.payload() for rule in FIXED_DENIES]
    assert service.environments[ROOT] == ENVIRONMENT
    assert service.prompts[0][1]["delivery"] == "queue"
    assert not service.switched


def test_a_resume_after_a_takeover_restores_the_leases_model_and_agent() -> None:
    service = _finishes()
    operator_model: Record = {"providerID": "anthropic", "id": "claude-x"}
    service.add_session(ROOT, metadata={"blizzard": {"runner": "run_1"}}, model=operator_model, agent="plan")
    Harness(service, _request(DriveKind.RESUME, agent="build")).run()
    assert service.switched == [("model", MODEL), ("agent", "build")]


def test_only_this_turns_messages_count() -> None:
    service = _finishes("new answer")
    service.add_session(ROOT, metadata={"blizzard": {"runner": "run_1"}}, model=MODEL)
    service.messages[ROOT] = [assistant("msg_old", "old answer", tools=(tool("call_old"),)), idle("msg_old_idle")]
    harness = Harness(service, _request(DriveKind.RESUME))
    result = harness.run()
    assert result.text == "new answer"
    assert [step.message_id for step in result.steps] == ["msg_a"]
    assert harness.heartbeats == 0


@pytest.mark.parametrize(
    ("autonomy", "reply"), [(Autonomy.Normal, "reject"), (Autonomy.Auto, "once"), (Autonomy.Dangerous, "once")]
)
def test_a_permission_request_is_answered_per_autonomy_and_recorded(autonomy: Autonomy, reply: str) -> None:
    def prompt(service: SessionService, session_id: str, body: Record) -> None:
        service.running.add(session_id)
        service.permissions[session_id] = [
            {"id": "per_1", "sessionID": session_id, "action": "shell", "resources": ["touch x"]}
        ]

    def replied(service: SessionService, session_id: str, body: Record) -> None:
        service.running.discard(session_id)
        service.messages[session_id] += [assistant("msg_a", "ok"), idle("msg_i")]

    service = SessionService(on_prompt=prompt, on_reply=replied)
    harness = Harness(service, _request(autonomy=autonomy))
    result = harness.run()
    assert service.replies == [(ROOT, "per_1", reply)]
    assert [(d.surface, d.reply, d.action, d.resources) for d in result.decisions] == [
        ("permission", reply, "shell", ("touch x",))
    ]
    assert harness.records("decision")[0]["request"] == "per_1"


def test_a_form_is_cancelled_and_recorded() -> None:
    def prompt(service: SessionService, session_id: str, body: Record) -> None:
        service.forms[session_id] = [{"id": "frm_1", "sessionID": session_id, "title": "Pick one"}]
        service.messages[session_id] += [assistant("msg_a", "ok"), idle("msg_i")]

    result = Harness(SessionService(on_prompt=prompt), _request(autonomy=Autonomy.Dangerous)).run()
    assert [(d.surface, d.reply, d.request_id) for d in result.decisions] == [("form", "cancel", "frm_1")]


def test_a_child_gets_the_rules_never_the_environment_and_its_steps_count() -> None:
    def prompt(service: SessionService, session_id: str, body: Record) -> None:
        service.add_session(CHILD, parent=session_id, permissions=[])
        service.messages[CHILD] = [assistant("msg_c", "child", tools=(tool("call_c"),)), idle("msg_ci")]
        service.messages[session_id] += [assistant("msg_a", "root"), idle("msg_i")]

    service = SessionService(on_prompt=prompt)
    harness = Harness(service, _request())
    result = harness.run()
    assert service.sessions[CHILD]["permissions"] == [rule.payload() for rule in FIXED_DENIES]
    assert CHILD not in service.environments
    assert {(s.session_id, s.parent_id) for s in result.steps} == {(ROOT, None), (CHILD, ROOT)}
    assert result.text == "root" and harness.heartbeats == 1


def test_a_root_idle_before_its_background_child_settles_is_not_done() -> None:
    """The root idles at once; the child runs on, then wakes the root through its inbox, and only then is it done."""

    def prompt(service: SessionService, session_id: str, body: Record) -> None:
        service.add_session(CHILD, parent=session_id)
        service.running.add(CHILD)
        service.messages[session_id] += [assistant("msg_a", "STARTED"), idle("msg_i1")]

    def advance(service: SessionService) -> None:
        if service.polls == 4:
            service.running.discard(CHILD)
            service.messages[CHILD] = [assistant("msg_c", "BACKGROUND-DONE"), idle("msg_ci")]
            service.inbox[ROOT] = [{"id": "msg_inbox"}]
        if service.polls == 8:
            service.inbox[ROOT] = []
            service.messages[ROOT] += [assistant("msg_a2", "WOKEN"), idle("msg_i2")]

    service = SessionService(on_prompt=prompt, on_poll=advance)
    result = Harness(service, _request()).run()
    assert result.text == "WOKEN"
    assert service.polls >= 8


def test_compaction_is_requested_once_the_window_is_crossed() -> None:
    harness = Harness(_finishes(), _request(compaction_tokens=100))
    harness.run()
    assert harness.service.compactions == [ROOT]
    assert harness.records("compaction")[0]["context"] == 160


@pytest.mark.parametrize("window", [None, 10_000])
def test_no_compaction_under_auto_or_below_the_window(window: int | None) -> None:
    harness = Harness(_finishes(), _request(compaction_tokens=window))
    harness.run()
    assert harness.service.compactions == []


def test_a_signal_interrupts_descendants_then_the_root_and_ends_interrupted() -> None:
    def prompt(service: SessionService, session_id: str, body: Record) -> None:
        service.add_session(CHILD, parent=session_id)
        service.running |= {session_id, CHILD}

    service = SessionService(on_prompt=prompt)
    harness = Harness(service, _request())
    service.on_poll = lambda s: harness.stop.set() if s.polls == 2 else None
    result = harness.run()
    assert service.interrupted == [CHILD, ROOT]
    assert result.outcome is Outcome.INTERRUPTED


def test_a_service_restart_fails_the_turn_and_interrupts_what_the_new_service_resumed() -> None:
    def prompt(service: SessionService, session_id: str, body: Record) -> None:
        service.running.add(session_id)

    service = SessionService(on_prompt=prompt)
    harness = Harness(service, _request())

    def restart(old: SessionService) -> None:
        if old.polls == 2:
            new = SessionService(pid=200, sessions=old.sessions, messages=old.messages, running={ROOT})
            harness.transport.service = new
            harness.registration = replace(REGISTRATION, id="svc-2", pid=200)
            harness.restarted = new

    service.on_poll = restart
    result = harness.run()
    assert result.outcome is Outcome.FAILED
    assert result.error is not None and result.error.kind is ErrorKind.SERVICE
    assert harness.restarted is not None and harness.restarted.interrupted == [ROOT]


def test_a_lost_event_stream_leaves_polling_to_finish_the_turn() -> None:
    harness = Harness(_finishes("polled"), _request(), listen=True)
    assert harness.run().text == "polled"
    assert len(harness.transport.calls("GET", "/api/event")) >= 1


def test_a_failed_turn_carries_its_classified_error() -> None:
    limit: Record = {
        "type": "provider.free-limit",
        "message": "Free usage limit reached.",
        "status": 429,
        "retryAfterMs": 60000,
    }

    def prompt(service: SessionService, session_id: str, body: Record) -> None:
        service.messages[session_id] += [
            assistant("msg_a", finish="error", error=limit),
            idle("msg_i", "failed", limit),
        ]

    harness = Harness(SessionService(on_prompt=prompt), _request())
    result = harness.run()
    assert result.outcome is Outcome.FAILED
    assert result.error is not None and result.error.kind is ErrorKind.USAGE_LIMIT
    assert result.error.retry_after_ms == 60000
    assert harness.records("error")[0]["kind"] == "usage-limit"


def test_the_password_never_reaches_the_result() -> None:
    harness = Harness(_finishes(), _request())
    harness.run()
    assert REGISTRATION.password not in harness.out.getvalue()
    assert all(urlsplit(r.url).hostname == "127.0.0.1" for r in harness.transport.requests)
