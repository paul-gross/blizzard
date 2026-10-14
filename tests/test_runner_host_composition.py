"""The runner daemon composed as a host around its runners (component tier): per-runner objects are reachable from the
host only through ``runners``, and each runner's tick and the host's own tick run on threads of their own."""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.logging import get_logger
from blizzard.runner.composition import HostProcess, add_runner, build_host_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.host_config import BuiltRunner, HaltedRunner, ResolvedToken
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.held_token import HubTokenHolder
from blizzard.runner.hub.identity import IdentityVerdictHolder, RunnerIdentityHolder
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop_wiring import PeriodicDriver
from blizzard.runner.stores import RunnerStores
from blizzard.runner.usage.credential_renewal import CredentialRenewalPass
from blizzard.runner.usage.host_tick import HostTick
from blizzard.runner.usage.periodic_pass_driver import PeriodicPassDriver
from tests.host_files import host_config_of
from tests.runner_fakes import migrated_store_at

pytestmark = pytest.mark.component

_RUNNER_OWNED = (
    RunnerStores,
    RunnerIdentityHolder,
    IdentityVerdictHolder,
    HubTokenHolder,
    HarnessRegistry,
    ThreadPoolExecutor,
)


def _config(tmp_path: Path) -> RunnerConfig:
    config = RunnerConfig(root=tmp_path, db_url=HostLayout(tmp_path).default_db_url, hub_url="http://127.0.0.1:9")
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)
    return config


def _host(config: RunnerConfig, *runner_ids: str, halted: tuple[HaltedRunner, ...] = ()) -> HostProcess:
    """The host the config's file declares, with one built runner for each of ``runner_ids``."""
    built = [BuiltRunner(rid, dataclasses.replace(config, runner_id=rid), ResolvedToken()) for rid in runner_ids]
    return build_host_process(host_config_of(config), built, halted=halted)


def _reachable_runner_owned(root: object) -> list[str]:
    """The paths from ``root`` to a runner-owned object, never walking through ``HostProcess.runners``."""
    found: list[str] = []
    seen: set[int] = set()

    def walk(value: object, path: str) -> None:
        if id(value) in seen or isinstance(value, (str, bytes, int, float, bool, type(None), Path)):
            return
        seen.add(id(value))
        if isinstance(value, _RUNNER_OWNED):
            found.append(path)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{path}[{key!r}]")
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            for member in dataclasses.fields(value):
                if isinstance(value, HostProcess) and member.name == "runners":
                    continue
                walk(getattr(value, member.name), f"{path}.{member.name}")
        elif type(value).__module__.startswith("blizzard") and hasattr(value, "__dict__"):
            for name, item in list(vars(value).items()):
                walk(item, f"{path}.{name}")

    walk(root, "host")
    return found


def test_no_runner_owned_object_is_reachable_from_the_host_except_through_its_runners(tmp_path: Path) -> None:
    host = _host(_config(tmp_path), "rn_a")
    try:
        assert len(host.runners) == 1
        assert _reachable_runner_owned(host) == []
        # The walk is not vacuous: the runner's own graph holds every type it forbids elsewhere.
        assert _reachable_runner_owned(host.runners[0]) != []
    finally:
        host.close()


class _CountingRenewal:
    def __init__(self) -> None:
        self.passes = 0

    def run(self) -> None:
        self.passes += 1


def _wait_until(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def test_one_runners_blocked_tick_delays_neither_the_other_runner_nor_the_host_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    host = _host(config, "rn_a")
    blocked, free = host.runners[0], add_runner(dataclasses.replace(config, runner_id="rn_b"), host, runner_id="rn_b")
    release = threading.Event()
    entered = threading.Event()
    ticks: dict[int, int] = {id(blocked.stores): 0, id(free.stores): 0}

    def tick(ctx: LoopContext) -> None:
        owner = id(ctx.stores)
        ticks[owner] += 1
        if owner == id(blocked.stores):
            entered.set()
            release.wait(timeout=30)

    monkeypatch.setattr(loop_wiring, "tick", tick)
    renewal: Any = _CountingRenewal()
    host.host_tick = HostTick(credential_renewal=renewal)
    drivers = [PeriodicDriver(config, interval_seconds=0.01, process_graph=runner) for runner in (blocked, free)]
    host_tick = PeriodicPassDriver(
        host.host_tick.run,
        name="test-host-tick",
        label="host tick",
        log=get_logger("blizzard.runner.test"),
        interval_seconds=0.01,
        jitter_seconds=0,
    )
    assert not isinstance(renewal, CredentialRenewalPass)  # a counting double stands in for the renewal pass
    try:
        for driver in drivers:
            driver.start()
        host_tick.start()
        assert entered.wait(timeout=10), "the blocking runner's tick never ran"
        free_before, host_before = ticks[id(free.stores)], renewal.passes
        _wait_until(
            lambda: ticks[id(free.stores)] >= free_before + 3 and renewal.passes >= host_before + 3,
            "the free runner's tick and the host tick to advance while the other runner's tick is blocked",
        )
        assert ticks[id(blocked.stores)] == 1
    finally:
        release.set()
        host_tick.stop()
        for driver in drivers:
            driver.stop()
        host.close()


def test_each_built_runner_is_bound_to_its_configured_id_and_a_halted_one_is_kept_unbuilt(tmp_path: Path) -> None:
    halted = HaltedRunner("r-broken", "rn_c", ("names subscription 'x', which the host does not declare",))
    host = _host(_config(tmp_path), "rn_a", "rn_b", halted=(halted,))
    try:
        assert [runner.runner_id for runner in host.runners] == ["rn_a", "rn_b"]
        assert [runner.connections.runner_id for runner in host.runners] == ["rn_a", "rn_b"]
        assert host.halted == (halted,)
    finally:
        host.close()


def test_the_readiness_names_each_halted_runner_and_keeps_meaning_the_stores(tmp_path: Path) -> None:
    halted = HaltedRunner("r-broken", "rn_c", ("has no hub token",))
    host = _host(_config(tmp_path), "rn_a", halted=(halted,))
    try:
        readiness = host.readiness.evaluate()
        assert readiness.ready is True
        assert halted.describe() in readiness.detail
        assert "r-broken" in readiness.detail and "rn_c" in readiness.detail and "has no hub token" in readiness.detail
    finally:
        host.close()
