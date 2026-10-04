"""The lease trace sweep (component tier) — closed leases written through the runner store, told to the
in-memory exporter one ``sweep()`` at a time against a fixed clock."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.lane_retry import BACKOFF_CAP
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.trace_ids import DerivedContext, RunnerSpanRole, StepKey
from blizzard.runner.domain.tracing.cursor import LeaseCursorKey
from blizzard.runner.domain.tracing.repository import LeaseCursorRecord
from blizzard.runner.domain.tracing.sweep import LeaseTraceSweep, announce_rejected_tracing
from blizzard.wire.facts import EVENT_RECORDED
from tests import runner_trace_fixtures as fx
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store
from tests.runner_trace_leases import closed_lease
from tests.support import InMemoryTraceExporter

pytestmark = pytest.mark.component

_SETTLED = TracingConfig(settle_seconds=0, sweep_seconds=60, max_lag_seconds=3600)


class _Runner:
    """One runner process: its store, clock, exporter and sweep — a restart is a second one over the same file."""

    def __init__(self, tmp_path: Path, config: TracingConfig = _SETTLED, clock: FixedClock | None = None) -> None:
        self.store: SqlAlchemyRunnerStore = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
        self.clock = clock or FixedClock(fx.at(1000))
        self.exporter = InMemoryTraceExporter()
        self.sweep = LeaseTraceSweep(
            leases=self.store, outbound=self.store, exporter=self.exporter, clock=self.clock, config=config
        )
        self._leases = 0

    def close_lease(self, *, identified: bool = True) -> str:
        """A lease minted a minute ago and closed now."""
        self._leases += 1
        lease_id = f"lease-{self._leases:02d}-{self.clock.now().timestamp():.0f}"
        now = self.clock.now()
        closed_lease(self.store, lease_id, opened=now - timedelta(minutes=1), closed=now, identified=identified)
        return lease_id

    def events(self) -> list[dict]:  # type: ignore[type-arg]
        return [json.loads(f.payload) for f in self.store.pending_outbound() if f.kind == EVENT_RECORDED]

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.events()]

    def cursor_rows(self) -> int:
        with self.store._engine.connect() as conn:
            return conn.exec_driver_sql("SELECT count(*) FROM trace_cursor").scalar_one()


def _worker(lease_id: str) -> int:
    return DerivedContext.of(StepKey.attempt(f"ch-{lease_id}", 1), RunnerSpanRole.WORKER, lease_id).span_id


def test_the_first_pass_starts_the_cursor_at_now_and_exports_nothing(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.close_lease()  # closed before tracing was on: history is told only by replay
    runner.clock.advance(timedelta(seconds=1))

    runner.sweep.sweep()

    now = runner.clock.now()
    assert runner.store.newest_trace_cursor() == LeaseCursorRecord(LeaseCursorKey.opening(now), 0, now)
    assert runner.exporter.attempts == 0
    runner.sweep.sweep()
    assert runner.exporter.attempts == 0
    assert runner.cursor_rows() == 1


def test_closed_leases_are_told_in_key_order_and_the_cursor_moves_after_acceptance(tmp_path: Path) -> None:
    runner = _Runner(tmp_path, TracingConfig(settle_seconds=0, batch_limit=1))
    runner.sweep.sweep()
    runner.clock.advance(timedelta(seconds=1))
    first, second = runner.close_lease(), runner.close_lease()

    for _ in range(4):
        runner.sweep.sweep()

    assert [b[0].context.span_id for b in runner.exporter.batches] == [_worker(first), _worker(second)]
    newest = runner.store.newest_trace_cursor()
    assert newest is not None
    assert newest.position == LeaseCursorKey(runner.clock.now(), second)
    assert newest.span_count == len(runner.exporter.batches[1])


def test_a_lease_inside_the_settle_window_waits(tmp_path: Path) -> None:
    runner = _Runner(tmp_path, TracingConfig(settle_seconds=300))
    runner.sweep.sweep()
    runner.clock.advance(timedelta(seconds=1))
    lease_id = runner.close_lease()

    runner.clock.advance(timedelta(seconds=299))
    runner.sweep.sweep()
    assert runner.exporter.attempts == 0

    runner.clock.advance(timedelta(seconds=1))
    runner.sweep.sweep()
    assert runner.exporter.batches[0][0].context.span_id == _worker(lease_id)


@pytest.mark.parametrize("mode", ["refuses", "raises"])
def test_a_failed_export_holds_the_cursor_backs_off_and_is_announced_once(tmp_path: Path, mode: str) -> None:
    runner = _Runner(tmp_path)
    exporter = runner.exporter
    runner.sweep.sweep()
    start = runner.store.newest_trace_cursor()
    runner.clock.advance(timedelta(seconds=1))
    runner.close_lease()
    exporter.fail = mode == "refuses"
    exporter.raises = mode == "raises"

    runner.sweep.sweep()
    assert exporter.attempts == 1
    assert runner.store.newest_trace_cursor() == start
    assert runner.kinds() == ["trace-export-failed"]

    # The first retry waits one interval, the next two: a sweep in between does not call the exporter.
    runner.sweep.sweep()
    assert exporter.attempts == 1
    runner.clock.advance(timedelta(seconds=60))
    runner.sweep.sweep()
    assert exporter.attempts == 2
    runner.clock.advance(timedelta(seconds=60))
    runner.sweep.sweep()
    assert exporter.attempts == 2
    runner.clock.advance(timedelta(seconds=60))
    runner.sweep.sweep()
    assert exporter.attempts == 3
    assert runner.kinds() == ["trace-export-failed"]
    assert runner.store.newest_trace_cursor() == start

    exporter.fail = exporter.raises = False
    runner.clock.advance(timedelta(seconds=240))
    runner.sweep.sweep()
    assert len(exporter.batches) == 1
    assert runner.kinds() == ["trace-export-failed", "trace-export-recovered"]

    runner.close_lease()
    runner.sweep.sweep()
    assert len(exporter.batches) == 2
    assert runner.kinds() == ["trace-export-failed", "trace-export-recovered"]


def test_the_backoff_stops_doubling_at_its_cap(tmp_path: Path) -> None:
    runner = _Runner(tmp_path, TracingConfig(settle_seconds=0, max_lag_seconds=86400))
    runner.sweep.sweep()
    runner.clock.advance(timedelta(seconds=1))
    runner.close_lease()
    runner.exporter.fail = True
    for _ in range(8):
        runner.clock.advance(BACKOFF_CAP)
        runner.sweep.sweep()
    attempts = runner.exporter.attempts

    runner.clock.advance(BACKOFF_CAP - timedelta(seconds=1))
    runner.sweep.sweep()
    assert runner.exporter.attempts == attempts
    runner.clock.advance(timedelta(seconds=1))
    runner.sweep.sweep()
    assert runner.exporter.attempts == attempts + 1


def test_a_restart_during_an_outage_does_not_announce_it_again(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.sweep.sweep()
    runner.clock.advance(timedelta(seconds=1))
    runner.close_lease()
    runner.exporter.fail = True
    runner.sweep.sweep()
    assert runner.kinds() == ["trace-export-failed"]

    restarted = _Runner(tmp_path, clock=FixedClock(runner.clock.now()))
    restarted.exporter.fail = True
    restarted.sweep.sweep()
    assert restarted.exporter.attempts == 1
    assert restarted.kinds() == ["trace-export-failed"]

    restarted.exporter.fail = False
    restarted.clock.advance(timedelta(seconds=60))
    restarted.sweep.sweep()
    assert len(restarted.exporter.batches) == 1
    assert restarted.kinds() == ["trace-export-failed", "trace-export-recovered"]


def test_an_unsent_lease_older_than_the_lag_cap_jumps_the_cursor_and_records_the_window(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.sweep.sweep()
    start = runner.store.newest_trace_cursor()
    assert start is not None
    runner.clock.advance(timedelta(seconds=1))
    runner.close_lease()
    runner.exporter.fail = True
    runner.sweep.sweep()

    runner.clock.advance(timedelta(seconds=3600 + 60))
    runner.exporter.fail = False
    runner.sweep.sweep()

    boundary = runner.clock.now() - timedelta(seconds=3600)
    now = runner.clock.now()
    assert runner.store.newest_trace_cursor() == LeaseCursorRecord(LeaseCursorKey.opening(boundary), 0, now)
    assert runner.exporter.batches == []
    assert runner.kinds() == ["trace-export-failed", "trace-window-skipped"]
    detail = runner.events()[1]["detail"]
    assert detail["reason"] == "lag-cap"
    assert detail["since"] == {"at": start.position.at.isoformat(), "lease_id": ""}
    assert detail["until"] == boundary.isoformat()


def test_a_cursor_stale_only_because_the_runner_was_idle_never_jumps(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.sweep.sweep()

    runner.clock.advance(timedelta(days=3))
    runner.sweep.sweep()

    assert runner.cursor_rows() == 1
    assert runner.kinds() == []
    runner.close_lease()
    runner.sweep.sweep()
    assert len(runner.exporter.batches) == 1


def test_re_enabling_after_a_long_gap_jumps_to_now_and_records_what_it_skipped(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.sweep.sweep()
    start = runner.store.newest_trace_cursor()
    assert start is not None
    runner.clock.advance(timedelta(seconds=1))
    runner.close_lease()  # closed while tracing was off again

    restarted = _Runner(tmp_path, clock=FixedClock(runner.clock.now() + timedelta(seconds=3600 + 60)))
    restarted.sweep.sweep()

    now = restarted.clock.now()
    assert restarted.store.newest_trace_cursor() == LeaseCursorRecord(LeaseCursorKey.opening(now), 0, now)
    assert restarted.exporter.attempts == 0
    [skipped] = restarted.events()
    assert skipped["kind"] == "trace-window-skipped"
    assert skipped["detail"]["reason"] == "enable-after-gap"
    assert skipped["detail"]["since"]["at"] == start.position.at.isoformat()
    assert skipped["detail"]["until"] == now.isoformat()


def test_a_gap_that_closed_nothing_jumps_without_an_event(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.sweep.sweep()

    restarted = _Runner(tmp_path, clock=FixedClock(runner.clock.now() + timedelta(seconds=3600 + 60)))
    restarted.sweep.sweep()

    assert restarted.cursor_rows() == 2
    assert restarted.kinds() == []


def test_a_window_of_leases_that_tell_nothing_advances_without_an_export(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.sweep.sweep()
    runner.clock.advance(timedelta(seconds=1))
    lease_id = runner.close_lease(identified=False)

    runner.sweep.sweep()

    assert runner.exporter.attempts == 0
    assert runner.store.newest_trace_cursor() == LeaseCursorRecord(
        LeaseCursorKey(runner.clock.now(), lease_id), 0, runner.clock.now()
    )
    assert runner.kinds() == []


def test_every_trace_event_rides_the_outbound_buffer_as_a_runner_wide_event(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.sweep.sweep()
    runner.clock.advance(timedelta(seconds=1))
    runner.close_lease()
    runner.exporter.fail = True
    runner.sweep.sweep()
    runner.clock.advance(timedelta(seconds=3600 + 60))
    runner.close_lease()
    runner.sweep.sweep()  # lag-cap jump past the held lease
    runner.exporter.fail = False
    runner.clock.advance(timedelta(seconds=1))
    runner.close_lease()
    runner.sweep.sweep()
    rejected = TracingSettings.of(
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:4318", "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}
    )
    announce_rejected_tracing(rejected, runner.store, runner.clock.now())
    announce_rejected_tracing(TracingSettings("disabled"), runner.store, runner.clock.now())

    facts = [f for f in runner.store.pending_outbound() if f.kind == EVENT_RECORDED]
    assert [(f.chunk_id, f.lease_id) for f in facts] == [(None, None)] * 4
    events = runner.events()
    assert [e["kind"] for e in events] == [
        "trace-export-failed",
        "trace-window-skipped",
        "trace-export-recovered",
        "trace-config-rejected",
    ]
    assert [e["severity"] for e in events] == ["warning", "warning", "info", "warning"]
    assert all(e["chunk_id"] is None and e["lease_id"] is None and e["node_name"] is None for e in events)
    assert events[3]["detail"] == {"setting": "OTEL_EXPORTER_OTLP_PROTOCOL", "value": "grpc"}
    assert "runner" in events[3]["message"]
