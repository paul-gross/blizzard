"""Keeping the managed service up: an answering service is left alone, a missing one is started with
``opencode service start`` under the tool profile unless the operator has disabled it, one that never answers fails
within the bound, and the model listing is read from the answering service."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import pytest

from blizzard.runner.harness.opencode_v2.service import ServiceDisabledError, ServiceKeeper, ServiceUnavailableError
from blizzard.runner.harness.opencode_v2.shapes import ListedModel
from blizzard.runner.process.child_env import AllowlistedEnv, ChildEnv
from blizzard.runner.process.child_processes import ChildRun, ShellCommand
from tests.support.opencode_v2_session_service import REGISTRATION, SessionService, SessionTransport


@dataclass
class _Children:
    on_run: Callable[[], None] = lambda: None
    disabled: str = "false\n"
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
        if list(command)[1:] == ["service", "get", "disabled"]:
            return ChildRun(exit_code=0, stdout=self.disabled, stderr="", timed_out=False)
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
    assert [argv for argv, _ in children.runs] == [
        ["opencode", "service", "get", "disabled"],
        ["opencode", "service", "start"],
    ]
    assert not any(name.startswith("BLIZZARD_") for _, env in children.runs for name in env)


def test_a_service_that_never_answers_fails_within_the_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keeper, clock = _keeper(tmp_path, monkeypatch, _Children())
    with pytest.raises(ServiceUnavailableError):
        keeper.ensure()
    assert clock.now >= 5.0


def test_a_disabled_service_is_refused_by_name_and_never_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    children = _Children(on_run=lambda: _register(tmp_path), disabled="true\n")
    keeper, _ = _keeper(tmp_path, monkeypatch, children)
    assert keeper.disabled()
    with pytest.raises(ServiceDisabledError, match="disabled"):
        keeper.ensure()
    assert ["opencode", "service", "start"] not in [argv for argv, _ in children.runs]


@pytest.mark.parametrize(("answer", "disabled"), [("true\n", True), ("false\n", False), ("", None), ("yes\n", None)])
def test_the_setting_reads_true_false_or_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, disabled: bool | None
) -> None:
    keeper, _ = _keeper(tmp_path, monkeypatch, _Children(disabled=answer))
    assert keeper.disabled() is disabled


def test_an_unreadable_setting_starts_no_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    children = _Children(on_run=lambda: _register(tmp_path), disabled="Unknown service config key: disabled\n")
    keeper, _ = _keeper(tmp_path, monkeypatch, children)
    with pytest.raises(ServiceDisabledError, match="neither true nor false"):
        keeper.ensure()
    assert ["opencode", "service", "start"] not in [argv for argv, _ in children.runs]


def test_the_model_listing_is_read_from_the_answering_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _register(tmp_path)
    keeper, _ = _keeper(tmp_path, monkeypatch, _Children())
    assert keeper.listed_models() == (ListedModel("openai", "gpt-6-luna", ("low", "high")),)


def test_no_answering_service_lists_no_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keeper, _ = _keeper(tmp_path, monkeypatch, _Children())
    with pytest.raises(ServiceUnavailableError, match="model listing"):
        keeper.listed_models()
