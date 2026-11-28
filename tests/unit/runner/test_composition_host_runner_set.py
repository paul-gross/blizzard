"""``HostRunnerSet``'s runner children: each closed once, before its runner's wiring (unit tier)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import NoReturn, cast

import pytest

import blizzard.runner.composition as composition
from blizzard.foundation.registry.interface import Lifetime, ScopeClosed
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.runner.composition import (
    HostProcess,
    HostRunnerSet,
    IRunnerStart,
    LiveRunner,
    RunnerProcess,
    RunnerStartFailed,
    drain_runner,
    start_runner,
)
from blizzard.runner.host_config import BuiltRunner, HostConfig
from tests.support.host_runner_graph import (
    Borrowed,
    FakeResume,
    FakeRunner,
    FakeWiring,
    Journal,
    Rig,
    RunnerResource,
    RunnerService,
    built_runner,
    host_rig,
)


@dataclass
class _Fuse:
    """Whether :class:`_Bomb` fails to initialize."""

    armed: bool = True


class _Bomb:
    """What fails to initialize while its fuse is armed."""


def _with_bomb(fuse: _Fuse) -> Callable[[RecordingRegistry], None]:
    def open_bomb() -> _Bomb:
        if fuse.armed:
            raise RuntimeError("initialization failed")
        return _Bomb()

    def register(registry: RecordingRegistry) -> None:
        registry.factory(open_bomb, lifetime=Lifetime.RUNNER)

    return register


class _Start:
    """The start seams a closure path reaches."""

    def __init__(self, journal: Journal) -> None:
        self.journal = journal

    def resume(self, runner: RunnerProcess) -> FakeResume:
        return FakeResume()

    def withdraw(self, runner_id: str) -> None:
        self.journal.note(f"withdraw:{runner_id}")


class _RefusingResume(_Start):
    def resume(self, runner: RunnerProcess) -> NoReturn:
        raise ValueError("no resume")


def _seams(start: _Start) -> IRunnerStart[FakeWiring, FakeResume]:
    return cast("IRunnerStart[FakeWiring, FakeResume]", start)


class _Drivers:
    def __init__(self, journal: Journal, runner_id: str) -> None:
        self.journal = journal
        self.runner_id = runner_id

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self.journal.note(f"drivers:{self.runner_id}")


def _compose_with(
    monkeypatch: pytest.MonkeyPatch, runner_for: Callable[[str], object], then: Callable[[], None] = lambda: None
) -> None:
    """Compose each built runner as ``runner_for`` its id, running ``then`` first."""

    def compose(host_config: HostConfig, host: HostProcess, built: BuiltRunner, **kwargs: object) -> object:
        then()
        return runner_for(built.id)

    monkeypatch.setattr(composition, "compose_runner", compose)


def _compose_fake(journal: Journal, monkeypatch: pytest.MonkeyPatch) -> None:
    _compose_with(monkeypatch, lambda runner_id: FakeRunner(runner_id, journal))


def _no_startup_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_steps(live: LiveRunner[FakeWiring, FakeResume], start: IRunnerStart[FakeWiring, FakeResume]) -> None:
        return None

    monkeypatch.setattr(composition, "run_startup_steps", no_steps)


def _start(rig: Rig, start: _Start) -> None:
    start_runner(built_runner("rn_1"), _seams(start), rig.root.runners)


def test_a_drain_closes_the_child_then_the_wiring_once() -> None:
    rig = host_rig()
    rig.place("rn_1", _Drivers(rig.journal, "rn_1"))
    dropped: list[str] = []
    host = SimpleNamespace(host_stores=SimpleNamespace(hub_tokens=SimpleNamespace(drop=dropped.append)))

    assert drain_runner(cast("HostProcess", host), rig.root.runners, _seams(_Start(rig.journal)), "rn_1")
    rig.root.close()

    assert rig.journal.of("rn_1") == ["withdraw:rn_1", "drivers:rn_1", "graph:rn_1", "child:rn_1", "wiring:rn_1"]


def test_an_abandon_closes_the_child_then_the_wiring_once() -> None:
    rig = host_rig()
    rig.place("rn_1", _Drivers(rig.journal, "rn_1"))

    rig.root.runners.abandon("rn_1", _seams(_Start(rig.journal)))
    rig.root.close()

    assert rig.journal.of("rn_1") == ["withdraw:rn_1", "drivers:rn_1", "graph:rn_1", "child:rn_1", "wiring:rn_1"]
    assert rig.root.runners.get("rn_1") is None


def test_an_open_after_close_all_is_refused() -> None:
    rig = host_rig()
    rig.root.runners.close_all()

    with pytest.raises(RuntimeError, match="closed"):
        rig.root.runners.open_runner(built_runner("rn_1"))
    rig.root.close()


def test_a_set_bound_to_no_parent_scope_opens_no_child() -> None:
    runners = HostRunnerSet[FakeWiring, FakeResume](cast("HostProcess", Borrowed(Journal())))

    assert runners.open_runner(built_runner("rn_1")) is None


def test_a_child_whose_eager_types_fail_is_closed_and_the_next_attempt_opens_a_new_one() -> None:
    fuse = _Fuse()
    rig = host_rig(eager=(RunnerResource, _Bomb), extra=_with_bomb(fuse))

    with pytest.raises(RuntimeError, match="initialization failed"):
        rig.root.runners.open_runner(built_runner("rn_1"))
    assert rig.journal.of("rn_1") == ["child:rn_1"]
    assert rig.root.runners.live() == ()

    fuse.armed = False
    child = rig.open("rn_1")

    child.resolver.get(RunnerService)
    assert rig.journal.of("rn_1") == ["child:rn_1", "built:rn_1"]
    rig.root.close()


def test_a_start_whose_eager_types_fail_fails_compose_and_places_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = host_rig(eager=(RunnerResource, _Bomb), extra=_with_bomb(_Fuse()))
    _compose_fake(rig.journal, monkeypatch)

    with pytest.raises(RunnerStartFailed) as failed:
        _start(rig, _Start(rig.journal))

    assert failed.value.step == "compose"
    assert rig.root.runners.live() == ()
    assert rig.journal.of("rn_1") == ["child:rn_1"]
    rig.root.close()


def test_a_failed_composition_closes_the_child_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = host_rig()

    def refuse() -> None:
        raise RuntimeError("bad entry")

    _compose_with(monkeypatch, lambda runner_id: None, then=refuse)

    with pytest.raises(RunnerStartFailed) as failed:
        _start(rig, _Start(rig.journal))

    assert (failed.value.step, failed.value.error) == ("compose", "bad entry")
    assert rig.journal.of("rn_1") == ["child:rn_1"]
    rig.root.close()


def test_a_start_failing_before_placement_closes_the_graph_then_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = host_rig()
    _compose_fake(rig.journal, monkeypatch)

    with pytest.raises(ValueError, match="no resume"):
        _start(rig, _RefusingResume(rig.journal))
    rig.root.close()

    assert rig.journal.of("rn_1") == ["graph:rn_1", "child:rn_1"]


def test_a_start_that_loses_to_close_all_is_refused_at_placement_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = host_rig()
    _compose_with(monkeypatch, lambda runner_id: FakeRunner(runner_id, rig.journal), then=rig.root.runners.close_all)
    _no_startup_steps(monkeypatch)

    with pytest.raises(RunnerStartFailed) as failed:
        _start(rig, _Start(rig.journal))

    assert failed.value.step == "serve"
    assert rig.root.runners.live() == ()
    assert rig.journal.of("rn_1") == ["graph:rn_1", "child:rn_1"]
    rig.root.close()


def test_a_cleanup_failure_does_not_mask_the_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = host_rig()

    def close_runner() -> None:
        raise OSError("close refused")

    _compose_with(monkeypatch, lambda runner_id: SimpleNamespace(runner_id=runner_id, close_runner=close_runner))

    with pytest.raises(ValueError, match="no resume") as failed:
        _start(rig, _RefusingResume(rig.journal))

    assert "close refused" in "".join(failed.value.__notes__)
    assert rig.journal.of("rn_1") == ["child:rn_1"]
    rig.root.close()


def test_a_started_runner_holds_its_child_and_a_closed_one_refuses_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = host_rig()
    _compose_fake(rig.journal, monkeypatch)
    _no_startup_steps(monkeypatch)

    _start(rig, _Start(rig.journal))
    live = rig.root.runners.get("rn_1")
    assert live is not None and live.child is not None
    assert live.child.owner == "rn_1"

    rig.root.close()

    with pytest.raises(ScopeClosed):
        live.child.resolver.get(RunnerService)
