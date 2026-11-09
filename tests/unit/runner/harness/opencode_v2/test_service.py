"""Keeping the managed service up: an answering service is left alone, a missing one is started with
``opencode service start`` under the tool profile unless the operator has disabled it, one that never answers fails
within the bound, and the model listing is read from the answering service."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import pytest

from blizzard.foundation.clock import ManualMonotonicClock
from blizzard.runner.harness.opencode_v2.declaration import snapshot_registration
from blizzard.runner.harness.opencode_v2.service import (
    UNLOCATED_DETAIL,
    ServiceDisabledError,
    ServiceKeeper,
    ServiceUnavailableError,
)
from blizzard.runner.harness.opencode_v2.shapes import ListedModel
from blizzard.runner.harness.opencode_v2.transport import (
    IServiceTransport,
    ServiceRequest,
    ServiceResponse,
    ServiceUnreachableError,
)
from blizzard.runner.process.child_env import ChildEnv
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


def _register(home: Path, *, state: Path | None = None, registration_id: str = REGISTRATION.id) -> None:
    path = (state or home / ".local" / "state") / "opencode" / "service.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'{{"id": "{registration_id}", "version": "2.1.4", "url": "{REGISTRATION.url}", '
        f'"pid": {REGISTRATION.pid}, "password": "{REGISTRATION.password}"}}'
    )


def _keeper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    children: _Children,
    *,
    tool_env: ChildEnv | None = None,
    transport: IServiceTransport | None = None,
) -> tuple[ServiceKeeper, ManualMonotonicClock]:
    # The snapshot alone locates the registration: the daemon's own environment points nowhere useful.
    monkeypatch.setenv("HOME", str(tmp_path / "daemon-home"))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    env = tool_env if tool_env is not None else ChildEnv((("HOME", str(tmp_path)),))
    clock = ManualMonotonicClock()
    keeper = ServiceKeeper(
        "opencode",
        env,
        children,  # pyright: ignore[reportArgumentType] — the keeper reaches only `run`
        transport if transport is not None else SessionTransport(SessionService()),
        source=snapshot_registration(env),
        monotonic_clock=clock,
        reach_seconds=5.0,
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
    with pytest.raises(ServiceUnavailableError, match="within 5s"):
        keeper.ensure()
    assert clock.reading >= 5.0
    assert clock.delays and set(clock.delays) == {0.2}
    assert sum(clock.delays) == pytest.approx(clock.reading)


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


def test_the_children_run_under_the_snapshot_handed_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = ChildEnv((("HOME", str(tmp_path)), ("PATH", "/snapshot/bin")))
    children = _Children(on_run=lambda: _register(tmp_path))
    keeper, _ = _keeper(tmp_path, monkeypatch, children, tool_env=env)
    monkeypatch.setenv("PATH", "/changed/bin")
    keeper.ensure()
    assert {variables["PATH"] for _, variables in children.runs} == {"/snapshot/bin"}


def test_xdg_state_home_locates_the_registration_before_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = tmp_path / "state"
    _register(tmp_path, state=state, registration_id="ses_xdg")
    _register(tmp_path, registration_id="ses_home")
    env = ChildEnv((("HOME", str(tmp_path)), ("XDG_STATE_HOME", str(state))))
    keeper, _ = _keeper(tmp_path, monkeypatch, _Children(), tool_env=env)
    answering = keeper.answering()
    assert answering is not None
    assert answering.registration_id == "ses_xdg"


def test_a_snapshot_locating_no_registration_is_unavailable_and_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    children = _Children(on_run=lambda: _register(tmp_path))
    keeper, _ = _keeper(tmp_path, monkeypatch, children, tool_env=ChildEnv(()))
    for call in (keeper.answering, keeper.ensure, keeper.listed_models):
        with pytest.raises(ServiceUnavailableError) as raised:
            call()
        assert str(raised.value) == UNLOCATED_DETAIL
        assert not isinstance(raised.value, ServiceDisabledError)
    assert children.runs == []


def test_the_registration_is_reread_on_every_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keeper, _ = _keeper(tmp_path, monkeypatch, _Children())
    assert keeper.answering() is None
    _register(tmp_path, registration_id="ses_first")
    first = keeper.answering()
    _register(tmp_path, registration_id="ses_second")
    second = keeper.answering()
    assert first is not None and first.registration_id == "ses_first"
    assert second is not None and second.registration_id == "ses_second"


class _Answering:
    """A transport whose service stays unreachable for ``silent`` requests, then answers as the session service."""

    def __init__(self, silent: int) -> None:
        self._inner = SessionTransport(SessionService())
        self._silent = silent
        self.sent = 0

    def send(self, request: ServiceRequest) -> ServiceResponse:
        self.sent += 1
        if self.sent <= self._silent:
            raise ServiceUnreachableError("not yet")
        return self._inner.send(request)

    def stream(self, request: ServiceRequest) -> AbstractContextManager[Iterator[bytes]]:
        raise AssertionError("the keeper never streams")


def test_readiness_polls_on_the_clock_until_the_service_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(tmp_path)
    transport = _Answering(silent=4)
    children = _Children()
    keeper, clock = _keeper(tmp_path, monkeypatch, children, transport=transport)
    assert keeper.ensure().registration_id == REGISTRATION.id
    assert ["opencode", "service", "start"] in [argv for argv, _ in children.runs]
    # One unanswered probe before the start, then three polls a poll interval apart.
    assert clock.delays == [0.2, 0.2, 0.2]
    assert clock.reading == pytest.approx(0.6)
