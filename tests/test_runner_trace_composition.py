"""Lease-trace enablement at the runner's composition root (component tier) — OpenTelemetry's own
variables decide whether a sweep is built, and ``runner tick`` never drives one."""

from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.runner.composition import RunnerProcess, build_runner_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.runner.tracing.sweep import LeaseTraceSweep, announce_rejected_tracing
from blizzard.runner.tracing.trace_driver import TraceSweepDriver
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store
from tests.runner_trace_leases import closed_lease
from tests.support import InMemoryTraceExporter

pytestmark = pytest.mark.component

_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}


def _config(tmp_path: Path) -> tuple[RunnerConfig, SqlAlchemyRunnerStore]:
    config = RunnerConfig(
        root=tmp_path,
        db_url=RunnerConfig.default_db_url(tmp_path),
        hub_url="http://127.0.0.1:9",
        tracing=TracingConfig(settle_seconds=0),
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    return config, make_store(config.db_url)


def _cursor_rows(store: SqlAlchemyRunnerStore) -> int:
    with store._engine.connect() as conn:
        return conn.exec_driver_sql("SELECT count(*) FROM trace_cursor").scalar_one()


def _events(store: SqlAlchemyRunnerStore) -> list[dict]:  # type: ignore[type-arg]
    return [json.loads(f.payload) for f in store.pending_outbound() if f.kind == EVENT_RECORDED]


def test_no_endpoint_builds_no_sweep_and_writes_no_cursor_row(tmp_path: Path) -> None:
    config, store = _config(tmp_path)
    graph = build_runner_process(config, environ={}, trace_exporter=InMemoryTraceExporter())
    try:
        assert graph.trace_settings.state == "disabled"
        assert graph.trace_sweep is None
        announce_rejected_tracing(graph.trace_settings, graph.stores.outbound, graph.clock.now())
        assert _cursor_rows(store) == 0
        assert _events(store) == []
    finally:
        graph.close()


def test_grpc_is_rejected_and_announced_once_with_no_sweep(tmp_path: Path) -> None:
    config, store = _config(tmp_path)
    environ = {**_ENDPOINT, "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}
    graph = build_runner_process(config, environ=environ, trace_exporter=InMemoryTraceExporter())
    try:
        assert graph.trace_settings.state == "rejected"
        assert graph.trace_sweep is None
        announce_rejected_tracing(graph.trace_settings, graph.stores.outbound, graph.clock.now())
        events = _events(store)
        assert [e["kind"] for e in events] == ["trace-config-rejected"]
        assert events[0]["detail"] == {"setting": "OTEL_EXPORTER_OTLP_PROTOCOL", "value": "grpc"}
        assert _cursor_rows(store) == 0
    finally:
        graph.close()


def test_an_endpoint_builds_a_sweep_over_the_injected_exporter(tmp_path: Path) -> None:
    config, store = _config(tmp_path)
    exporter = InMemoryTraceExporter()
    graph = build_runner_process(config, environ=_ENDPOINT, trace_exporter=exporter)
    try:
        assert graph.trace_settings.state == "enabled"
        assert isinstance(graph.trace_sweep, LeaseTraceSweep)
        graph.trace_sweep.sweep()  # the first pass opens the cursor at now
        now = graph.clock.now()
        closed_lease(store, "lease-01", opened=now - timedelta(minutes=1), closed=now)
        graph.trace_sweep.sweep()
        assert len(exporter.batches) == 1
        assert _cursor_rows(store) == 2
    finally:
        graph.close()


def test_an_endpoint_without_an_injected_exporter_binds_otlp(tmp_path: Path) -> None:
    config, _ = _config(tmp_path)
    graph = build_runner_process(config, environ=_ENDPOINT)
    try:
        assert isinstance(graph.trace_sweep, LeaseTraceSweep)
    finally:
        graph.close()


def test_runner_tick_never_drives_the_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, store = _config(tmp_path)
    exporter = InMemoryTraceExporter()
    built: list[RunnerProcess] = []

    def traced(config: RunnerConfig, **kwargs: object) -> RunnerProcess:
        graph = build_runner_process(config, environ=_ENDPOINT, trace_exporter=exporter, **kwargs)  # type: ignore[arg-type]
        built.append(graph)
        return graph

    monkeypatch.setattr(loop_wiring, "build_runner_process", traced)
    LoopWiring.of(config).tick_once()

    assert built and built[0].trace_sweep is not None
    assert exporter.attempts == 0
    assert _cursor_rows(store) == 0
    assert _events(store) == []


class _Counting:
    def __init__(self, *, raises: bool = False) -> None:
        self.passes = 0
        self.raises = raises

    def sweep(self) -> None:
        self.passes += 1
        if self.raises:
            raise RuntimeError("bad pass")


def test_the_driver_sweeps_after_its_jitter_and_survives_a_raising_pass() -> None:
    sweep = _Counting(raises=True)
    driver = TraceSweepDriver(sweep, interval_seconds=0.01, jitter_seconds=0)
    driver.start()
    try:
        deadline = time.monotonic() + 5
        while sweep.passes < 3 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        driver.stop()
    assert sweep.passes >= 3


def test_the_driver_stop_is_bounded_by_a_hung_pass() -> None:
    release = threading.Event()

    class _Hung:
        def sweep(self) -> None:
            release.wait(10)

    driver = TraceSweepDriver(_Hung(), interval_seconds=60, jitter_seconds=0, stop_timeout_seconds=0.2)
    driver.start()
    time.sleep(0.05)
    started = time.monotonic()
    driver.stop()
    assert time.monotonic() - started < 2
    release.set()
