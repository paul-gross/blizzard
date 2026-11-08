"""``blizzard runner opencode compatibility``, ``drive`` and ``attend`` reach the OpenCode 2 service through the
carrier's transport: a fake handed in as ``obj`` is the one every command talks to, with no socket opened."""

from __future__ import annotations

import json
import signal
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from click.testing import CliRunner

from blizzard.foundation.clock import ManualMonotonicClock
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.children import RunnerCliCollaborators
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.opencode_v2.attend import DEFAULT_STOP_SECONDS
from blizzard.runner.harness.opencode_v2.drive import DEFAULT_GRACE_SECONDS
from blizzard.runner.harness.opencode_v2.request import METADATA_KEY, DriveKind, DriveRequest, write_request
from blizzard.runner.harness.opencode_v2.result import Outcome, parse_result
from blizzard.runner.harness.opencode_v2.transport import (
    IServiceTransport,
    ServiceRequest,
    ServiceResponse,
    ServiceUnreachableError,
    UrllibServiceTransport,
)
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import ChildRun
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses
from tests.support.opencode_v2_session_service import REGISTRATION, Record, SessionService, SessionTransport
from tests.support.worker_http import bind_local_only


class _RefusingService:
    """A service transport that records each request and refuses the connection."""

    def __init__(self) -> None:
        self.requests: list[ServiceRequest] = []

    def send(self, request: ServiceRequest) -> ServiceResponse:
        self.requests.append(request)
        raise ServiceUnreachableError("the fake service answers nobody")

    def stream(self, request: ServiceRequest) -> AbstractContextManager[Iterator[bytes]]:
        self.requests.append(request)
        raise ServiceUnreachableError("the fake service answers nobody")


class _VersionOnly(SubprocessChildProcesses):
    """Answers every ``run`` as a successful OpenCode 2.1.4, launching nothing."""

    def run(self, command: Any, *, cwd: Path | str | None, env: ChildEnv, timeout: float, **kwargs: Any) -> ChildRun:
        del command, cwd, env, timeout, kwargs
        return ChildRun(exit_code=0, stdout="2.1.4\n", stderr="", timed_out=False)


def _carrier(service: IServiceTransport, **replacements: Any) -> RunnerCliCollaborators:
    return replace(RunnerCliCollaborators.extending(bind_local_only().collaborators), service=service, **replacements)


def _register(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    path = home / ".local" / "state" / "opencode" / "service.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"id": "svc_1", "version": "2.1.4", "url": "http://127.0.0.1:9", "pid": 1, "password": "pw"})
    )


def test_the_production_carrier_holds_the_urllib_service_transport() -> None:
    carrier = RunnerCliCollaborators.extending(bind_local_only().collaborators)
    assert isinstance(carrier.service, UrllibServiceTransport)


def test_drive_talks_to_the_carriers_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register(tmp_path, monkeypatch)
    fake = _RefusingService()
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

    answer = CliRunner().invoke(runner_group, ["opencode", "drive", "--request", str(path)], obj=_carrier(fake))

    assert answer.exit_code == 0, answer.output
    result = parse_result(answer.output)
    assert result is not None and result.outcome is Outcome.FAILED
    assert len(fake.requests) == 1 and fake.requests[0].url.startswith("http://127.0.0.1:9")


def test_attend_talks_to_the_carriers_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register(tmp_path, monkeypatch)
    fake = _RefusingService()
    opened: list[object] = []

    def execvp(file: str, argv: list[str]) -> None:
        del file
        opened.append(argv)

    monkeypatch.setattr("os.execvp", execvp)

    answer = CliRunner().invoke(runner_group, ["opencode", "attend", "ses_1"], obj=_carrier(fake))

    assert answer.exit_code != 0
    assert "no OpenCode service holds the session" in answer.output
    assert len(fake.requests) == 1 and opened == []


def test_the_opencode_two_compatibility_proof_talks_to_the_carriers_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path, monkeypatch)
    fake = _RefusingService()
    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    evidence = tmp_path / "evidence"
    argv: Sequence[str] = [
        "opencode",
        "compatibility",
        "--binary",
        str(binary),
        "--evidence-dir",
        str(evidence),
        "--model",
        "p/m",
        "--variant",
        "high",
        "--allow-live-provider",
    ]

    answer = CliRunner().invoke(runner_group, list(argv), obj=_carrier(fake, children=_VersionOnly()))

    assert fake.requests, answer.output
    assert answer.exit_code == 1, answer.output


class _Reached(Exception):
    """Raised by :class:`_TrippingClock` the first time anything reads it."""


class _TrippingClock(ManualMonotonicClock):
    """A manual clock whose first reading proves the command handed it on, by ending the command there."""

    def monotonic(self) -> float:
        raise _Reached


@pytest.fixture
def sigint() -> Iterator[None]:
    """Restores the SIGINT handler ``drive`` installs."""
    previous = signal.getsignal(signal.SIGINT)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _register_service(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    path = home / ".local" / "state" / "opencode" / "service.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(asdict(REGISTRATION)))


class _RemovingTransport(SessionTransport):
    """Answers like the service, and removes a session on ``DELETE``, which the proof's cleanup asks for."""

    def send(self, request: ServiceRequest) -> ServiceResponse:
        if request.method == "DELETE" and self.service is not None:
            self.requests.append(request)
            self.service.sessions.pop(urlsplit(request.url).path.rsplit("/", 1)[1], None)
            return ServiceResponse(204, b"")
        return super().send(request)


def _keeps_running(service: SessionService, session_id: str, body: Record) -> None:
    del body
    service.running.add(session_id)


@pytest.mark.usefixtures("sigint")
def test_drive_measures_its_interrupt_grace_on_the_carriers_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_service(tmp_path, monkeypatch)
    service = SessionService(on_prompt=_keeps_running)
    service.failing["/interrupt"] = 200

    def interrupt_once_prompted(answering: SessionService) -> None:
        if answering.prompts and answering.running:
            answering.on_poll = None
            signal.raise_signal(signal.SIGINT)

    service.on_poll = interrupt_once_prompted
    clock = ManualMonotonicClock()
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

    answer = CliRunner().invoke(
        runner_group,
        ["opencode", "drive", "--request", str(path)],
        obj=_carrier(SessionTransport(service), monotonic_clock=clock),
    )

    assert answer.exit_code == 0, answer.output
    result = parse_result(answer.output)
    assert result is not None and result.outcome is Outcome.INTERRUPTED
    assert clock.delays and set(clock.delays) == {0.2}
    assert clock.reading >= DEFAULT_GRACE_SECONDS > clock.reading - clock.delays[-1]


def test_attend_measures_its_stop_grace_on_the_carriers_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register_service(tmp_path, monkeypatch)
    service = SessionService()
    service.add_session("ses_1", metadata={METADATA_KEY: {"runner": "run_1", "lease": "lease_1"}})
    service.running.add("ses_1")
    service.failing["/interrupt"] = 200
    opened: list[list[str]] = []

    def execvp(file: str, argv: list[str]) -> None:
        del file
        opened.append(argv)

    monkeypatch.setattr("os.execvp", execvp)
    clock = ManualMonotonicClock()

    answer = CliRunner().invoke(
        runner_group,
        ["opencode", "attend", "ses_1"],
        obj=_carrier(SessionTransport(service), monotonic_clock=clock),
    )

    assert answer.exit_code == 0, answer.output
    assert "still runs" in answer.output
    assert clock.delays and set(clock.delays) == {0.2}
    assert clock.reading >= DEFAULT_STOP_SECONDS > clock.reading - clock.delays[-1]
    assert service.sessions["ses_1"]["permissions"] == [] and len(opened) == 1


def test_the_opencode_two_compatibility_proofs_core_reads_the_carriers_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_service(tmp_path, monkeypatch)
    service = SessionService(on_prompt=_keeps_running)
    binary = tmp_path / "opencode"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    argv: Sequence[str] = [
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
    ]

    answer = CliRunner().invoke(
        runner_group,
        list(argv),
        obj=_carrier(_RemovingTransport(service), children=_VersionOnly(), monotonic_clock=_TrippingClock()),
    )

    assert isinstance(answer.exception, _Reached), answer.output
    assert service.prompts
