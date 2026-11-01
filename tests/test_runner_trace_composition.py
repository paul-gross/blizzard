"""Lease-trace enablement at the runner's composition root (component tier) — OpenTelemetry's own
variables decide whether a sweep is built, and ``runner tick`` never drives one."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.runner.composition import RunnerProcess, build_runner_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.runner.tracing.config import RunnerTracingConfig
from blizzard.runner.tracing.sweep import LeaseTraceSweep, announce_rejected_tracing
from tests.runner_fakes import FIXTURE_RUNNER_ID, REGISTERED_AT, SqlAlchemyRunnerStore, make_store, migrated_store_at
from tests.runner_trace_leases import closed_lease
from tests.support import InMemoryTraceExporter

pytestmark = pytest.mark.component

_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}


def _config(tmp_path: Path) -> tuple[RunnerConfig, SqlAlchemyRunnerStore]:
    config = RunnerConfig(
        root=tmp_path,
        db_url=HostLayout(tmp_path).default_db_url,
        hub_url="http://127.0.0.1:9",
        tracing=RunnerTracingConfig(settle_seconds=0),
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)
    store = make_store(config.db_url)
    # Registered, as a running host is — the process graph binds its store to the identity row it reads at boot.
    store.record_runner_identity(RunnerIdentity(FIXTURE_RUNNER_ID, "runner-local", REGISTERED_AT))
    return config, store


def _cursor_rows(store: SqlAlchemyRunnerStore) -> int:
    with store._engine.connect() as conn:
        return conn.exec_driver_sql("SELECT count(*) FROM trace_cursor").scalar_one()


def _events(store: SqlAlchemyRunnerStore) -> list[dict]:
    return [json.loads(f.payload) for f in store.pending_outbound() if f.kind == EVENT_RECORDED]


def test_no_endpoint_builds_no_sweep_and_writes_no_cursor_row(tmp_path: Path) -> None:
    config, store = _config(tmp_path)
    graph = build_runner_process(
        config, environ={}, trace_exporter=InMemoryTraceExporter(), runner_id=FIXTURE_RUNNER_ID
    )
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
    graph = build_runner_process(
        config, environ=environ, trace_exporter=InMemoryTraceExporter(), runner_id=FIXTURE_RUNNER_ID
    )
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
    graph = build_runner_process(config, environ=_ENDPOINT, trace_exporter=exporter, runner_id=FIXTURE_RUNNER_ID)
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
    graph = build_runner_process(config, environ=_ENDPOINT, runner_id=FIXTURE_RUNNER_ID)
    try:
        assert isinstance(graph.trace_sweep, LeaseTraceSweep)
    finally:
        graph.close()


def test_runner_tick_never_drives_the_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config, store = _config(tmp_path)
    exporter = InMemoryTraceExporter()
    built: list[RunnerProcess] = []

    def traced(config: RunnerConfig, **kwargs: Any) -> RunnerProcess:
        graph = build_runner_process(
            config, environ=_ENDPOINT, trace_exporter=exporter, **kwargs, runner_id=FIXTURE_RUNNER_ID
        )
        built.append(graph)
        return graph

    monkeypatch.setattr(loop_wiring, "build_runner_process", traced)
    LoopWiring.of(config).tick_once()

    assert built and built[0].trace_sweep is not None
    assert exporter.attempts == 0
    assert _cursor_rows(store) == 0
    assert _events(store) == []
