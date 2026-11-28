"""Worker command scopes borrow the runner session and preserve operation-specific identity."""

from __future__ import annotations

import json
import sys
from collections.abc import Generator, Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from types import FrameType
from typing import Any, Protocol

import click
import httpx2
import pytest
from click.testing import CliRunner

from blizzard.foundation.clock import IMonotonicClock
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.runner.cli import runner
from blizzard.runner.cli.children import RunnerCliCollaborators
from blizzard.runner.cli.container_command import ContainerWorkerCommand
from blizzard.runner.cli.opencode_drive import DriveCommand
from blizzard.runner.cli.worker_call import WorkerCall, WorkerPolicy, WorkerSession
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.opencode.compatibility.loopback import LoopbackRequest, LoopbackResponse
from blizzard.runner.harness.opencode_v2.request import DriveKind, DriveRequest, write_request
from blizzard.runner.harness.opencode_v2.transport import (
    IServiceTransport,
    ServiceRequest,
    ServiceResponse,
    ServiceUnreachableError,
)
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import ChildRun
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses
from tests.support.worker_http import bind_stubs, bind_transport

_ENV = {
    "BLIZZARD_RUNNER_URL": "http://runner.test",
    "BLIZZARD_LEASE_ID": "lease_9",
    "BLIZZARD_LEASE_TOKEN": "token",
    "BLIZZARD_TRACEPARENT": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
}


class _Resource:
    marker = "resource"


class _ReadResource(Protocol):
    marker: str


class _WriteResource(Protocol):
    marker: str


def _answer_get(url: str, **kwargs: Any) -> httpx2.Response:
    return httpx2.Response(200, request=httpx2.Request("GET", url))


class _Launcher(SubprocessChildProcesses):
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.closed = False

    def run(self, command: Any, *, cwd: Path | str | None, env: ChildEnv, timeout: float, **kwargs: Any) -> ChildRun:
        self.calls.append(command)
        return ChildRun(exit_code=0, stdout="2.1.4\n", stderr="", timed_out=False)

    def close(self) -> None:
        self.closed = True


class _Loopback:
    def request(self, request: LoopbackRequest, *, timeout: float) -> AbstractContextManager[LoopbackResponse]:
        raise AssertionError("unexpected loopback request")


class _Service:
    def __init__(self) -> None:
        self.requests: list[ServiceRequest] = []
        self.closed = False

    def send(self, request: ServiceRequest) -> ServiceResponse:
        self.requests.append(request)
        return ServiceResponse(200, b"{}")

    def stream(self, request: ServiceRequest) -> AbstractContextManager[Iterator[bytes]]:
        raise AssertionError("unexpected service stream")

    def close(self) -> None:
        self.closed = True


@contextmanager
def _observe_production_defaults() -> Generator[list[str]]:
    """Observe provider and constructor calls, without replacing any production function."""
    watched = {
        ("cli/collaborators.py", "CliCollaborators.production"),
        ("cli/collaborators.py", "_short_lived_client"),
        ("process/wiring.py", "child_processes"),
        ("process/internal/subprocess_child_processes.py", "SubprocessChildProcesses.__init__"),
        ("opencode/compatibility/wiring.py", "loopback_transport"),
        ("opencode/compatibility/loopback.py", "UrllibLoopbackTransport.__init__"),
        ("opencode_v2/transport_wiring.py", "service_transport"),
        ("opencode_v2/transport.py", "UrllibServiceTransport.__init__"),
    }
    calls: list[str] = []
    previous = sys.getprofile()

    def observe(frame: FrameType, event: str, arg: object) -> None:
        if event == "call":
            code = frame.f_code
            for filename, name in watched:
                if code.co_filename.endswith(filename) and code.co_qualname == name:
                    calls.append(f"{filename}:{name}")

    sys.setprofile(observe)
    try:
        yield calls
    finally:
        sys.setprofile(previous)


class _RefusingService(_Service):
    def send(self, request: ServiceRequest) -> ServiceResponse:
        self.requests.append(request)
        raise ServiceUnreachableError("fake service")


def test_default_observer_detects_service_provider_and_constructor() -> None:
    from blizzard.runner.harness.opencode_v2.transport_wiring import service_transport

    with _observe_production_defaults() as calls:
        service_transport()

    assert "opencode_v2/transport_wiring.py:service_transport" in calls
    assert "opencode_v2/transport.py:UrllibServiceTransport.__init__" in calls


def test_drive_with_supplied_carrier_constructs_no_replaced_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound = bind_stubs()
    launcher, service = _Launcher(), _RefusingService()
    shared = bound.collaborators
    carrier = RunnerCliCollaborators(
        client_factory=shared.client_factory,
        clock=shared.clock,
        wall_clock=shared.wall_clock,
        monotonic_clock=shared.monotonic_clock,
        children=launcher,
        loopback=_Loopback(),
        service=service,
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    registration = tmp_path / ".local/state/opencode/service.json"
    registration.parent.mkdir(parents=True)
    registration.write_text(
        json.dumps({"id": "svc_1", "version": "2.1.4", "url": "http://127.0.0.1:9", "pid": 1, "password": "pw"})
    )
    request = DriveRequest(
        kind=DriveKind.SPAWN,
        session_id="ses_1",
        directory=str(tmp_path),
        prompt="p",
        runner_id="run_1",
        lease_id="lease_1",
        autonomy=Autonomy.Normal,
    )
    path = write_request(tmp_path / "requests", request)

    with _observe_production_defaults() as calls:
        result = CliRunner().invoke(runner, ["opencode", "drive", "--request", str(path)], obj=carrier, env=_ENV)

    assert result.exit_code == 0, result.output
    assert len(service.requests) == 1
    assert calls == []


@pytest.mark.parametrize("fails", [False, True])
def test_drive_scope_keeps_carrier_seams_by_identity_and_borrows_them(
    monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    bound = bind_stubs(get=_answer_get)
    launcher, loopback, service = _Launcher(), _Loopback(), _Service()
    shared = bound.collaborators
    carrier = RunnerCliCollaborators(
        client_factory=shared.client_factory,
        clock=shared.clock,
        wall_clock=shared.wall_clock,
        monotonic_clock=shared.monotonic_clock,
        children=launcher,
        loopback=loopback,
        service=service,
    )
    failure = RuntimeError("sentinel")

    @click.command(
        cls=DriveCommand,
        collaborators={"worker": WorkerCall, "service": IServiceTransport, "clock": IMonotonicClock},
        policy=WorkerPolicy("probe", traced=False),
    )
    @click.pass_context
    def probe(ctx: click.Context, worker: WorkerCall, service: IServiceTransport, clock: IMonotonicClock) -> None:
        chosen = RunnerCliCollaborators.of(ctx)
        assert chosen is carrier
        assert chosen.children is launcher and chosen.loopback is loopback
        assert service is carrier.service
        assert clock is carrier.monotonic_clock
        worker.get("/probe", failure="failed")
        service.send(ServiceRequest("GET", "http://127.0.0.1:9/probe"))
        if fails:
            raise failure

    monkeypatch.setitem(runner.commands, "probe", probe)
    result = CliRunner().invoke(runner, ["probe"], obj=carrier, env=_ENV)

    assert result.exception is failure if fails else result.exit_code == 0
    assert len(bound.built) == 1 and bound.built[0].closed
    assert [request.url for request in service.requests] == ["http://127.0.0.1:9/probe"]
    assert not service.closed and not launcher.closed


def test_compatibility_uses_explicit_carrier_launcher_and_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound = bind_stubs()
    launcher, service = _Launcher(), _Service()
    shared = bound.collaborators
    carrier = RunnerCliCollaborators(
        client_factory=shared.client_factory,
        clock=shared.clock,
        wall_clock=shared.wall_clock,
        monotonic_clock=shared.monotonic_clock,
        children=launcher,
        loopback=_Loopback(),
        service=service,
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    registration = tmp_path / ".local/state/opencode/service.json"
    registration.parent.mkdir(parents=True)
    registration.write_text(
        json.dumps({"id": "svc_1", "version": "2.1.4", "url": "http://127.0.0.1:9", "pid": 1, "password": "pw"})
    )
    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)

    result = CliRunner().invoke(
        runner,
        [
            "opencode",
            "compatibility",
            "--binary",
            str(binary),
            "--evidence-dir",
            str(tmp_path / "evidence"),
            "--model",
            "p/m",
            "--variant",
            "high",
            "--allow-live-provider",
        ],
        obj=carrier,
    )

    assert result.exit_code == 1, result.output
    assert launcher.calls
    assert service.requests
    assert not service.closed and not launcher.closed


def test_selected_scope_borrows_session_and_closes_owned_resource_before_group_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    bound = bind_stubs(get=_answer_get)

    def open_resource() -> Iterator[_Resource]:
        events.append("open")
        try:
            yield _Resource()
        finally:
            assert not bound.built[0].closed
            events.append("close")

    def area(registry: Registry) -> None:
        resource = registry.resource(open_resource, lifetime=Lifetime.COMMAND)
        registry.alias(port(_ReadResource), resource)
        registry.alias(port(_WriteResource), resource)

    @click.command(
        cls=ContainerWorkerCommand,
        collaborators={"worker": WorkerCall, "read": _ReadResource, "write": _WriteResource},
        policy=WorkerPolicy("probe", traced=False),
        area=area,
    )
    @click.pass_context
    def probe(ctx: click.Context, worker: WorkerCall, read: _ReadResource, write: _WriteResource) -> None:
        assert read is write
        assert worker.session is WorkerSession.from_context(ctx)
        worker.get("/probe", failure="failed")
        assert not bound.built[0].closed
        events.append("invoke")

    monkeypatch.setitem(runner.commands, "probe", probe)
    result = CliRunner().invoke(runner, ["probe"], obj=bound.collaborators, env=_ENV)

    assert result.exit_code == 0, result.output
    assert events == ["open", "invoke", "close"]
    assert len(bound.built) == 1 and bound.built[0].closed


def test_selected_scope_preserves_exception_and_cleans_up(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    failure = RuntimeError("sentinel")
    bound = bind_stubs(get=_answer_get)

    def open_resource() -> Iterator[_Resource]:
        try:
            yield _Resource()
        finally:
            assert not bound.built[0].closed
            events.append("closed")

    def area(registry: Registry) -> None:
        registry.resource(open_resource, lifetime=Lifetime.COMMAND)

    @click.command(
        cls=ContainerWorkerCommand,
        collaborators={"worker": WorkerCall, "resource": _Resource},
        policy=WorkerPolicy("probe", traced=False),
        area=area,
    )
    def probe(worker: WorkerCall, resource: _Resource) -> None:
        worker.get("/probe", failure="failed")
        raise failure

    monkeypatch.setitem(runner.commands, "probe", probe)
    result = CliRunner().invoke(runner, ["probe"], obj=bound.collaborators, env=_ENV)

    assert result.exception is failure
    assert events == ["closed"]
    assert len(bound.built) == 1 and bound.built[0].closed


@pytest.mark.parametrize(
    ("name", "span_name"),
    [("work-items", "runner work-items"), ("pm-items", "runner pm-items")],
)
def test_alias_uses_same_worker_identity_and_one_group_span(name: str, span_name: str) -> None:
    requests: list[httpx2.Request] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json=[] if request.url.path != "/v1/traces" else {})

    bound = bind_transport(answer)
    result = bound.runner.invoke(runner, [name, "ch_1"], env={**_ENV, "BLIZZARD_LEASE_ID": ""})

    assert result.exit_code == 0, result.output
    assert [r.url.path for r in requests] == ["/api/chunks/ch_1/work-items", "/v1/traces"]
    assert requests[0].headers["X-Blizzard-Lease-Token"] == "token"
    span = json.loads(requests[1].content)["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert span["name"] == span_name
    assert requests[0].headers["traceparent"].split("-")[2] == span["spanId"]
    assert len(bound.built) == 1 and bound.built[0].is_closed


@pytest.mark.parametrize(
    ("args", "exit_code", "message"),
    [
        (["ask", "question"], 1, "ask: no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL"),
        (["chunk", "history"], 1, "chunk history: no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL"),
        (["heartbeat"], 0, "heartbeat: no BLIZZARD_LEASE_ID/BLIZZARD_RUNNER_URL"),
    ],
)
def test_operation_policy_keeps_hard_and_soft_missing_identity_distinct(
    args: list[str], exit_code: int, message: str
) -> None:
    bound = bind_stubs()
    result = bound.runner.invoke(runner, args, env={**_ENV, "BLIZZARD_LEASE_ID": ""})
    assert result.exit_code == exit_code
    assert message in result.output
    assert bound.built == []
