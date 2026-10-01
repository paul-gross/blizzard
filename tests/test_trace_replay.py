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

from blizzard.hub.cli import hub as hub_group
from blizzard.hub.config import TracingConfig
from blizzard.hub.domain.tracing.replay import ReplayUnavailable, ReplayWindowRefused, TraceReplay
from blizzard.hub.domain.tracing.repository import IReadTraceSteps
from blizzard.hub.store import schema
from blizzard.hub.trace_export.settings import TracingSettings
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
    replay = TraceReplay(steps=cast(IReadTraceSteps, object()), exporter=None, config=_CONFIG)
    with pytest.raises(ReplayWindowRefused, match=named.replace("(", r"\(").replace(")", r"\)")):
        replay.replay(since, until, dry_run=True)


@pytest.mark.unit
def test_a_wet_replay_with_no_exporter_is_unavailable() -> None:
    replay = TraceReplay(steps=cast(IReadTraceSteps, object()), exporter=None, config=_CONFIG)
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
    return {"since": _T0.isoformat(), "until": (hub.clock.now() + timedelta(seconds=1)).isoformat(), **extra}


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
    assert body == {"steps": 2, "spans": len(live), "batches": 1, "dry_run": False}
    assert sorted(s.context.span_id for s in exporter.batches[-1]) == live
    assert _cursor_rows(hub) == rows
    assert _event_count(hub) == events


@pytest.mark.component
def test_a_window_wider_than_the_batch_limit_is_told_in_full(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path, TracingConfig(settle_seconds=0, batch_limit=1, replay_max_window=3600))
    _told_live(hub)
    before = len(exporter.batches)
    live = sorted(s.context.span_id for s in exporter.spans)

    body = hub.client.post("/api/traces/replay", json=_window(hub)).json()

    assert (body["steps"], body["batches"]) == (2, 2)
    assert sorted(s.context.span_id for b in exporter.batches[before:] for s in b) == live


@pytest.mark.component
def test_the_window_is_half_open(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path)
    _told_live(hub)
    closed = hub.clock.now()

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
    assert (body["steps"], body["batches"]) == (1, 1)
    assert body["spans"] > 0
    assert _cursor_rows(hub) == rows


@pytest.mark.component
def test_the_cli_is_a_pure_client_and_renders_both_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, _ = _hub(tmp_path)
    _told_live(hub)
    timeouts: list[float] = []

    def relay(url: str, *, json: object, timeout: float, **_: object) -> httpx.Response:
        timeouts.append(timeout)
        return hub.client.post(url, json=json)

    monkeypatch.setattr(httpx, "post", relay)
    since = _T0.astimezone().replace(tzinfo=None).isoformat()
    until = (hub.clock.now() + timedelta(seconds=1)).astimezone().replace(tzinfo=None).isoformat()
    env = {"BZ_HUB_URL": "http://hub.local:8421"}

    dry = CliRunner().invoke(hub_group, ["traces", "replay", "--since", since, "--until", until, "--dry-run"], env=env)
    assert dry.exit_code == 0, dry.output
    assert "would tell 2 steps" in dry.output
    wet = CliRunner().invoke(hub_group, ["traces", "replay", "--since", since, "--until", until], env=env)
    assert wet.exit_code == 0, wet.output
    assert "told 2 steps" in wet.output
    assert min(timeouts) > 15.0

    wide = (hub.clock.now() + timedelta(days=30)).astimezone().replace(tzinfo=None).isoformat()
    refused = CliRunner().invoke(hub_group, ["traces", "replay", "--since", since, "--until", wide], env=env)
    assert refused.exit_code != 0
    assert "replay_max_window" in refused.output
