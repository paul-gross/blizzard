"""`runner tick` with no host serving its directory opens one standalone host root, through the opener its
collaborators carry, and closes it whether the pass completes or raises."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Unpack

import pytest

from blizzard.cli.main import blizzard
from blizzard.foundation.registry.interface import ScopeClosed
from blizzard.runner.cli import runtime
from blizzard.runner.cli.runtime import InitCollaborators
from blizzard.runner.composition import HostRunnerSet, RunnerProcess
from blizzard.runner.host_config import BuiltRunner, HostConfig
from blizzard.runner.host_root import OpenedHost, StandaloneOptions, open_standalone
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.runner.settings_reader import RunnerSettingsReader
from tests.support.runner_init_fakes import FakeInitHub, init_runner
from tests.support.session_store_fakes import InMemorySessionStore


class _RecordingOpener:
    """The production standalone opener, keeping each host it opened to inspect after the command."""

    def __init__(self) -> None:
        self.opened: list[OpenedHost] = []

    def __call__(
        self, host_config: HostConfig, built: BuiltRunner, **options: Unpack[StandaloneOptions]
    ) -> RunnerProcess:
        opened = open_standalone(host_config, built, **options)
        self.opened.append(opened)
        (runner,) = opened.runners
        return replace(runner, closes=opened.close)


def _tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fails: str | None) -> tuple[int, _RecordingOpener]:
    hub = FakeInitHub()
    root = tmp_path / "runner"
    assert init_runner(hub).invoke(blizzard, ["runner", "init", str(root)]).exit_code == 0

    def tick_once(_wiring: LoopWiring, process: RunnerProcess) -> None:
        assert process.settings_reader is not None
        if fails == "pass":
            raise RuntimeError("the pass failed")

    monkeypatch.setattr(LoopWiring, "tick_once", tick_once)
    opener = _RecordingOpener()
    if fails == "reader":
        real = runtime.RunnerSettingsReader

        def reader(*args: Any, **kwargs: Any) -> RunnerSettingsReader:
            if any(args[2] is opened.runners[0].stores.settings for opened in opener.opened):
                raise RuntimeError("the settings reader failed")
            return real(*args, **kwargs)

        monkeypatch.setattr(runtime, "RunnerSettingsReader", reader)
    collaborators = InitCollaborators(
        hub.clients, hub.host_clients, InMemorySessionStore(), build_runner_process=opener
    )
    result = init_runner(hub).invoke(blizzard, ["runner", "tick", "--dir", str(root)], obj=collaborators)
    return result.exit_code, opener


@pytest.mark.parametrize("fails", [None, "pass", "reader"])
def test_tick_closes_the_host_root_it_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: str | None
) -> None:
    """On success, when the pass raises, and when the runner's settings reader fails to build."""
    code, opener = _tick(tmp_path, monkeypatch, fails=fails)

    assert code == (0 if fails is None else 1)
    (opened,) = opener.opened
    with pytest.raises(ScopeClosed):
        opened.root.owner.resolver.get(HostRunnerSet)
    (child,) = opened.children
    with pytest.raises(ScopeClosed):
        child.resolver.get(RunnerProcess)
    assert opened.runners[0].identity_client.is_closed
