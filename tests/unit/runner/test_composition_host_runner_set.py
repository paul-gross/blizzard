"""``HostRunnerSet``'s runner children: opened by it alone, and closed before each runner's wiring (unit tier)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

import blizzard.runner.composition as composition
from blizzard.foundation.registry.interface import Lifetime, ScopeClosed
from blizzard.runner.composition import HostRunnerSet, RunnerStartFailed, drain_runner, start_runner
from tests.support.host_runner_graph import (
    Borrowed,
    FakeResume,
    FakeRunner,
    Journal,
    Rig,
    RunnerResource,
    RunnerService,
    built_runner,
    host_rig,
)


class _Bomb:
    """What fails to initialize while ``armed``."""

    armed = True


def _open_bomb() -> _Bomb:
    if _Bomb.armed:
        raise RuntimeError("initialization failed")
    return _Bomb()


def _with_bomb(registry: Any) -> None:
    registry.factory(_open_bomb, lifetime=Lifetime.RUNNER)


class _Start:
    """The start seams a closure path reaches."""

    def __init__(self, journal: Journal, resume: Any = None) -> None:
        self.journal = journal
        self._resume = resume or FakeResume()

    def resume(self, runner: Any) -> Any:
        return self._resume

    def withdraw(self, runner_id: str) -> None:
        self.journal.note(f"withdraw:{runner_id}")


class _Drivers:
    def __init__(self, journal: Journal, runner_id: str) -> None:
        self.journal = journal
        self.runner_id = runner_id

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self.journal.note(f"drivers:{self.runner_id}")


def _compose_fake(journal: Journal, monkeypatch: pytest.MonkeyPatch) -> None:
    def compose(host_config: Any, host: Any, built: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            runner_id=built.id,
            close_runner=FakeRunner(built.id, journal).close_runner,
        )

    monkeypatch.setattr(composition, "compose_runner", compose)


def _start(rig: Rig, start: Any) -> None:
    start_runner(cast(Any, object()), cast(Any, rig.host), built_runner("rn_1"), start, rig.root.runners)


def test_a_drain_closes_the_child_then_the_wiring_once() -> None:
    rig = host_rig()
    rig.place("rn_1", _Drivers(rig.journal, "rn_1"))
    dropped: list[str] = []
    host = SimpleNamespace(host_stores=SimpleNamespace(hub_tokens=SimpleNamespace(drop=dropped.append)))

    assert drain_runner(cast(Any, host), rig.root.runners, cast(Any, _Start(rig.journal)), "rn_1")
    rig.root.close()

    assert rig.journal.of("rn_1") == ["withdraw:rn_1", "drivers:rn_1", "graph:rn_1", "child:rn_1", "wiring:rn_1"]


def test_an_abandon_closes_the_child_then_the_wiring_once() -> None:
    rig = host_rig()
    rig.place("rn_1", _Drivers(rig.journal, "rn_1"))

    rig.root.runners.abandon("rn_1", cast(Any, _Start(rig.journal)))
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
    runners = HostRunnerSet[Any, Any](cast(Any, Borrowed(Journal())))

    assert runners.open_runner(built_runner("rn_1")) is None


def test_a_child_whose_eager_types_fail_is_closed_and_the_next_attempt_opens_a_new_one() -> None:
    rig = host_rig(eager=(RunnerResource, _Bomb), extra=_with_bomb)
    _Bomb.armed = True

    with pytest.raises(RuntimeError, match="initialization failed"):
        rig.root.runners.open_runner(built_runner("rn_1"))
    assert rig.journal.of("rn_1") == ["child:rn_1"]
    assert rig.root.runners.live() == ()

    _Bomb.armed = False
    child = rig.open("rn_1")

    child.resolver.get(RunnerService)
    assert rig.journal.of("rn_1") == ["child:rn_1", "built:rn_1"]
    rig.root.close()


def test_a_start_whose_eager_types_fail_fails_compose_and_places_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = host_rig(eager=(RunnerResource, _Bomb), extra=_with_bomb)
    _compose_fake(rig.journal, monkeypatch)
    _Bomb.armed = True

    with pytest.raises(RunnerStartFailed) as failed:
        _start(rig, _Start(rig.journal))

    assert failed.value.step == "compose"
    assert rig.root.runners.live() == ()
    assert rig.journal.of("rn_1") == ["child:rn_1"]
    rig.root.close()


def test_a_failed_composition_closes_the_child_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = host_rig()

    def compose(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("bad entry")

    monkeypatch.setattr(composition, "compose_runner", compose)

    with pytest.raises(RunnerStartFailed) as failed:
        _start(rig, _Start(rig.journal))

    assert (failed.value.step, failed.value.error) == ("compose", "bad entry")
    assert rig.journal.of("rn_1") == ["child:rn_1"]
    rig.root.close()


def test_a_start_failing_before_placement_closes_the_graph_then_the_child(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = host_rig()
    _compose_fake(rig.journal, monkeypatch)

    def resume(runner: Any) -> Any:
        raise ValueError("no resume")

    start = _Start(rig.journal)
    monkeypatch.setattr(start, "resume", resume)

    with pytest.raises(ValueError, match="no resume"):
        _start(rig, start)
    rig.root.close()

    assert rig.journal.of("rn_1") == ["graph:rn_1", "child:rn_1"]


def test_a_cleanup_failure_does_not_mask_the_start_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = host_rig()

    def compose(host_config: Any, host: Any, built: Any, **kwargs: Any) -> Any:
        def close_runner() -> None:
            raise OSError("close refused")

        return SimpleNamespace(runner_id=built.id, close_runner=close_runner)

    def resume(runner: Any) -> Any:
        raise ValueError("no resume")

    monkeypatch.setattr(composition, "compose_runner", compose)
    start = _Start(rig.journal)
    monkeypatch.setattr(start, "resume", resume)

    with pytest.raises(ValueError, match="no resume") as failed:
        _start(rig, start)

    assert "close refused" in "".join(failed.value.__notes__)
    assert rig.journal.of("rn_1") == ["child:rn_1"]
    rig.root.close()


def test_a_started_runner_holds_its_child_and_a_closed_one_refuses_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = host_rig()
    _compose_fake(rig.journal, monkeypatch)

    def no_steps(live: Any, start: Any) -> None:
        return None

    monkeypatch.setattr(composition, "run_startup_steps", no_steps)

    _start(rig, _Start(rig.journal))
    live = rig.root.runners.get("rn_1")
    assert live is not None and live.child is not None
    assert live.child.owner == "rn_1"

    rig.root.close()

    with pytest.raises(ScopeClosed):
        live.child.resolver.get(RunnerService)
