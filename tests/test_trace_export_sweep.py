"""The trace export sweep (component tier) — closed steps driven through the hub's real routes,
told to the in-memory exporter one ``sweep()`` at a time against a fixed clock."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.trace_ids import SpanRole, StepKey, span_id
from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.app import Sweep
from blizzard.hub.config import TracingConfig
from blizzard.hub.domain.tracing.cursor import BACKOFF_CAP, CursorKey
from blizzard.hub.domain.tracing.repository import TraceCursorRecord
from blizzard.hub.domain.tracing.sweep import TraceExportSweep
from blizzard.hub.store import schema
from blizzard.hub.store.internal.trace_store import TraceStore
from tests.support import HubHarness, InMemoryTraceExporter, hub_store_connections
from tests.trace_hub import label, trace_hub, transitioned_and_stopped

pytestmark = pytest.mark.component

_SETTLED = TracingConfig(settle_seconds=0, sweep_seconds=60, max_lag_seconds=3600)


def _sweep(hub: HubHarness) -> TraceExportSweep:
    assert hub.services.trace_export is not None
    return hub.services.trace_export


def _store(hub: HubHarness) -> TraceStore:
    return TraceStore(hub_store_connections(hub.engine), graphs=hub.services.graphs, label=label)


def _row_count(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(schema.trace_cursor)).scalar_one()


def _kinds(hub: HubHarness) -> list[str]:
    with hub.engine.connect() as conn:
        rows = conn.execute(sa.select(schema.event_log.c.kind).order_by(schema.event_log.c.id)).all()
    return [r.kind for r in rows if r.kind.startswith("trace-")]


def _skipped_detail(hub: HubHarness) -> dict:  # type: ignore[type-arg]
    with hub.engine.connect() as conn:
        row = conn.execute(
            sa.select(schema.event_log.c.detail).where(schema.event_log.c.kind == "trace-window-skipped")
        ).one()
    return json.loads(row.detail) if isinstance(row.detail, str) else row.detail


def _hub(tmp_path: Path, config: TracingConfig = _SETTLED) -> tuple[HubHarness, InMemoryTraceExporter]:
    exporter = InMemoryTraceExporter()
    hub, _graph = trace_hub(tmp_path, trace_exporter=exporter, tracing=config)
    return hub, exporter


def _closed_pair(hub: HubHarness, ref: int = 1) -> tuple[str, str]:
    graph = hub.services.graphs.get_enabled_by_name("default-delivery")
    assert graph is not None
    return transitioned_and_stopped(hub, graph, ref)


def _roots(exporter: InMemoryTraceExporter) -> list[int]:
    return [s.context.span_id for s in exporter.spans if s.parent_span_id is None]


def _root(chunk_id: str) -> int:
    return span_id(StepKey.attempt(chunk_id, 1), SpanRole.STEP)


def test_the_first_pass_starts_the_cursor_at_now_and_exports_nothing(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path)
    _closed_pair(hub)  # closed before tracing was on: history is told only by replay
    hub.clock.advance(timedelta(seconds=1))

    _sweep(hub).sweep()

    assert _store(hub).newest_cursor() == TraceCursorRecord(CursorKey.opening(hub.clock.now()), 0, hub.clock.now())
    assert exporter.attempts == 0
    _sweep(hub).sweep()
    assert exporter.attempts == 0
    assert _row_count(hub) == 1


def test_closed_steps_are_told_in_total_order_and_the_cursor_moves_after_acceptance(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path, TracingConfig(settle_seconds=0, batch_limit=1))
    _sweep(hub).sweep()
    moved, stopped = _closed_pair(hub)
    tie = hub.clock.now()
    first, second = sorted([CursorKey(tie, moved, 1), CursorKey(tie, stopped, 1)])

    for _ in range(6):
        _sweep(hub).sweep()

    # One step a batch, in cursor order, each batch's cursor row written after it was accepted.
    assert [len({s.context.trace_id for s in batch}) for batch in exporter.batches] == [1, 1]
    assert _roots(exporter) == [_root(first.chunk_id), _root(second.chunk_id)]
    newest = _store(hub).newest_cursor()
    assert newest is not None
    assert newest.position == second
    assert newest.span_count == len(exporter.batches[1])


def test_a_step_inside_the_settle_window_waits(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path, TracingConfig(settle_seconds=300))
    _sweep(hub).sweep()
    _closed_pair(hub)

    hub.clock.advance(timedelta(seconds=299))
    _sweep(hub).sweep()
    assert exporter.attempts == 0

    hub.clock.advance(timedelta(seconds=1))
    _sweep(hub).sweep()
    assert len(_roots(exporter)) == 2


@pytest.mark.parametrize("mode", ["refuses", "raises"])
def test_a_failed_export_holds_the_cursor_backs_off_and_is_announced_once(tmp_path: Path, mode: str) -> None:
    hub, exporter = _hub(tmp_path)
    _sweep(hub).sweep()
    start = _store(hub).newest_cursor()
    _closed_pair(hub)
    exporter.fail = mode == "refuses"
    exporter.raises = mode == "raises"

    _sweep(hub).sweep()
    assert exporter.attempts == 1
    assert _store(hub).newest_cursor() == start
    assert _kinds(hub) == ["trace-export-failed"]

    # The first retry waits one interval, the next two: a sweep in between does not call the exporter.
    _sweep(hub).sweep()
    assert exporter.attempts == 1
    hub.clock.advance(timedelta(seconds=60))
    _sweep(hub).sweep()
    assert exporter.attempts == 2
    hub.clock.advance(timedelta(seconds=60))
    _sweep(hub).sweep()
    assert exporter.attempts == 2
    hub.clock.advance(timedelta(seconds=60))
    _sweep(hub).sweep()
    assert exporter.attempts == 3
    assert _kinds(hub) == ["trace-export-failed"]
    assert _store(hub).newest_cursor() == start

    exporter.fail = exporter.raises = False
    hub.clock.advance(timedelta(seconds=240))
    _sweep(hub).sweep()
    assert len(_roots(exporter)) == 2
    assert _kinds(hub) == ["trace-export-failed", "trace-export-recovered"]

    _closed_pair(hub, ref=10)
    _sweep(hub).sweep()
    assert len(_roots(exporter)) == 4
    assert _kinds(hub) == ["trace-export-failed", "trace-export-recovered"]


def test_the_backoff_stops_doubling_at_its_cap(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path, TracingConfig(settle_seconds=0, max_lag_seconds=86400))
    _sweep(hub).sweep()
    _closed_pair(hub)
    exporter.fail = True
    for _ in range(8):
        hub.clock.advance(BACKOFF_CAP)
        _sweep(hub).sweep()
    attempts = exporter.attempts

    hub.clock.advance(BACKOFF_CAP - timedelta(seconds=1))
    _sweep(hub).sweep()
    assert exporter.attempts == attempts
    hub.clock.advance(timedelta(seconds=1))
    _sweep(hub).sweep()
    assert exporter.attempts == attempts + 1


def test_a_restart_during_an_outage_does_not_announce_it_again(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path)
    _sweep(hub).sweep()
    _closed_pair(hub)
    exporter.fail = True
    _sweep(hub).sweep()
    assert _kinds(hub) == ["trace-export-failed"]

    restarted, again = _hub(tmp_path)
    restarted.clock.instant = hub.clock.now()
    again.fail = True
    _sweep(restarted).sweep()
    assert again.attempts == 1
    assert _kinds(restarted) == ["trace-export-failed"]

    again.fail = False
    restarted.clock.advance(timedelta(seconds=60))
    _sweep(restarted).sweep()
    assert len(_roots(again)) == 2
    assert _kinds(restarted) == ["trace-export-failed", "trace-export-recovered"]


def test_an_unsent_step_older_than_the_lag_cap_jumps_the_cursor_and_records_the_window(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path)
    _sweep(hub).sweep()
    start = _store(hub).newest_cursor()
    assert start is not None
    _closed_pair(hub)
    exporter.fail = True
    _sweep(hub).sweep()

    hub.clock.advance(timedelta(seconds=3600 + 60))
    exporter.fail = False
    _sweep(hub).sweep()

    boundary = hub.clock.now() - timedelta(seconds=3600)
    newest = _store(hub).newest_cursor()
    assert newest == TraceCursorRecord(CursorKey.opening(boundary), 0, hub.clock.now())
    assert exporter.batches == []
    assert _kinds(hub) == ["trace-export-failed", "trace-window-skipped"]
    detail = _skipped_detail(hub)
    assert detail["reason"] == "lag-cap"
    assert detail["since"]["at"] == start.position.at.isoformat()
    assert detail["until"] == boundary.isoformat()


def test_a_cursor_stale_only_because_the_fleet_was_idle_never_jumps(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path)
    _sweep(hub).sweep()

    hub.clock.advance(timedelta(days=3))
    _sweep(hub).sweep()

    assert _row_count(hub) == 1
    assert _kinds(hub) == []
    _closed_pair(hub)
    _sweep(hub).sweep()
    assert len(_roots(exporter)) == 2


def test_re_enabling_after_a_long_gap_jumps_to_now_and_records_what_it_skipped(tmp_path: Path) -> None:
    hub, _exporter = _hub(tmp_path)
    _sweep(hub).sweep()
    start = _store(hub).newest_cursor()
    assert start is not None
    _closed_pair(hub)  # closed while tracing was off again

    restarted, exporter = _hub(tmp_path)
    restarted.clock.instant = hub.clock.now()
    restarted.clock.advance(timedelta(seconds=3600 + 60))
    _sweep(restarted).sweep()

    assert _store(restarted).newest_cursor() == TraceCursorRecord(
        CursorKey.opening(restarted.clock.now()), 0, restarted.clock.now()
    )
    assert exporter.attempts == 0
    detail = _skipped_detail(restarted)
    assert detail["reason"] == "enable-after-gap"
    assert detail["since"]["at"] == start.position.at.isoformat()
    assert detail["until"] == restarted.clock.now().isoformat()


def test_a_gap_that_closed_nothing_jumps_without_an_event(tmp_path: Path) -> None:
    hub, _exporter = _hub(tmp_path)
    _sweep(hub).sweep()

    restarted, _again = _hub(tmp_path)
    restarted.clock.instant = hub.clock.now()
    restarted.clock.advance(timedelta(seconds=3600 + 60))
    _sweep(restarted).sweep()

    assert _row_count(restarted) == 2
    assert _kinds(restarted) == []


def test_a_hub_with_tracing_off_has_no_trace_sweep_and_writes_no_cursor(tmp_path: Path) -> None:
    hub, _graph = trace_hub(tmp_path)
    _closed_pair(hub)
    assert hub.services.trace_export is None
    assert hub.app is not None
    hub.app.state.shutdown = None

    sweeps = list(Sweep.all(hub.app))
    assert "blizzard.hub.trace_export" not in [s.logger_name for s in sweeps]
    for sweep in sweeps:
        sweep.reconciler.sweep()

    assert _row_count(hub) == 0


def test_a_hub_with_tracing_on_yields_the_trace_sweep(tmp_path: Path) -> None:
    hub, _exporter = _hub(tmp_path)
    assert hub.app is not None
    hub.app.state.shutdown = None

    traced = [s for s in Sweep.all(hub.app) if s.logger_name == "blizzard.hub.trace_export"]

    assert [s.reconciler for s in traced] == [hub.services.trace_export]
    assert traced[0].interval_seconds == _SETTLED.sweep_seconds


_OTEL_ENABLEMENT = (
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_TRACES_EXPORTER",
    "OTEL_SDK_DISABLED",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
)


def _refuse_construction(**_: object) -> None:
    raise AssertionError("no trace exporter may be built while tracing is off")


def test_the_hosted_app_builds_no_exporter_without_an_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _OTEL_ENABLEMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(hub_app, "OtlpTraceExporter", _refuse_construction)

    app = hub_app.build_hosted_app(hub_runtime.init_environment(tmp_path / "hub"))

    assert app.state.services.trace_export is None


def test_the_hosted_app_wires_the_sweep_when_an_endpoint_enables_tracing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in _OTEL_ENABLEMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    built: list[InMemoryTraceExporter] = []

    def construct(**_: object) -> InMemoryTraceExporter:
        built.append(InMemoryTraceExporter())
        return built[-1]

    monkeypatch.setattr(hub_app, "OtlpTraceExporter", construct)

    app = hub_app.build_hosted_app(hub_runtime.init_environment(tmp_path / "hub"))

    assert len(built) == 1
    assert app.state.services.trace_export is not None
