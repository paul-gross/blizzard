"""``POST /api/traces/replay`` and ``blizzard hub traces replay`` — the window told again through the live
sweep's assembly, with the live cursor untouched."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
import pytest
import sqlalchemy as sa
from click.testing import CliRunner

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.domain.observability.tracing.replay import ReplayUnavailable, ReplayWindowRefused, TraceReplay
from blizzard.hub.domain.observability.tracing.repository import IReadTraceSteps
from blizzard.hub.store import schema
from tests.support import HubHarness, InMemoryTraceExporter
from tests.test_trace_export_sweep import _closed_pair, _sweep
from tests.trace_hub import trace_hub

_T0 = datetime(2026, 7, 13, tzinfo=UTC)
_CONFIG = TracingConfig(settle_seconds=0, sweep_seconds=60, max_lag_seconds=3600, replay_max_window=3600)


# --- the window's bounds (unit tier) -----------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("since", "until", "named"),
    [
        (_T0, _T0, "until must be after since"),
        (_T0, _T0 - timedelta(seconds=1), "until must be after since"),
        (_T0, _T0 + timedelta(seconds=3601), "replay_max_window (3600 seconds)"),
    ],
)
def test_a_bad_window_is_refused_naming_its_bound(since: datetime, until: datetime, named: str) -> None:
    replay = TraceReplay(
        steps=cast(IReadTraceSteps, object()), exporter=None, clock=FixedClock(_T0 + timedelta(days=1)), config=_CONFIG
    )
    with pytest.raises(ReplayWindowRefused, match=named.replace("(", r"\(").replace(")", r"\)")):
        replay.replay(since, until, dry_run=True)


@pytest.mark.unit
def test_a_wet_replay_with_no_exporter_is_unavailable() -> None:
    replay = TraceReplay(
        steps=cast(IReadTraceSteps, object()), exporter=None, clock=FixedClock(_T0 + timedelta(days=1)), config=_CONFIG
    )
    with pytest.raises(ReplayUnavailable):
        replay.replay(_T0, _T0 + timedelta(seconds=1), dry_run=False)


# --- over the real routes (component tier) -----------------------------------------------------


def _hub(
    tmp_path: Path, config: TracingConfig = _CONFIG, *, on: bool = True
) -> tuple[HubHarness, InMemoryTraceExporter]:
    exporter = InMemoryTraceExporter()
    hub, _graph = trace_hub(
        tmp_path,
        trace_exporter=exporter if on else None,
        tracing=config,
        tracing_settings=TracingSettings.of({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}),
    )
    return hub, exporter


def _window(hub: HubHarness, **extra: object) -> dict[str, object]:
    """The window from ``_T0`` through just past now, with the clock then moved past its end."""
    until = hub.clock.now() + timedelta(seconds=1)
    hub.clock.advance(timedelta(seconds=1))
    return {"since": _T0.isoformat(), "until": until.isoformat(), **extra}


def _cursor_rows(hub: HubHarness) -> list[tuple]:  # type: ignore[type-arg]
    with hub.engine.connect() as conn:
        return [tuple(r) for r in conn.execute(sa.select(schema.trace_cursor).order_by(schema.trace_cursor.c.id))]


def _event_count(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        return conn.execute(sa.select(sa.func.count()).select_from(schema.event_log)).scalar_one()


def _told_live(hub: HubHarness) -> None:
    _sweep(hub).sweep()
    _closed_pair(hub)
    for _ in range(4):
        _sweep(hub).sweep()


@pytest.mark.component
def test_replay_tells_the_same_ids_as_the_live_sweep_and_leaves_the_cursor_alone(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path)
    _told_live(hub)
    live = sorted(s.context.span_id for s in exporter.spans)
    rows, events = _cursor_rows(hub), _event_count(hub)
    assert live

    resp = hub.client.post("/api/traces/replay", json=_window(hub))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"steps": 2, "chunks": 1, "spans": len(live), "batches": 1, "dry_run": False}
    assert sorted(s.context.span_id for s in exporter.batches[-1]) == live
    assert _cursor_rows(hub) == rows
    assert _event_count(hub) == events


@pytest.mark.component
def test_status_names_the_widest_window_a_replay_request_may_cover(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path)

    assert hub.client.get("/api/traces/status").json()["replay_max_window_seconds"] == 3600


@pytest.mark.component
def test_a_window_wider_than_the_batch_limit_is_told_in_full(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path, TracingConfig(settle_seconds=0, batch_limit=1, replay_max_window=3600))
    _told_live(hub)
    before = len(exporter.batches)
    live = sorted(s.context.span_id for s in exporter.spans)

    body = hub.client.post("/api/traces/replay", json=_window(hub)).json()

    assert (body["steps"], body["chunks"], body["batches"]) == (2, 1, 3)
    assert sorted(s.context.span_id for b in exporter.batches[before:] for s in b) == live


@pytest.mark.component
def test_the_window_is_half_open(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path)
    _told_live(hub)
    closed = hub.clock.now()
    hub.clock.advance(timedelta(seconds=1))

    at_close = {"since": _T0.isoformat(), "until": closed.isoformat()}
    assert hub.client.post("/api/traces/replay", json=at_close).json()["steps"] == 0
    from_close = {"since": closed.isoformat(), "until": (closed + timedelta(seconds=1)).isoformat()}
    assert hub.client.post("/api/traces/replay", json=from_close).json()["steps"] == 2


@pytest.mark.component
def test_a_dry_run_counts_and_exports_nothing_even_with_tracing_off(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path, on=False)
    _closed_pair(hub)

    resp = hub.client.post("/api/traces/replay", json=_window(hub, dry_run=True))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["steps"] == 2
    assert body["spans"] > 0
    assert body["dry_run"] is True
    assert exporter.attempts == 0


@pytest.mark.component
def test_a_wet_replay_with_tracing_off_is_a_conflict(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path, on=False)
    assert hub.client.post("/api/traces/replay", json=_window(hub)).status_code == 409


@pytest.mark.component
def test_an_inverted_or_oversized_window_is_unprocessable_naming_the_limit(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path)
    inverted = {"since": _T0.isoformat(), "until": (_T0 - timedelta(seconds=1)).isoformat()}
    assert hub.client.post("/api/traces/replay", json=inverted).status_code == 422
    wide = {"since": _T0.isoformat(), "until": (_T0 + timedelta(seconds=3601)).isoformat()}
    resp = hub.client.post("/api/traces/replay", json=wide)
    assert resp.status_code == 422
    assert "replay_max_window" in resp.json()["detail"]
    assert "3600" in resp.json()["detail"]


@pytest.mark.component
@pytest.mark.parametrize("mode", ["refuses", "raises"])
def test_an_exporter_failure_is_a_bad_gateway_with_the_partial_counts(tmp_path: Path, mode: str) -> None:
    hub, exporter = _hub(tmp_path, TracingConfig(settle_seconds=0, batch_limit=1, replay_max_window=3600))
    _told_live(hub)
    rows = _cursor_rows(hub)
    accepted = len(exporter.batches)
    original = exporter.export

    def second_fails(spans):  # type: ignore[no-untyped-def]
        if exporter.attempts > accepted:
            exporter.fail, exporter.raises = mode == "refuses", mode == "raises"
        return original(spans)

    exporter.export = second_fails  # type: ignore[method-assign]
    resp = hub.client.post("/api/traces/replay", json=_window(hub))

    assert resp.status_code == 502
    body = resp.json()
    assert (body["steps"] + body["chunks"], body["batches"]) == (1, 1)
    assert body["spans"] > 0
    assert _cursor_rows(hub) == rows


def _relay(hub: HubHarness, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Route the CLI's module-level ``httpx`` calls to the hub's test client; returns each replay request's body."""
    bodies: list[dict[str, object]] = []

    def post(url: str, *, json: dict[str, object], timeout: float, **_: object) -> httpx.Response:
        assert timeout > 15.0
        bodies.append(json)
        return hub.client.post(url, json=json)

    def get(url: str, **_: object) -> httpx.Response:
        return hub.client.get(url)

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(httpx, "get", get)
    return bodies


def _local(at: datetime) -> str:
    return at.astimezone().replace(tzinfo=None).isoformat()


_ENV = {"BZ_HUB_URL": "http://hub.local:8421"}


@pytest.mark.component
def test_the_cli_is_a_pure_client_and_renders_both_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, _ = _hub(tmp_path)
    _told_live(hub)
    bodies = _relay(hub, monkeypatch)
    since, until = _local(_T0), _local(hub.clock.now() + timedelta(seconds=1))
    hub.clock.advance(timedelta(seconds=1))

    dry = CliRunner().invoke(hub_group, ["traces", "replay", "--since", since, "--until", until, "--dry-run"], env=_ENV)
    assert dry.exit_code == 0, dry.output
    assert "would tell 2 steps, 1 chunks" in dry.output
    wet = CliRunner().invoke(hub_group, ["traces", "replay", "--since", since, "--until", until], env=_ENV)
    assert wet.exit_code == 0, wet.output
    assert "told 2 steps, 1 chunks" in wet.output
    assert len(bodies) == 2


@pytest.mark.component
def test_the_cli_splits_a_range_wider_than_the_limit_and_reports_each_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, exporter = _hub(tmp_path)
    _told_live(hub)
    live = sorted(s.context.span_id for s in exporter.spans)
    before = len(exporter.batches)
    bodies = _relay(hub, monkeypatch)
    end = hub.clock.now() + timedelta(seconds=1)
    hub.clock.advance(timedelta(seconds=1))
    start = end - timedelta(seconds=3600 * 2 + 1800)

    result = CliRunner().invoke(
        hub_group, ["traces", "replay", "--since", _local(start), "--until", _local(end)], env=_ENV
    )

    assert result.exit_code == 0, result.output
    windows = [(datetime.fromisoformat(str(b["since"])), datetime.fromisoformat(str(b["until"]))) for b in bodies]
    assert len(windows) == 3
    assert all(stop - begin <= timedelta(seconds=3600) for begin, stop in windows)
    assert [w[1] for w in windows[:-1]] == [w[0] for w in windows[1:]]
    assert (windows[0][0], windows[-1][1]) == (start, end)
    assert "window 1 of 3" in result.output and "window 3 of 3" in result.output
    assert "told 2 steps, 1 chunks" in result.output
    assert sorted(s.context.span_id for b in exporter.batches[before:] for s in b) == live


@pytest.mark.component
def test_the_cli_stops_on_a_failing_window_and_names_where_to_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub, exporter = _hub(tmp_path)
    _told_live(hub)
    bodies = _relay(hub, monkeypatch)
    exporter.fail = True
    end = hub.clock.now() + timedelta(seconds=1)
    hub.clock.advance(timedelta(seconds=1))
    start = end - timedelta(seconds=3600 * 3 + 1800)
    # The told spans sit in the last of four windows; the three before it are empty and pass.
    failing_start = start + timedelta(seconds=3600 * 3)

    result = CliRunner().invoke(
        hub_group, ["traces", "replay", "--since", _local(start), "--until", _local(end)], env=_ENV
    )

    assert result.exit_code != 0
    assert len(bodies) == 4
    assert "window 4 of 4" in result.output
    assert f"resume with --since {failing_start.astimezone().strftime('%Y-%m-%dT%H:%M:%S')}" in result.output


@pytest.mark.component
@pytest.mark.parametrize("failure", ["transport", "unmapped status"])
def test_the_cli_names_where_to_resume_when_a_window_request_itself_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    hub, _ = _hub(tmp_path)
    _relay(hub, monkeypatch)

    def post(url: str, **_: object) -> httpx.Response:
        if failure == "transport":
            raise httpx.ReadTimeout("timed out")
        return httpx.Response(500, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    end = hub.clock.now() + timedelta(seconds=1)
    hub.clock.advance(timedelta(seconds=1))
    start = end - timedelta(seconds=3600 + 1800)

    result = CliRunner().invoke(
        hub_group, ["traces", "replay", "--since", _local(start), "--until", _local(end)], env=_ENV
    )

    assert result.exit_code != 0
    assert "window 1 of 2" in result.output
    assert f"resume with --since {start.astimezone().strftime('%Y-%m-%dT%H:%M:%S')}" in result.output
