"""``GET /api/traces/status`` and ``blizzard hub traces status`` (component tier) — composed from the settings
and the facts the sweep leaves behind, with credentials planted in every part of the endpoint."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.hub.cli import hub as hub_group
from tests.support import HubHarness, InMemoryTraceExporter
from tests.test_trace_export_sweep import _closed_pair, _sweep
from tests.trace_hub import trace_hub

pytestmark = pytest.mark.component

_CREDENTIALS = ("SECRET1", "SECRET2", "SECRET3")
_ENV = {
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://user:pw-SECRET1@collector:4318/v1/traces?token=SECRET2",
    "OTEL_EXPORTER_OTLP_HEADERS": "authorization=SECRET3",
}
_SETTLED = TracingConfig(settle_seconds=0, sweep_seconds=60, max_lag_seconds=3600)


def _hub(
    tmp_path: Path, settings: TracingSettings, config: TracingConfig = _SETTLED
) -> tuple[HubHarness, InMemoryTraceExporter]:
    exporter = InMemoryTraceExporter()
    hub, _graph = trace_hub(
        tmp_path, trace_exporter=exporter if settings.enabled() else None, tracing=config, tracing_settings=settings
    )
    return hub, exporter


def _status(hub: HubHarness) -> dict:  # type: ignore[type-arg]
    resp = hub.client.get("/api/traces/status")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_hub_with_tracing_off_reports_it_off(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path, TracingSettings.of({}))
    body = _status(hub)
    assert body["state"] == "disabled"
    assert body["endpoint"] is None
    assert body["cursor_at"] is None
    assert body["lag_seconds"] is None


def test_a_rejected_setting_is_named(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path, TracingSettings.of({**_ENV, "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"}))
    body = _status(hub)
    assert body["state"] == "rejected"
    assert body["rejected_setting"] == "OTEL_EXPORTER_OTLP_PROTOCOL"
    assert body["rejected_value"] == "grpc"
    assert body["lag_seconds"] is None


def test_status_shows_only_the_origin_in_the_api_and_the_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = TracingSettings.of(_ENV)
    hub, _ = _hub(tmp_path, settings)
    _sweep(hub).sweep()
    assert _status(hub)["endpoint"] == "http://collector:4318"
    assert not any(secret in hub.client.get("/api/traces/status").text for secret in _CREDENTIALS)

    monkeypatch.setattr(httpx, "get", lambda url, *, params=None, timeout, **_: hub.client.get(url, params=params))
    for args in (["traces", "status"], ["traces", "status", "--json"]):
        result = CliRunner().invoke(hub_group, args, env={"BZ_HUB_URL": "http://hub.local:8421"})
        assert result.exit_code == 0, result.output
        assert "http://collector:4318" in result.output
        assert not any(secret in result.output for secret in _CREDENTIALS)


def test_the_cursor_its_lag_and_the_last_export(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path, TracingSettings.of(_ENV), TracingConfig(settle_seconds=300))
    _sweep(hub).sweep()
    start = _status(hub)
    assert start["cursor_at"] is not None
    assert start["lag_seconds"] is None
    assert start["last_export_at"] is None

    _closed_pair(hub)
    hub.clock.advance(timedelta(seconds=100))
    waiting = _status(hub)
    assert waiting["lag_seconds"] == 100
    assert waiting["last_export_at"] is None

    hub.clock.advance(timedelta(seconds=200))
    _sweep(hub).sweep()
    told = _status(hub)
    assert told["lag_seconds"] is None
    assert told["last_export_at"] is not None
    assert told["last_export_span_count"] > 0
    assert told["cursor_at"] > start["cursor_at"]


def test_an_idle_fleet_has_no_lag_however_old_the_cursor(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path, TracingSettings.of(_ENV))
    _sweep(hub).sweep()
    hub.clock.advance(timedelta(seconds=1800))
    assert _status(hub)["lag_seconds"] is None


def test_the_last_error_is_ongoing_until_an_export_recovers_and_carries_no_exporter_text(tmp_path: Path) -> None:
    hub, exporter = _hub(tmp_path, TracingSettings.of(_ENV))
    _sweep(hub).sweep()
    assert _status(hub)["last_error_at"] is None
    _closed_pair(hub)
    exporter.fail = True
    _sweep(hub).sweep()
    failed = _status(hub)
    assert failed["last_error_ongoing"] is True
    assert failed["last_error_at"] is not None
    assert not any(secret in str(failed) for secret in _CREDENTIALS)

    exporter.fail = False
    hub.clock.advance(timedelta(seconds=120))
    _sweep(hub).sweep()
    recovered = _status(hub)
    assert recovered["last_error_ongoing"] is False
    assert recovered["last_error_at"] == failed["last_error_at"]
    assert recovered["last_export_at"] is not None
