"""The host record's entries take effect at pass boundaries: each pass works from the snapshot taken at its start."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from structlog.testing import capture_logs

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.foundation.sweep_passes import SweepPassOutcome
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli.runtime import (
    build_runner_threads,
    host_tick_driver,
    seed_host,
    seed_runners,
    tick_interval,
)
from blizzard.runner.composition import HostProcess
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.host_config import BuiltRunner, HostConfig
from blizzard.runner.host_root import OpenedHost
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.settings.authoring import HostSettingsAuthoring
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from tests.support.composed_host import build_host_process, open_host
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_fakes import make_store


def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, top: str = "") -> HostProcess:
    return build_host_process(*_seeded(tmp_path, monkeypatch, top))


def _opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, top: str = "") -> OpenedHost:
    return open_host(*_seeded(tmp_path, monkeypatch, top))


def _seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, top: str) -> tuple[HostConfig, list[BuiltRunner]]:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"\n{top}')
    store = make_store(url, TEST_RUNNER_ID)
    config = load_host_config(tmp_path)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, config.runners[0].name, datetime.now(UTC))
    )
    config = seed_host(config, {}, clock=SystemClock())
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    return config, ready


def _write(host: HostProcess, **sets: object) -> int:
    held = host.host_settings
    assert held is not None
    settings = host.host_stores.settings
    settings.write(
        Change("edit", sets={key.replace("__", "."): value for key, value in sets.items()}),
        if_match=settings.revision(),
        validate=held.reader.validate,
        door="api",
        actor="test",
    )
    return settings.revision()


def test_a_transcripts_or_worker_stdout_write_reaches_the_next_runner_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = _opened(tmp_path, monkeypatch, "[worker_stdout]\nretention_days = 3")
    host = opened.host
    hosted = build_hosted_app(host.runners[0].config, opened)
    seen: list[tuple[int | None, int, int | None]] = []

    def observe(ctx: LoopContext) -> None:
        config = ctx.config
        seen.append(
            (config.host_settings_revision, config.worker_stdout_retention_days, config.transcript_record_max_bytes)
        )

    monkeypatch.setattr(loop_wiring, "tick", observe)
    try:
        assert hosted.steppers[0].request().outcome is SweepPassOutcome.COMPLETED
        revision = _write(host, worker_stdout__retention_days=9, transcripts__record_max_bytes=4096)
        assert hosted.steppers[0].request().outcome is SweepPassOutcome.COMPLETED
        assert seen == [(1, 3, None), (revision, 9, 4096)]
    finally:
        hosted.close()
        opened.close()


class _SweepSpy:
    def __init__(self, during: list[object]) -> None:
        self.seen: list[TracingConfig] = []
        self._during = during

    def sweep(self, config: TracingConfig) -> None:
        self.seen.append(config)
        if self._during:
            # A write landing while this sweep runs: this sweep keeps the snapshot it started with.
            action = self._during.pop()
            assert callable(action)
            action()


def test_a_tracing_write_reaches_the_next_sweep_and_not_the_running_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path, monkeypatch, "[tracing]\nbatch_limit = 10")
    runner = host.runners[0]
    during: list[object] = [lambda: _write(host, tracing__batch_limit=20, tracing__sweep_seconds=2)]
    spy = _SweepSpy(during)
    stepper = RunnerStepper(runner.config, process_graph=replace(runner, trace_sweep=spy))
    try:
        stepper.timed_trace_sweep()
        assert (spy.seen[-1].batch_limit, spy.seen[-1].sweep_seconds) == (10, 60)
        stepper.timed_trace_sweep()
        assert (spy.seen[-1].batch_limit, spy.seen[-1].sweep_seconds) == (20, 2)
        assert stepper.trace_interval() == 2
        assert spy.seen[0].batch_limit == 10
    finally:
        stepper.close()
        host.close()


def test_a_trace_sweep_keeps_its_last_good_tracing_when_a_revision_does_not_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path, monkeypatch, "[tracing]\nbatch_limit = 10")
    runner = host.runners[0]
    spy = _SweepSpy([])
    stepper = RunnerStepper(runner.config, process_graph=replace(runner, trace_sweep=spy))
    reader = runner.settings_reader
    assert reader is not None
    try:
        stepper.timed_trace_sweep()

        def unresolvable(*_args: object, **_kwargs: object) -> object:
            raise ValueError("revision does not resolve")

        monkeypatch.setattr(reader, "resolve_pair", unresolvable)
        with capture_logs() as logs:
            stepper.timed_trace_sweep()
            stepper.timed_trace_sweep()
        assert [config.batch_limit for config in spy.seen] == [10, 10, 10]
        assert [entry["event"] for entry in logs if entry["log_level"] == "error"] == [
            "trace sweep settings did not resolve"
        ]
    finally:
        stepper.close()
        host.close()


def test_a_tracing_write_reaches_the_next_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = _host(tmp_path, monkeypatch)
    runner = host.runners[0]
    try:
        _write(host, tracing__replay_max_window=60)
        since = datetime(2026, 1, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="60"):
            runner.trace_replay.replay(since, since.replace(hour=2), dry_run=True)
    finally:
        host.close()


def test_a_harness_telemetry_write_reaches_the_next_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = _host(tmp_path, monkeypatch)
    reader = host.runners[0].settings_reader
    assert reader is not None
    try:
        before, config = reader.snapshot_pair()
        assert config.tracing.harness_telemetry is False
        revision = _write(host, tracing__harness_telemetry=True)
        after, config = reader.snapshot_pair()
        assert (before.runner.revision, after.host_revision()) == (after.runner.revision, revision)
        assert config.tracing.harness_telemetry is True
        # The snapshot already held resolves as it did, so a pass started before the write is unchanged.
        assert reader.resolve_pair(before).tracing.harness_telemetry is False
    finally:
        host.close()


def test_a_tools_write_reaches_the_next_host_tool_child_and_not_a_running_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_TOOL_PROFILE_PROBE", "on")
    opened = _opened(tmp_path, monkeypatch)
    host = opened.host
    hosted = build_hosted_app(host.runners[0].config, opened)
    try:
        assert hosted.host_stepper is not None
        running = host.tool_profile.current()
        assert "BZ_TOOL_PROFILE_PROBE" not in running.variables
        revision = _write(host, tools__env_passthrough=["BZ_TOOL_PROFILE_PROBE"])
        # Written, but not in force until the host tick's next snapshot.
        assert host.tool_profile.current() is running
        assert hosted.host_stepper.request().outcome is SweepPassOutcome.COMPLETED
        assert host.tool_profile.current().variables["BZ_TOOL_PROFILE_PROBE"] == "on"
        assert "BZ_TOOL_PROFILE_PROBE" not in running.variables
        assert host.host_settings is not None
        assert host.host_settings.revision == revision
        assert host.host_settings.since is not None
    finally:
        hosted.close()
        opened.close()


def test_an_edited_seeded_host_key_in_the_file_changes_nothing_after_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = _host(tmp_path, monkeypatch, "[worker_stdout]\nretention_days = 3")
    host.close()
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"\n[worker_stdout]\nretention_days = 8')
    config = seed_host(load_host_config(tmp_path), {}, clock=SystemClock())
    ready, _ = seed_runners(config, clock=SystemClock())
    restarted = build_host_process(config, ready)
    try:
        assert config.worker_stdout.retention_days == 3
        assert restarted.runners[0].config.worker_stdout_retention_days == 3
    finally:
        restarted.close()


def test_a_tick_seconds_write_from_an_hour_to_a_second_takes_hold_without_waiting_the_hour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = _opened(tmp_path, monkeypatch, "tick_seconds = 3600")
    host = opened.host
    hosted = build_hosted_app(host.runners[0].config, opened)
    held = host.host_settings
    assert held is not None and hosted.host_stepper is not None
    ticks = threading.Semaphore(0)
    host_passes = threading.Semaphore(0)

    def counted(ctx: LoopContext) -> None:
        ticks.release()

    monkeypatch.setattr(loop_wiring, "tick", counted)
    host_pass = hosted.host_stepper

    class _CountedHostPass:
        def timed(self) -> None:
            host_pass.timed()
            host_passes.release()

    threads = build_runner_threads(host, host.runners[0], hosted.steppers[0], tick_seconds=3600)
    host_driver = host_tick_driver(host, interval=tick_interval(host, 3600), stepper=cast(Any, _CountedHostPass()))
    threads.start()
    host_driver.start()
    try:
        assert ticks.acquire(timeout=10) and host_passes.acquire(timeout=10)
        authoring = HostSettingsAuthoring(held.reader, host.host_stores.settings, HOST_REGISTRY, drivers=host.wakes)
        authoring.edit(
            {"tick_seconds": 1}, frozenset(), if_match=host.host_stores.settings.revision(), door="api", actor="test"
        )
        # Both drivers were waiting out the hour; the write wakes each onto the new second.
        assert ticks.acquire(timeout=10) and ticks.acquire(timeout=10)
        assert host_passes.acquire(timeout=10)
    finally:
        host_driver.stop()
        threads.stop()
        hosted.close()
        opened.close()


def test_a_write_not_naming_an_interval_wakes_no_driver(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = _host(tmp_path, monkeypatch)
    held = host.host_settings
    assert held is not None
    signal = host.wakes.signal()
    try:
        authoring = HostSettingsAuthoring(held.reader, host.host_stores.settings, HOST_REGISTRY, drivers=host.wakes)
        authoring.edit(
            {"worker_stdout.retention_days": 4},
            frozenset(),
            if_match=host.host_stores.settings.revision(),
            door="api",
            actor="test",
        )
        assert not signal.event.is_set()
        authoring.edit(
            {"tracing.sweep_seconds": 5},
            frozenset(),
            if_match=host.host_stores.settings.revision(),
            door="api",
            actor="test",
        )
        assert signal.event.is_set()
    finally:
        host.close()


def test_a_tracing_write_reaches_the_next_worker_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host = _host(tmp_path, monkeypatch)
    try:
        before = host.telemetry_receiver.tracing()
        assert (before.harness_telemetry, before.worker_programs, dict(before.mapped_services)) == (False, False, {})
        _write(
            host,
            tracing__harness_telemetry=True,
            tracing__worker_programs=True,
            tracing__worker_program_services={"winter": "winter-cli"},
        )
        after = host.telemetry_receiver.tracing()
        assert (after.harness_telemetry, after.worker_programs) == (True, True)
        assert dict(after.mapped_services) == {"winter": "winter-cli"}
    finally:
        host.close()
