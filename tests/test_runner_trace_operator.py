"""The runner's trace operator surface (component tier): status and replay over the real routes and CLI verbs,
against closed leases written through the runner store and the in-memory exporter."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.harness_telemetry_outcome import HarnessTelemetryOutcome
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.app import create_app
from blizzard.runner.cli.traces import harness_telemetry_lines, traces_group
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.tracing.receiver_limits import ReceiverCounter
from blizzard.runner.domain.tracing.replay import LeaseTraceReplay, ReplayUnavailable, ReplayWindowRefused
from blizzard.runner.domain.tracing.status import LeaseTraceStatusReader
from blizzard.runner.domain.tracing.sweep import LeaseTraceSweep
from blizzard.runner.harness.harness_telemetry import HarnessTelemetryPlan
from tests import runner_trace_fixtures as fx
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store
from tests.runner_trace_leases import closed_lease
from tests.support import InMemoryTraceExporter

pytestmark = pytest.mark.component

_CONFIG = TracingConfig(settle_seconds=0, sweep_seconds=60, max_lag_seconds=3600, replay_max_window=3600)
_SECRET = "https://user:hunter2@collector.example:4318/v1/traces?key=abc"


def _env(enabled: bool) -> dict[str, str]:
    return {"OTEL_EXPORTER_OTLP_ENDPOINT": _SECRET} if enabled else {}


class _Runner:
    def __init__(self, tmp_path: Path, *, on: bool = True) -> None:
        self.store: SqlAlchemyRunnerStore = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
        self.clock = FixedClock(fx.at(1000))
        self.exporter = InMemoryTraceExporter()
        self.settings = TracingSettings.of(_env(on))
        self.sweep = LeaseTraceSweep(
            leases=self.store, outbound=self.store, exporter=self.exporter, clock=self.clock, config=_CONFIG
        )
        self.replayer = LeaseTraceReplay(leases=self.store, exporter=self.exporter if on else None, config=_CONFIG)
        self.status = LeaseTraceStatusReader(
            settings=self.settings, leases=self.store, clock=self.clock, replay_max_window=_CONFIG.replay_max_window
        )
        self._n = 0

    def close_lease(self) -> str:
        self._n += 1
        lease_id = f"lease-{self._n:02d}"
        now = self.clock.now()
        closed_lease(self.store, lease_id, opened=now - timedelta(minutes=1), closed=now)
        return lease_id

    def window(self, back: int = 60) -> tuple[datetime, datetime]:
        now = self.clock.now()
        return now - timedelta(seconds=back), now + timedelta(seconds=1)


def _client(runner: _Runner, tmp_path: Path) -> TestClient:
    app = create_app(_config(tmp_path), runner_stores=None, trace_status=runner.status, trace_replay=runner.replayer)
    return TestClient(app)


def _config(tmp_path: Path) -> RunnerConfig:
    return RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url="http://127.0.0.1:9")


def test_replay_ids_match_the_live_sweep_and_the_cursor_does_not_move(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.sweep.sweep()
    runner.clock.advance(timedelta(seconds=1))
    runner.close_lease()
    runner.sweep.sweep()
    live = [s.context.span_id for b in runner.exporter.batches for s in b]
    cursor = runner.store.newest_trace_cursor()
    runner.exporter.batches.clear()

    since, until = runner.window()
    result = runner.replayer.replay(since, until, dry_run=False)

    assert [s.context.span_id for b in runner.exporter.batches for s in b] == live
    assert result.leases == 1 and result.spans == len(live) and not result.failed
    assert runner.store.newest_trace_cursor() == cursor


def test_a_dry_run_counts_and_sends_nothing(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.close_lease()
    runner.close_lease()
    since, until = runner.window()

    result = runner.replayer.replay(since, until, dry_run=True)

    assert (result.leases, result.dry_run) == (2, True) and result.spans > 0
    assert runner.exporter.attempts == 0


def test_a_window_is_half_open_and_pages_by_the_batch_limit(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.replayer = LeaseTraceReplay(
        leases=runner.store, exporter=runner.exporter, config=TracingConfig(batch_limit=1, replay_max_window=3600)
    )
    runner.close_lease()
    runner.clock.advance(timedelta(seconds=10))
    runner.close_lease()
    closed_at = runner.clock.now()

    result = runner.replayer.replay(closed_at - timedelta(seconds=30), closed_at, dry_run=False)

    assert (result.leases, result.batches) == (1, 1)  # `until` itself is excluded


def test_an_over_long_or_inverted_window_is_refused_and_a_wet_replay_needs_an_exporter(tmp_path: Path) -> None:
    runner = _Runner(tmp_path, on=False)
    now = runner.clock.now()
    with pytest.raises(ReplayWindowRefused, match="replay_max_window"):
        runner.replayer.replay(now - timedelta(seconds=3601), now, dry_run=True)
    with pytest.raises(ReplayWindowRefused, match="after since"):
        runner.replayer.replay(now, now, dry_run=True)
    with pytest.raises(ReplayUnavailable):
        runner.replayer.replay(now - timedelta(seconds=1), now, dry_run=False)


def test_a_refusing_exporter_stops_the_replay_with_what_it_accepted(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.close_lease()
    runner.exporter.fail = True
    since, until = runner.window()

    result = runner.replayer.replay(since, until, dry_run=False)

    assert result.failed and (result.leases, result.spans, result.batches) == (0, 0, 0)


def test_status_reports_the_origin_only_the_cursor_lag_export_and_failure(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.sweep.sweep()
    runner.clock.advance(timedelta(seconds=1))
    runner.close_lease()
    runner.sweep.sweep()
    runner.clock.advance(timedelta(seconds=5))
    runner.close_lease()
    runner.exporter.fail = True
    runner.sweep.sweep()

    read = runner.status.read()

    assert read.state == "enabled"
    assert read.endpoint == "https://collector.example:4318"
    assert read.last_export_span_count and read.last_export_at is not None
    assert read.last_error_ongoing and read.last_error_at is not None
    assert read.lag_seconds == 0.0
    assert read.cursor_at is not None
    assert "hunter2" not in repr(read) and "/v1/traces" not in repr(read)


def test_status_when_tracing_is_off_reads_the_settings_only(tmp_path: Path) -> None:
    read = _Runner(tmp_path, on=False).status.read()
    assert (read.state, read.endpoint, read.cursor_at, read.lag_seconds, read.last_error_at) == (
        "disabled",
        None,
        None,
        None,
        None,
    )


def test_the_routes_serve_and_refuse_an_unwired_runner(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.close_lease()
    since, until = runner.window()
    body = {"since": since.isoformat(), "until": until.isoformat(), "dry_run": True}

    with _client(runner, tmp_path) as client:
        ok = client.get("/api/traces/status")
        told = client.post("/api/traces/replay", json=body)
        bad = client.post(
            "/api/traces/replay",
            json={"since": since.isoformat(), "until": (since + timedelta(days=2)).isoformat()},
        )

    assert ok.status_code == 200 and ok.json()["endpoint"] == "https://collector.example:4318"
    assert told.status_code == 200 and told.json()["leases"] == 1 and told.json()["dry_run"] is True
    assert bad.status_code == 422
    unwired = TestClient(create_app(_config(tmp_path)))
    assert unwired.get("/api/traces/status").status_code == 503
    assert unwired.post("/api/traces/replay", json=body).status_code == 503


def test_the_verbs_are_listed_under_runner_help() -> None:
    from blizzard.runner.cli import runner

    result = CliRunner().invoke(runner, ["traces", "--help"])
    assert result.exit_code == 0
    assert "status" in result.output and "replay" in result.output
    assert "traces" in CliRunner().invoke(runner, ["--help"]).output


def test_cli_status_and_replay_are_pure_clients(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _Runner(tmp_path)
    runner.close_lease()
    from blizzard.runner.cli import daemon as daemon_module

    def reach(verb: str, directory: str, runner_url: str | None) -> daemon_module.RunnerDaemon:
        return daemon_module.RunnerDaemon(verb, _client(runner, tmp_path), "test")  # type: ignore[arg-type]

    monkeypatch.setattr(daemon_module.RunnerDaemon, "reach", staticmethod(reach))
    since = (runner.clock.now() - timedelta(seconds=60)).astimezone().replace(tzinfo=None)
    until = (runner.clock.now() + timedelta(seconds=1)).astimezone().replace(tzinfo=None)

    shown = CliRunner().invoke(traces_group, ["status"])
    replayed = CliRunner().invoke(
        traces_group, ["replay", "--since", since.isoformat(), "--until", until.isoformat(), "--dry-run"]
    )

    assert shown.exit_code == 0, shown.output
    assert "exporting to https://collector.example:4318" in shown.output and "hunter2" not in shown.output
    assert replayed.exit_code == 0, replayed.output
    assert "would tell 1 leases" in replayed.output


def _reach_the_runner(runner: _Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from blizzard.runner.cli import daemon as daemon_module

    def reach(verb: str, directory: str, runner_url: str | None) -> daemon_module.RunnerDaemon:
        return daemon_module.RunnerDaemon(verb, _client(runner, tmp_path), "test")  # type: ignore[arg-type]

    monkeypatch.setattr(daemon_module.RunnerDaemon, "reach", staticmethod(reach))


def _local(at: datetime) -> str:
    return at.astimezone().replace(tzinfo=None).isoformat()


def test_cli_splits_a_wide_range_into_windows_and_reports_each(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _Runner(tmp_path)
    runner.close_lease()
    _reach_the_runner(runner, tmp_path, monkeypatch)
    until = runner.clock.now() + timedelta(seconds=1)
    since = until - timedelta(seconds=3600 * 2 + 1800)

    result = CliRunner().invoke(
        traces_group, ["replay", "--since", _local(since), "--until", _local(until), "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert "window 1 of 3" in result.output and "window 3 of 3" in result.output
    assert "would tell 1 leases" in result.output


def test_cli_stops_on_a_failing_window_and_names_where_to_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _Runner(tmp_path)
    runner.close_lease()
    runner.exporter.fail = True
    _reach_the_runner(runner, tmp_path, monkeypatch)
    until = runner.clock.now() + timedelta(seconds=1)
    since = until - timedelta(seconds=3600 * 2 + 1800)
    failing = since + timedelta(seconds=3600 * 2)

    result = CliRunner().invoke(traces_group, ["replay", "--since", _local(since), "--until", _local(until)])

    assert result.exit_code != 0
    assert "window 3 of 3" in result.output
    assert f"resume with --since {failing.astimezone().strftime('%Y-%m-%dT%H:%M:%S')}" in result.output


@pytest.mark.parametrize("failure", ["timeout", "unmapped status"])
def test_cli_names_where_to_resume_when_a_window_request_itself_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from blizzard.runner.cli import daemon as daemon_module

    runner = _Runner(tmp_path)
    client = _client(runner, tmp_path)

    def post(url: str, **kwargs: object) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timed out")
        return httpx.Response(500, json={}, request=httpx.Request("POST", url))

    def reach(verb: str, directory: str, runner_url: str | None) -> daemon_module.RunnerDaemon:
        return daemon_module.RunnerDaemon(verb, client, "test")  # type: ignore[arg-type]

    monkeypatch.setattr(daemon_module.RunnerDaemon, "reach", staticmethod(reach))
    monkeypatch.setattr(client, "post", post)
    until = runner.clock.now() + timedelta(seconds=1)
    since = until - timedelta(seconds=3600 + 1800)

    result = CliRunner().invoke(traces_group, ["replay", "--since", _local(since), "--until", _local(until)])

    assert result.exit_code != 0
    assert "window 1 of 2" in result.output
    assert f"resume with --since {since.astimezone().strftime('%Y-%m-%dT%H:%M:%S')}" in result.output


_PLAN = HarnessTelemetryPlan(
    traces=HarnessTelemetryOutcome.CAPTURED,
    metrics=HarnessTelemetryOutcome.OPERATOR_CONFIGURED,
    logs=HarnessTelemetryOutcome.NO_RUNNER_DESTINATION,
)


def _reader_with_plan(runner: _Runner, plan: HarnessTelemetryPlan | None) -> LeaseTraceStatusReader:
    spans, points = ReceiverCounter(), ReceiverCounter()
    spans.record(accepted=3, dropped=1)
    points.record(accepted=7, dropped=0)
    return LeaseTraceStatusReader(
        settings=runner.settings,
        leases=runner.store,
        clock=runner.clock,
        receiver=ReceiverCounter(),
        harness_telemetry=plan,
        claude_trace_receiver=spans,
        metric_receiver=points,
        log_receiver=ReceiverCounter(),
    )


def test_status_carries_the_plan_and_each_signals_receiver_count(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    harness = _reader_with_plan(runner, _PLAN).read().harness_telemetry

    assert harness is not None and harness.plan == _PLAN
    counts = {signal.value: (c.accepted, c.dropped) for signal, c in harness.receivers.items()}
    assert counts == {"traces": (3, 1), "metrics": (7, 0), "logs": (0, 0)}
    assert _reader_with_plan(runner, None).read().harness_telemetry is None


def test_the_status_route_serves_the_runner_only_harness_telemetry_block(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.status = _reader_with_plan(runner, _PLAN)

    with _client(runner, tmp_path) as client:
        body = client.get("/api/traces/status").json()

    assert body["harness_telemetry"] == {
        "traces": {"outcome": "captured", "accepted": 3, "dropped": 1},
        "metrics": {"outcome": "operator_configured", "accepted": 7, "dropped": 0},
        "logs": {"outcome": "no_runner_destination", "accepted": 0, "dropped": 0},
    }


def test_the_status_route_omits_the_block_where_no_plan_is_wired(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    with _client(runner, tmp_path) as client:
        assert client.get("/api/traces/status").json()["harness_telemetry"] is None


def test_the_verb_names_each_signals_outcome(tmp_path: Path) -> None:
    runner = _Runner(tmp_path)
    runner.status = _reader_with_plan(runner, _PLAN)
    body = _client(runner, tmp_path).get("/api/traces/status").json()

    assert harness_telemetry_lines(body["harness_telemetry"]) == [
        "harness telemetry:",
        "  traces: captured  (3 spans accepted, 1 dropped)",
        "  metrics: operator-configured (not captured)",
        "  logs: no runner destination (not captured)",
    ]
    off = harness_telemetry_lines(
        {s: {"outcome": "off", "accepted": 0, "dropped": 0} for s in ("traces", "metrics", "logs")}
    )
    assert off == ["harness telemetry: off"]
    assert harness_telemetry_lines(None) == []
