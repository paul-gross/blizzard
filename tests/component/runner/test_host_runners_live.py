"""Runners added and retired on a running host (component tier): the host tick reconciles its live runners against the
store's hosted ones — it starts an added runner and drains a retired one — while the runners beside it keep ticking."""

from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import blizzard.runner.composition as composition
import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.foundation.secrets.value import SecretValue
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.runner.app import HostedApp, build_hosted_app
from blizzard.runner.cli.runtime import StoreHostedRunners, build_runner_threads, seed_runners
from blizzard.runner.composition import (
    HostProcess,
    LiveRunner,
    RunnerProcess,
    build_host_process,
    build_runner_admission,
)
from blizzard.runner.host_config import HostConfig
from blizzard.runner.hosting.model import CLI, OPERATOR, HostedRunnerOp, HostingFact, RunnerAdmission
from blizzard.runner.hosting.retirement import HubNoticedRetirement
from blizzard.runner.hub.identity import HostStanding, RunnerIdentity
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.host_tick import HostTick
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.hub_tokens import host_hub_tokens
from tests.support.runner_fakes import make_store, migrated_store_at, runner_store_errors

_ADDED = "rn_night1"
_NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


@dataclass
class _Ticks:
    """Every runner tick, by the identity holder of the runner it ran for."""

    seen: list[object] = field(default_factory=list[object])

    def __call__(self, ctx: LoopContext) -> None:
        self.seen.append(ctx.identity)

    def of(self, runner: RunnerProcess) -> int:
        return sum(1 for identity in self.seen if identity is runner.identity)


@dataclass
class _Registration:
    """The host's registration step, standing in for a hub: ``on_report`` runs at each report."""

    on_report: Callable[[], None] = lambda: None
    runs: int = 0

    def run(self) -> None:
        self.runs += 1
        self.on_report()


@dataclass
class _Resume:
    """A runner's resume hooks, counting its shutdown marking."""

    shutdowns: int = 0

    def on_startup(self) -> int:
        return 0

    def recover_takeovers(self) -> int:
        return 0

    def reconcile_sessions(self) -> int:
        return 0

    def on_shutdown(self) -> int:
        self.shutdowns += 1
        return 0


@dataclass
class _World:
    root: Path
    url: str
    config: HostConfig
    host: HostProcess
    hosted: HostedApp
    ticks: _Ticks
    registration: _Registration

    def runner(self, runner_id: str) -> RunnerProcess:
        return next(runner for runner in self.host.runners if runner.runner_id == runner_id)

    def live(self, runner_id: str) -> LiveRunner:
        live = self.hosted.runners.get(runner_id)
        assert live is not None
        return live

    def stepper(self, runner_id: str) -> RunnerStepper:
        return cast(Any, self.live(runner_id).wired).stepper

    def host_pass(self) -> None:
        assert self.hosted.host_stepper is not None
        assert self.hosted.host_stepper.request().outcome is SweepPassOutcome.COMPLETED

    def tick(self, runner_id: str) -> None:
        assert self.stepper(runner_id).request().outcome is SweepPassOutcome.COMPLETED

    def add(self, runner_id: str = _ADDED, name: str = "night-1") -> None:
        """What an add writes: the runner's token, identity, first settings and hosting, as one admission."""
        with host_hub_tokens(self.root, self.url) as tokens:
            tokens.seal_if_absent(runner_id, SecretValue(f"token-{runner_id}"))
        store = make_store(self.url, runner_id)
        build_runner_admission(
            store.engine, errors=runner_store_errors(), runner_id=runner_id, clock=SystemClock()
        ).admit(
            RunnerAdmission(
                identity=RunnerIdentity(runner_id, name, _NOW),
                entries={"name": Entry(name), "projects": Entry([]), "agents": Entry(1)},
                actor="ada",
                door="api",
                at=_NOW,
            ),
            SealedValue(key_id="unused", ciphertext=b"unused", nonce=b"unused"),
        )
        store.engine.dispose()

    def retire(self, runner_id: str) -> None:
        """What a retire writes: the runner's ``retired`` fact."""
        self.host.host_stores.hosted_runners.append_fact(
            runner_id, HostedRunnerOp.RETIRED, HostingFact(actor=OPERATOR, door=CLI, at=_NOW)
        )

    def served(self) -> set[str]:
        return {runner.runner_id for runner in self.hosted.app.state.runners.served}

    def resolves(self, runner_id: str) -> int:
        """What a request under ``runner_id``'s prefix resolves to: ``200`` when served, else its refusal."""
        try:
            self.hosted.app.state.runners.resolve(runner_id)
        except HTTPException as exc:
            return exc.status_code
        return 200

    def sealed(self, runner_id: str) -> bool:
        return self.host.host_stores.hub_tokens.sealed(runner_id) is not None


def _boot(root: Path) -> tuple[HostConfig, HostProcess, HostedApp]:
    config = load_host_config(root)
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    host = build_host_process(config, ready)
    hosted = build_hosted_app(
        config.host_view(), host, host_config=config, source=StoreHostedRunners(config, SystemClock())
    )
    return config, host, hosted


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[_World]:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'data' / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    migrated_store_at(url)
    RunnerIdentityStore(make_store(url, TEST_RUNNER_ID).connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, "runner-local", _NOW)
    )
    ticks = _Ticks()
    monkeypatch.setattr(loop_wiring, "tick", ticks)
    config, host, hosted = _boot(tmp_path)
    registration = _Registration()
    host.host_tick = HostTick(registration=cast(Any, registration))
    world = _World(tmp_path, url, config, host, hosted, ticks, registration)
    try:
        yield world
    finally:
        world.hosted.runners.stop_all(mark=False)
        world.hosted.close()
        world.host.close()


def test_an_added_runner_starts_at_the_next_tick_and_its_neighbour_keeps_ticking(world: _World) -> None:
    neighbour = world.stepper(TEST_RUNNER_ID)
    world.tick(TEST_RUNNER_ID)
    world.add()
    assert world.served() == {TEST_RUNNER_ID}

    world.host_pass()

    assert world.served() == {TEST_RUNNER_ID, _ADDED}
    assert world.stepper(TEST_RUNNER_ID) is neighbour
    world.tick(_ADDED)
    world.tick(TEST_RUNNER_ID)
    assert world.ticks.of(world.runner(TEST_RUNNER_ID)) == 2
    assert world.ticks.of(world.runner(_ADDED)) == 1
    assert world.runner(_ADDED).config.name == "night-1"
    assert world.resolves(_ADDED) == 200


def test_a_restart_runs_every_hosted_runner(world: _World) -> None:
    world.add()
    world.host_pass()
    world.hosted.close()
    world.host.close()

    world.config, world.host, world.hosted = _boot(world.root)

    assert world.served() == {TEST_RUNNER_ID, _ADDED}
    world.tick(_ADDED)
    world.tick(TEST_RUNNER_ID)


def test_a_runner_the_hub_released_is_told_so_before_its_first_tick(world: _World) -> None:
    seen: dict[str, object] = {}

    def report() -> None:
        # The host's one report falls between preparing the added runner and serving it.
        added = next((runner for runner in world.host.runners if runner.runner_id == _ADDED), None)
        if added is not None:
            seen.setdefault("served", _ADDED in world.served())
            added.identity_verdict.stand(HostStanding.NOT_HELD, reason="not held under this host")

    world.registration.on_report = report
    world.add()

    world.host_pass()

    assert seen == {"served": False}
    assert world.runner(_ADDED).identity_verdict.standing() is HostStanding.NOT_HELD
    world.tick(_ADDED)
    assert world.ticks.of(world.runner(_ADDED)) == 1


def test_a_failing_start_is_retried_at_the_next_tick_while_its_neighbour_keeps_ticking(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    announce = composition.announce_rejected_tracing
    failing = threading.Event()
    failing.set()

    def flaky(*args: Any, **kwargs: Any) -> None:
        if failing.is_set():
            raise RuntimeError("outbound store unavailable")
        announce(*args, **kwargs)

    monkeypatch.setattr(composition, "announce_rejected_tracing", flaky)
    world.add()

    world.host_pass()

    reconcile = world.hosted.reconcile
    assert reconcile is not None
    failure = reconcile.failures.of(_ADDED)
    assert failure is not None and (failure.step, failure.error) == ("tracing", "outbound store unavailable")
    assert world.served() == {TEST_RUNNER_ID}
    assert [runner.runner_id for runner in world.host.runners] == [TEST_RUNNER_ID]
    assert world.resolves(_ADDED) == 503
    world.tick(TEST_RUNNER_ID)

    failing.clear()
    world.host_pass()

    assert reconcile.failures.of(_ADDED) is None
    assert world.served() == {TEST_RUNNER_ID, _ADDED}
    world.tick(_ADDED)
    world.tick(TEST_RUNNER_ID)
    assert world.ticks.of(world.runner(TEST_RUNNER_ID)) == 2


def test_a_retire_drains_only_its_own_runner(world: _World) -> None:
    world.add()
    world.host_pass()
    resumes = {runner_id: _Resume() for runner_id in (TEST_RUNNER_ID, _ADDED)}
    for runner_id, resume in resumes.items():
        live = world.live(runner_id)
        live.resume = resume
        live.started_up = True
    added = world.runner(_ADDED)
    neighbour = world.stepper(TEST_RUNNER_ID)

    world.retire(_ADDED)
    world.host_pass()

    assert (resumes[_ADDED].shutdowns, resumes[TEST_RUNNER_ID].shutdowns) == (1, 0)
    assert world.served() == {TEST_RUNNER_ID}
    assert world.hosted.runners.get(_ADDED) is None
    assert added.executor._shutdown
    assert added.identity_client.is_closed
    assert not world.sealed(_ADDED) and world.sealed(TEST_RUNNER_ID)
    assert RunnerIdentityStore(make_store(world.url, _ADDED).connections).runner_identity() is not None
    assert world.stepper(TEST_RUNNER_ID) is neighbour
    world.tick(TEST_RUNNER_ID)


def test_retiring_the_first_runner_leaves_the_host_page_served(world: _World) -> None:
    world.add()
    world.host_pass()

    world.retire(TEST_RUNNER_ID)
    world.host_pass()

    assert world.served() == {_ADDED}
    with TestClient(world.hosted.app) as client:
        assert client.get("/api/health").status_code == 200
        ready = client.get("/api/ready")
        assert ready.status_code == 200
        assert {item["runner_id"] for item in ready.json()["runners"]} == {_ADDED}
    assert world.resolves(TEST_RUNNER_ID) == 404
    assert world.resolves(_ADDED) == 200
    world.tick(_ADDED)


def test_a_hub_side_retirement_stops_the_runners_threads_in_the_tick_that_notices_it(world: _World) -> None:
    host = world.host

    def drivers(runner: RunnerProcess, stepper: RunnerStepper) -> composition.IRunnerDrivers:
        return build_runner_threads(host, runner, stepper, tick_seconds=3600)

    world.hosted.start.serving(drivers=drivers, scratch=lambda _runner: None, said=lambda _message: None)
    live = world.live(TEST_RUNNER_ID)
    assert live.wired is not None
    live.drivers = world.hosted.start.drive(live.runner, live.wired)
    threads = cast(Any, live.drivers)
    assert threads.driver._thread.is_alive()

    def report() -> None:
        # The registration step's watch: the hub proved the runner retired.
        watch = world.runner(TEST_RUNNER_ID).retirement
        retirement = cast(HubNoticedRetirement, watch._retirement)  # pyright: ignore[reportPrivateUsage]
        retirement.record_retired()

    world.registration.on_report = report

    world.host_pass()

    assert not threads.driver._thread.is_alive()
    assert world.served() == set()
    assert host.host_stores.hosted_runners.retired() == {TEST_RUNNER_ID}
    assert not world.sealed(TEST_RUNNER_ID)
