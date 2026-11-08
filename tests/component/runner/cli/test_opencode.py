"""``blizzard runner opencode compatibility``, ``drive`` and ``attend`` reach the OpenCode 2 service through the
carrier's transport: a fake handed in as ``obj`` is the one every command talks to, with no socket opened."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.children import RunnerCliCollaborators
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.opencode_v2.request import DriveKind, DriveRequest, write_request
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
