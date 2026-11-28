"""``HostRoot`` over a test-owned runner graph: one child scope per runner beneath the borrowed host (unit tier)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from blizzard.foundation.registry.interface import Lifetime, ScopeClosed, port
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import HOST_LIFETIMES
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, supply
from blizzard.runner.composition import HostProcess, HostRunnerSet, RunnerCompose
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_root import register_host
from tests.support.container_guards import graph_builds
from tests.support.host_runner_graph import HostResource, RunnerService, host_rig


def test_the_host_root_declares_a_graph_that_builds_under_strict_validation() -> None:
    registry = LegacyRegistry()
    register_host(registry)
    values = ScopeValues(
        (
            supply(port(HostProcess), object()),
            supply(port(HostConfig), object()),
            supply(port(RunnerCompose), RunnerCompose()),
        )
    )

    assert graph_builds(registry, HOST_LIFETIMES, values) == []


def test_two_runner_children_share_only_host_owned_resources() -> None:
    rig = host_rig()
    first, second = rig.open("rn_1"), rig.open("rn_2")

    one, two = first.resolver.get(RunnerService), second.resolver.get(RunnerService)

    assert one is not two
    assert (one.built.id, two.built.id) == ("rn_1", "rn_2")
    assert one.shared is two.shared is rig.root.owner.resolver.get(HostResource)
    assert one.host is two.host
    assert (first.lifetime, first.owner) == (Lifetime.RUNNER, "rn_1")
    rig.root.close()


class _Slow:
    """A runner service whose construction takes long enough for every racer to arrive during it."""

    built: list[_Slow]


def _slow() -> _Slow:
    time.sleep(0.05)
    service = _Slow()
    _Slow.built.append(service)
    return service


def _with_slow(registry: RecordingRegistry) -> None:
    registry.factory(_slow, lifetime=Lifetime.RUNNER)


def test_thirty_two_first_resolutions_build_one_runner_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_Slow, "built", [], raising=False)
    rig = host_rig(extra=_with_slow)
    child = rig.open("rn_1")
    barrier = threading.Barrier(32)

    def resolve() -> _Slow:
        barrier.wait(timeout=5)
        return child.resolver.get(_Slow)

    with ThreadPoolExecutor(32) as pool:
        resolved = [future.result() for future in [pool.submit(resolve) for _ in range(32)]]

    assert len(_Slow.built) == 1
    assert all(service is _Slow.built[0] for service in resolved)
    rig.root.close()


def test_a_lookup_through_a_closed_child_raises_scope_closed() -> None:
    rig = host_rig()
    child = rig.open("rn_1")
    child.close()

    with pytest.raises(ScopeClosed) as refused:
        child.resolver.get(RunnerService)

    assert (refused.value.lifetime, refused.value.owner) == (Lifetime.RUNNER, "rn_1")
    rig.root.close()


def test_closing_the_root_closes_each_child_then_its_wiring_before_the_process_resources() -> None:
    rig = host_rig()
    for runner_id in ("rn_1", "rn_2"):
        rig.place(runner_id).resolver.get(RunnerService)

    rig.root.close()
    rig.root.close()

    for runner_id in ("rn_1", "rn_2"):
        assert rig.journal.of(runner_id) == [
            f"built:{runner_id}",
            f"graph:{runner_id}",
            f"child:{runner_id}",
            f"wiring:{runner_id}",
        ]
    assert rig.journal.entries.count("close:host-resource") == 1
    first_close = rig.journal.entries.index("close:host-resource")
    assert all(rig.journal.entries.index(f"wiring:{r}") < first_close for r in ("rn_1", "rn_2"))


def test_the_borrowed_host_stays_open_after_the_root_closes() -> None:
    rig = host_rig()
    rig.open("rn_1").resolver.get(RunnerService)

    rig.root.close()

    assert "close:borrowed-host" not in rig.journal.entries


class _Stubborn:
    """A process resource whose finalizer fails."""


def _open_stubborn() -> Iterator[_Stubborn]:
    yield _Stubborn()
    raise OSError("close refused")


def test_a_failing_cleanup_does_not_mask_the_failure_to_open(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(runners: object, parent: OwnedChild, *, eager: object) -> None:
        parent.resolver.get(_Stubborn)
        raise ValueError("no bind")

    monkeypatch.setattr(HostRunnerSet, "bind", refuse)

    with pytest.raises(ValueError, match="no bind") as failed:
        host_rig(extra=_register_stubborn)

    assert "closing the process scope after it also failed" in "".join(failed.value.__notes__)


def _register_stubborn(registry: RecordingRegistry) -> None:
    registry.resource(_open_stubborn, lifetime=Lifetime.PROCESS)
