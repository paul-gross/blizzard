"""Keeping the managed service up: an answering service is left alone, a missing one is started with
``opencode service start`` under the tool profile, and one that never answers fails within the bound."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import pytest

from blizzard.runner.harness.opencode_v2.service import ServiceKeeper, ServiceUnavailableError
from blizzard.runner.process.child_env import AllowlistedEnv, ChildEnv
from blizzard.runner.process.child_processes import ChildRun, ShellCommand
from tests.unit.runner.harness.opencode_v2.service_fake import REGISTRATION, SessionService, SessionTransport


@dataclass
class _Children:
    on_run: Callable[[], None] = lambda: None
    runs: list[tuple[list[str], dict[str, str]]] = field(default_factory=list[tuple[list[str], dict[str, str]]])

    def run(
        self,
        command: Sequence[str] | ShellCommand,
        *,
        cwd: Path | str | None,
        env: ChildEnv,
        timeout: float,
        input: str | None = None,
        input_settle: float = 0.0,
        stdout: IO[bytes] | None = None,
    ) -> ChildRun:
        assert not isinstance(command, ShellCommand)
        self.runs.append((list(command), env.variables))
        self.on_run()
        return ChildRun(exit_code=0, stdout="", stderr="", timed_out=False)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _register(home: Path) -> None:
    path = home / ".local" / "state" / "opencode" / "service.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'{{"id": "{REGISTRATION.id}", "version": "2.1.4", "url": "{REGISTRATION.url}", '
        f'"pid": {REGISTRATION.pid}, "password": "{REGISTRATION.password}"}}'
    )


def _keeper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, children: _Children) -> tuple[ServiceKeeper, _Clock]:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    clock = _Clock()
    keeper = ServiceKeeper(
        "opencode",
        AllowlistedEnv.of(()),
        children,  # pyright: ignore[reportArgumentType] — the keeper reaches only `run`
        SessionTransport(SessionService()),
        reach_seconds=5.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return keeper, clock


def test_an_answering_service_is_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register(tmp_path)
    children = _Children()
    keeper, _ = _keeper(tmp_path, monkeypatch, children)
    assert keeper.ensure().pid == REGISTRATION.pid
    assert children.runs == []


def test_a_missing_service_is_started_under_the_tool_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    children = _Children(on_run=lambda: _register(tmp_path))
    keeper, _ = _keeper(tmp_path, monkeypatch, children)
    assert keeper.ensure().registration_id == REGISTRATION.id
    argv, env = children.runs[0]
    assert argv == ["opencode", "service", "start"]
    assert not any(name.startswith("BLIZZARD_") for name in env)


def test_a_service_that_never_answers_fails_within_the_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keeper, clock = _keeper(tmp_path, monkeypatch, _Children())
    with pytest.raises(ServiceUnavailableError):
        keeper.ensure()
    assert clock.now >= 5.0
