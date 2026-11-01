from __future__ import annotations

import dataclasses
import signal
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli import runtime
from blizzard.runner.cli.runtime import ENV_TICK_SECONDS
from blizzard.runner.composition import build_host_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import BuiltRunner, ResolvedToken
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.host_tick import HostTick
from blizzard.runner.loop.mode import LoopMode
from blizzard.runner.loop.step_passes import HostPassStepper, PassResult, TickInProgress
from blizzard.runner.loop_wiring import RunnerStepper
from tests.config_inputs import create_runner_app
from tests.host_files import host_config_of, one_workspace
from tests.runner_fakes import FIXTURE_RUNNER_ID, loop_graph, migrated_store_at

pytestmark = pytest.mark.component

_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
_LOG = get_logger("tests.step_passes")


def _host_stepper(run: Any) -> HostPassStepper:
    return HostPassStepper(run, clock=FixedClock(_NOW), log=_LOG, label="host tick")


def _config(tmp_path: Path) -> RunnerConfig:
    config = RunnerConfig(
        runner_id=FIXTURE_RUNNER_ID,
        root=tmp_path,
        db_url=HostLayout(tmp_path).default_db_url,
        workspaces=one_workspace(root=str(tmp_path / "workspace")),
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)
    return config


class _Blocking:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, *_: object) -> None:
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=30)


def _hold_in_thread(stepper: HostPassStepper | RunnerStepper, blocking: _Blocking) -> threading.Thread:
    thread = threading.Thread(target=stepper.request)
    thread.start()
    assert blocking.entered.wait(timeout=10)
    return thread


def test_a_requested_host_pass_runs_once_and_answers_completed() -> None:
    calls: list[int] = []
    result = _host_stepper(lambda: calls.append(1)).request()

    assert calls == [1]
    assert (result.outcome, result.error) == (SweepPassOutcome.COMPLETED, None)
    assert result.started_at <= result.finished_at


def test_a_host_pass_that_raises_answers_failed_with_its_message_and_the_stepper_survives() -> None:
    def boom() -> None:
        raise RuntimeError("renewal broke")

    stepper = _host_stepper(boom)

    assert stepper.request() == PassResult(_NOW, _NOW, SweepPassOutcome.FAILED, "renewal broke")
    assert stepper.request().outcome is SweepPassOutcome.FAILED


def test_a_host_pass_that_raises_with_no_message_answers_its_type_name() -> None:
    def boom() -> None:
        raise KeyError

    assert _host_stepper(boom).request().error == "KeyError"


def test_a_host_pass_is_refused_at_once_while_another_holds_the_lock_and_a_timed_one_skips() -> None:
    blocking = _Blocking()
    stepper = _host_stepper(blocking)
    holder = _hold_in_thread(stepper, blocking)
    try:
        with pytest.raises(TickInProgress):
            stepper.request()
        stepper.timed()
        assert blocking.calls == 1
    finally:
        blocking.release.set()
        holder.join(timeout=10)

    assert stepper.request().outcome is SweepPassOutcome.COMPLETED
    assert blocking.calls == 2


def test_a_requested_runner_tick_runs_the_loops_tick_over_one_long_lived_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    contexts: list[LoopContext] = []
    monkeypatch.setattr(loop_wiring, "tick", contexts.append)
    config = _config(tmp_path)
    with loop_graph(config) as graph:
        stepper = RunnerStepper(config, process_graph=graph)
        try:
            first, second = stepper.request(), stepper.request()
        finally:
            stepper.close()

    assert (first.outcome, second.outcome) == (SweepPassOutcome.COMPLETED, SweepPassOutcome.COMPLETED)
    assert len(contexts) == 2 and contexts[0] is contexts[1]


def test_a_runner_tick_that_raises_answers_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_: LoopContext) -> None:
        raise RuntimeError("hub unreachable")

    monkeypatch.setattr(loop_wiring, "tick", boom)
    config = _config(tmp_path)
    with loop_graph(config) as graph:
        stepper = RunnerStepper(config, process_graph=graph)
        try:
            result = stepper.request()
            stepper.timed_tick()
        finally:
            stepper.close()

    assert (result.outcome, result.error) == (SweepPassOutcome.FAILED, "hub unreachable")


class _TraceSweep:
    def __init__(self) -> None:
        self.sweeps = 0

    def sweep(self) -> None:
        self.sweeps += 1


def test_a_requested_runner_pass_runs_one_trace_sweep_after_its_tick_when_tracing_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    sweep = _TraceSweep()

    def tick(_: LoopContext) -> None:
        order.append("tick")

    monkeypatch.setattr(loop_wiring, "tick", tick)
    config = _config(tmp_path)
    with loop_graph(config) as graph:
        traced = dataclasses.replace(graph, trace_sweep=sweep)
        stepper = RunnerStepper(config, process_graph=traced)
        sweep.sweep = lambda: order.append("sweep")
        try:
            assert stepper.traces()
            assert stepper.request().outcome is SweepPassOutcome.COMPLETED
        finally:
            stepper.close()

    assert order == ["tick", "sweep"]


def test_a_requested_runner_pass_is_refused_while_a_timed_tick_runs_and_the_timed_tick_skips_while_one_is_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocking = _Blocking()
    monkeypatch.setattr(loop_wiring, "tick", blocking)
    config = _config(tmp_path)
    with loop_graph(config) as graph:
        stepper = RunnerStepper(config, process_graph=graph)
        timed = threading.Thread(target=stepper.timed_tick)
        timed.start()
        try:
            assert blocking.entered.wait(timeout=10)
            with pytest.raises(TickInProgress):
                stepper.request()
            blocking.release.set()
            timed.join(timeout=10)
            blocking.release.clear()
            blocking.entered.clear()
            requested = _hold_in_thread(stepper, blocking)
            stepper.timed_tick()
            assert blocking.calls == 2
        finally:
            blocking.release.set()
            timed.join(timeout=10)
            stepper.close()
        requested.join(timeout=10)


def test_one_runners_held_lock_refuses_only_that_runner_and_never_the_host_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticks: list[object] = []
    blocking = _Blocking()

    def tick(ctx: LoopContext) -> None:
        if ctx.config.runner_id == "rn_a":
            blocking(ctx)
        else:
            ticks.append(ctx)

    monkeypatch.setattr(loop_wiring, "tick", tick)
    config_a = dataclasses.replace(_config(tmp_path / "a"), runner_id="rn_a")
    config_b = dataclasses.replace(_config(tmp_path / "b"), runner_id="rn_b")
    with loop_graph(config_a, runner_id="rn_a") as graph_a, loop_graph(config_b, runner_id="rn_b") as graph_b:
        stepper_a = RunnerStepper(config_a, process_graph=graph_a)
        stepper_b = RunnerStepper(config_b, process_graph=graph_b)
        host = _host_stepper(lambda: None)
        holder = _hold_in_thread(stepper_a, blocking)
        try:
            with pytest.raises(TickInProgress):
                stepper_a.request()
            assert stepper_b.request().outcome is SweepPassOutcome.COMPLETED
            assert host.request().outcome is SweepPassOutcome.COMPLETED
        finally:
            blocking.release.set()
            holder.join(timeout=10)
            stepper_a.close()
            stepper_b.close()
    assert len(ticks) == 1


def test_quiesce_waits_out_a_requested_tick(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocking = _Blocking()
    monkeypatch.setattr(loop_wiring, "tick", blocking)
    config = _config(tmp_path)
    with loop_graph(config) as graph:
        stepper = RunnerStepper(config, process_graph=graph)
        holder = _hold_in_thread(stepper, blocking)
        waited = threading.Thread(target=stepper.quiesce)
        waited.start()
        waited.join(timeout=0.2)
        assert waited.is_alive()
        blocking.release.set()
        waited.join(timeout=10)
        holder.join(timeout=10)
        stepper.close()
    assert not waited.is_alive()


class _Stepper:
    def __init__(self, answer: PassResult | TickInProgress) -> None:
        self._answer = answer
        self.requests = 0

    def request(self) -> PassResult:
        self.requests += 1
        if isinstance(self._answer, TickInProgress):
            raise self._answer
        return self._answer


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[TestClient, _Stepper, _Stepper]]:
    runner = _Stepper(PassResult(_NOW, _NOW, SweepPassOutcome.COMPLETED))
    host = _Stepper(PassResult(_NOW, _NOW, SweepPassOutcome.FAILED, "renewal broke"))
    app = create_runner_app(_config(tmp_path), stepper=runner, host_stepper=host)
    with TestClient(app) as client:
        yield client, runner, host


def test_the_tick_route_answers_completed_with_its_span(served: tuple[TestClient, _Stepper, _Stepper]) -> None:
    client, runner, _ = served

    response = client.post(f"/runners/{FIXTURE_RUNNER_ID}/api/ticks")

    assert response.status_code == 200
    assert response.json() == {
        "started_at": "2026-08-01T12:00:00+00:00",
        "finished_at": "2026-08-01T12:00:00+00:00",
        "outcome": "completed",
        "error": None,
    }
    assert runner.requests == 1


def test_the_host_route_answers_a_failed_pass_as_200(served: tuple[TestClient, _Stepper, _Stepper]) -> None:
    client, _, host = served

    response = client.post("/api/host/ticks")

    assert response.status_code == 200
    assert (response.json()["outcome"], response.json()["error"]) == ("failed", "renewal broke")
    assert host.requests == 1


def test_a_held_lock_answers_409_tick_in_progress_on_both_routes(tmp_path: Path) -> None:
    refused = _Stepper(TickInProgress())
    client = TestClient(create_runner_app(_config(tmp_path), stepper=refused, host_stepper=refused))

    for path in (f"/runners/{FIXTURE_RUNNER_ID}/api/ticks", "/api/host/ticks"):
        response = client.post(path)
        assert (response.status_code, response.json()) == (409, {"code": "tick_in_progress"})


def test_an_unknown_runner_is_404_and_starts_no_pass(served: tuple[TestClient, _Stepper, _Stepper]) -> None:
    client, runner, _ = served

    response = client.post("/runners/rn_nobody/api/ticks")

    assert response.status_code == 404
    assert runner.requests == 0


class _Registration:
    def __init__(self) -> None:
        self.runs = 0

    def run(self) -> None:
        self.runs += 1


class _Resume:
    def __init__(self) -> None:
        self.startups = 0
        self.shutdowns = 0

    def on_startup(self) -> int:
        self.startups += 1
        return 0

    def on_shutdown(self) -> int:
        self.shutdowns += 1
        return 0


class _ServingNothing:
    def __init__(self, body: Callable[[], None]) -> None:
        self._body = body

    def run(self, *, sockets: object) -> None:
        self._body()

    def handle_exit(self, *_: object) -> None: ...


_LOOP_THREADS = {"blizzard-runner-loop", "blizzard-host-tick"}


@pytest.mark.parametrize("loop", list(LoopMode))
def test_a_stepped_host_starts_no_timer_yet_reconciles_reports_once_and_marks_at_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, loop: LoopMode
) -> None:
    ticked: list[object] = []
    monkeypatch.setattr(loop_wiring, "tick", ticked.append)

    def install_no_handler(*_: object) -> None:
        return None

    monkeypatch.setattr(signal, "signal", install_no_handler)
    monkeypatch.setenv(ENV_TICK_SECONDS, "3600")
    config = _config(tmp_path)
    built = [BuiltRunner(FIXTURE_RUNNER_ID, config, ResolvedToken())]
    host = build_host_process(host_config_of(config), built)
    registration = _Registration()
    host.host_tick = HostTick(registration=cast(Any, registration))

    def no_listeners() -> list[object]:
        return []

    cast(Any, host).listeners = SimpleNamespace(bound=no_listeners)
    resume = _Resume()
    seen: dict[str, object] = {}

    def serving() -> None:
        seen["threads"] = {thread.name for thread in threading.enumerate()} & _LOOP_THREADS
        seen["startups"], seen["reports"], seen["shutdowns"] = resume.startups, registration.runs, resume.shutdowns
        if loop is LoopMode.STEPPED:
            seen["result"] = hosted.steppers[0].request()

    def build_server(*_: object, **__: object) -> _ServingNothing:
        return _ServingNothing(serving)

    monkeypatch.setattr(runtime, "build_early_shutdown_server", build_server)
    hosted = dataclasses.replace(build_hosted_app(config, host), resumes=(resume,))
    try:
        cast(Any, runtime)._serve_host(config, host, hosted, loop=loop)
    finally:
        hosted.close()
        host.close()

    assert (seen["startups"], seen["shutdowns"]) == (1, 0)
    assert resume.shutdowns == 1
    if loop is LoopMode.TIMED:
        assert seen["threads"] == _LOOP_THREADS
        assert cast(int, seen["reports"]) >= 1
    else:
        assert seen["threads"] == set()
        assert cast(PassResult, seen["result"]).outcome is SweepPassOutcome.COMPLETED
        assert seen["reports"] == 1 and len(ticked) == 1 and registration.runs == 1


def test_a_requested_pass_whose_tick_and_trace_sweep_both_raise_answers_both_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_: LoopContext) -> None:
        raise RuntimeError("tick broke")

    class _Failing:
        def sweep(self) -> None:
            raise RuntimeError("sweep broke")

    monkeypatch.setattr(loop_wiring, "tick", boom)
    config = _config(tmp_path)
    with loop_graph(config) as graph:
        stepper = RunnerStepper(config, process_graph=dataclasses.replace(graph, trace_sweep=cast(Any, _Failing())))
        try:
            result = stepper.request()
        finally:
            stepper.close()

    assert (result.outcome, result.error) == (SweepPassOutcome.FAILED, "tick broke; sweep broke")
