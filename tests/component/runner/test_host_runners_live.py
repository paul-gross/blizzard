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
import structlog
from fastapi import HTTPException
from fastapi.testclient import TestClient

import blizzard.foundation.store.engine as store_engine
import blizzard.runner.app as runner_app
import blizzard.runner.cli.runtime as cli_runtime
import blizzard.runner.composition as composition
import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.registry.interface import ScopeClosed
from blizzard.foundation.registry.scopes import OwnedChild
from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.foundation.secrets.value import SecretValue
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.runner.app import HostedApp, build_hosted_app
from blizzard.runner.cli.runtime import StoreHostedRunners, build_runner_threads, seed_runners
from blizzard.runner.composition import (
    HostProcess,
    HostRunnerSet,
    LiveRunner,
    RunnerCompose,
    RunnerProcess,
    build_runner_admission,
)
from blizzard.runner.host_config import BuiltRunner, HostConfig
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

    def live(self, runner_id: str) -> LiveRunner[Any, Any]:
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
    host = composition.build_host_process(config)
    hosted = build_hosted_app(
        config.host_view(),
        host,
        host_config=config,
        source=StoreHostedRunners.over(config, host),
        boot=ready,
        compose=RunnerCompose(publish=frozenset(built.id for built in ready)),
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


def test_starting_a_runner_from_the_tick_opens_no_engine(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.add()
    opened: list[object] = []

    def counting(*args: Any, **kwargs: Any) -> Any:
        opened.append(args)
        return create_engine(*args, **kwargs)

    create_engine = store_engine.create_engine
    monkeypatch.setattr(store_engine, "create_engine", counting)

    world.host_pass()

    assert world.served() == {TEST_RUNNER_ID, _ADDED}
    assert opened == []


def test_a_runner_started_from_the_tick_imports_its_token_through_the_hosts_own_sealer(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world.add()
    sealers: list[object] = []
    import_hub_token = cli_runtime.import_hub_token

    def recording(tokens: Any, *args: Any) -> Any:
        sealers.append(tokens)
        return import_hub_token(tokens, *args)

    monkeypatch.setattr(cli_runtime, "import_hub_token", recording)

    world.host_pass()

    assert world.host.hub_tokens is not None
    assert sealers == [world.host.hub_tokens]


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
    assert "failed to start at tracing" in (world.host.readiness.evaluate().detail or "")
    world.tick(TEST_RUNNER_ID)

    failing.clear()
    world.host_pass()

    assert reconcile.failures.of(_ADDED) is None
    assert "is halted" not in (world.host.readiness.evaluate().detail or "")
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

    world.hosted.start.serving(drivers=drivers, scratch=lambda _runner: None, said=lambda _note: None)
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


def _record_closes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Each runner graph, child scope, wiring and host close as it happens, by runner id."""
    closed: list[tuple[str, str]] = []
    wired_type = cast(Any, runner_app)._WiredRunner
    close_graph, close_wiring = RunnerProcess.close_runner, wired_type.close
    close_child = cast(Any, HostRunnerSet).close_graph
    close_host = HostProcess.close

    def child(runners: Any, runner: RunnerProcess | None, opened: OwnedChild | None) -> None:
        close_child(runners, runner, opened)
        if opened is not None:
            closed.append(("child", opened.owner))

    def host(process: HostProcess) -> None:
        closed.append(("host", "host"))
        close_host(process)

    monkeypatch.setattr(HostRunnerSet, "close_graph", child)
    monkeypatch.setattr(HostProcess, "close", host)

    def graph(runner: RunnerProcess) -> None:
        closed.append(("graph", runner.runner_id))
        close_graph(runner)

    def wiring(wired: Any) -> None:
        closed.append(("wiring", wired.served.runner_id))
        close_wiring(wired)

    monkeypatch.setattr(RunnerProcess, "close_runner", graph)
    monkeypatch.setattr(wired_type, "close", wiring)
    return closed


def test_abandoning_a_start_that_failed_after_placement_closes_the_graph_before_the_wiring(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(runner: RunnerProcess, stepper: RunnerStepper) -> composition.IRunnerDrivers:
        raise RuntimeError("no drivers")

    world.hosted.start.serving(drivers=refuse, scratch=lambda _runner: None, said=lambda _note: None)
    world.add()
    closed = _record_closes(monkeypatch)

    world.host_pass()

    assert world.served() == {TEST_RUNNER_ID}
    assert closed == [("graph", _ADDED), ("child", _ADDED), ("wiring", _ADDED)]


def test_host_stop_closes_each_runners_graph_before_its_wiring(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    world.add()
    world.host_pass()
    closed = _record_closes(monkeypatch)

    world.hosted.runners.stop_all(mark=False)
    assert closed == []
    world.hosted.close()
    before_host_close = list(closed)
    world.host.close()

    for runner_id in (TEST_RUNNER_ID, _ADDED):
        assert [kind for kind, closed_id in before_host_close if closed_id == runner_id] == ["graph", "child", "wiring"]
    assert closed[len(before_host_close) :][0] == ("host", "host")


def test_a_failing_abandon_keeps_the_start_failure_and_its_sibling_still_starts(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def drivers(runner: RunnerProcess, stepper: RunnerStepper) -> composition.IRunnerDrivers:
        if runner.runner_id == _ADDED:
            raise RuntimeError("no drivers")
        return composition.NoDrivers()

    world.hosted.start.serving(drivers=drivers, scratch=lambda _runner: None, said=lambda _note: None)
    wired_type = cast(Any, runner_app)._WiredRunner
    close_wiring = wired_type.close

    def refusing(wired: Any) -> None:
        close_wiring(wired)
        if wired.served.runner_id == _ADDED:
            raise OSError("wiring would not close")

    monkeypatch.setattr(wired_type, "close", refusing)
    world.add()
    world.add("rn_night2", "night-2")

    with structlog.testing.capture_logs() as logs:
        world.host_pass()

    reconcile = world.hosted.reconcile
    assert reconcile is not None
    failure = reconcile.failures.of(_ADDED)
    assert failure is not None and (failure.step, failure.error) == ("serve", "no drivers")
    assert world.served() == {TEST_RUNNER_ID, "rn_night2"}
    assert world.resolves(_ADDED) == 503
    assert [
        entry["runner_id"] for entry in logs if entry["event"] == "closing a runner that failed to start also failed"
    ] == [_ADDED]


def _record_children(monkeypatch: pytest.MonkeyPatch) -> list[OwnedChild]:
    """Every runner child the host's runner set opens, in order."""
    opened: list[OwnedChild] = []
    open_child = composition.open_child

    def recording(parent: OwnedChild, **kwargs: Any) -> OwnedChild:
        child = open_child(parent, **kwargs)
        opened.append(child)
        return child

    monkeypatch.setattr(composition, "open_child", recording)
    return opened


def _closed(child: OwnedChild) -> bool:
    try:
        child.resolver.get(BuiltRunner)
    except ScopeClosed:
        return True
    return False


def test_a_drained_runners_sibling_keeps_serving_from_its_own_child(world: _World) -> None:
    world.add()
    world.host_pass()
    neighbour = world.live(TEST_RUNNER_ID)
    drained = world.live(_ADDED).child
    assert neighbour.child is not None and drained is not None

    world.retire(_ADDED)
    world.host_pass()

    assert _closed(drained)
    assert world.resolves(TEST_RUNNER_ID) == 200
    assert world.live(TEST_RUNNER_ID) is neighbour
    assert neighbour.child.resolver.get(RunnerProcess) is neighbour.runner
    assert neighbour.child.resolver.get(BuiltRunner).id == TEST_RUNNER_ID


@pytest.mark.parametrize("step", ["compose", "serve"])
def test_a_failed_start_leaves_no_open_child_and_the_next_reconcile_opens_a_new_one(
    world: _World, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    failing = threading.Event()
    failing.set()
    if step == "compose":
        compose = composition.compose_runner

        def refusing_compose(*args: Any, **kwargs: Any) -> RunnerProcess:
            if failing.is_set():
                raise RuntimeError("bad entry")
            return compose(*args, **kwargs)

        monkeypatch.setattr(composition, "compose_runner", refusing_compose)
    else:

        def drivers(runner: RunnerProcess, stepper: RunnerStepper) -> composition.IRunnerDrivers:
            if failing.is_set():
                raise RuntimeError("no drivers")
            return composition.NoDrivers()

        world.hosted.start.serving(drivers=drivers, scratch=lambda _runner: None, said=lambda _note: None)
    opened = _record_children(monkeypatch)
    world.add()

    world.host_pass()

    reconcile = world.hosted.reconcile
    assert reconcile is not None
    failure = reconcile.failures.of(_ADDED)
    assert failure is not None and failure.step == step
    assert len(opened) == 1 and _closed(opened[0])

    failing.clear()
    world.host_pass()

    assert world.served() == {TEST_RUNNER_ID, _ADDED}
    assert len(opened) == 2 and opened[1] is not opened[0] and not _closed(opened[1])
    assert world.live(_ADDED).child is opened[1]


def test_the_boot_publishes_each_runners_bundle_and_a_reconcile_start_publishes_none(
    world: _World, monkeypatch: pytest.MonkeyPatch
) -> None:
    published: dict[str, bool] = {}
    compose = composition.compose_runner

    def recording(*args: Any, publish_bundle: bool = False, **kwargs: Any) -> RunnerProcess:
        runner = compose(*args, publish_bundle=publish_bundle, **kwargs)
        published[runner.runner_id] = publish_bundle
        return runner

    monkeypatch.setattr(composition, "compose_runner", recording)
    world.add()
    world.host_pass()
    assert published == {_ADDED: False}
    world.hosted.close()
    world.host.close()

    world.config, world.host, world.hosted = _boot(world.root)

    assert published == {_ADDED: True, TEST_RUNNER_ID: True}
