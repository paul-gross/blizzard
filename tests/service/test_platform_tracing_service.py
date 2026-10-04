"""Platform spans from real daemons (service tier) — each host exports to an OTLP sink the test serves."""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from blizzard.foundation.tokens import TokenHash
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_ids import StepKey, trace_id
from blizzard.hub.config import HubConfig
from blizzard.runner.config import RunnerConfig
from blizzard.runner.leases import NewLease
from tests import claude_code_telemetry
from tests.e2e.test_acceptance_loop import _await_http, _free_port, _runner_config
from tests.otlp_sink import OtlpSink, otlp_sink
from tests.runner_fakes import make_store
from tests.service.support import (
    mint_fixture,
    mock_hub,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)
from tests.support import daemon_log_sink, read_daemon_log

pytestmark = [pytest.mark.service, service_gate]

#: The step-trace scopes — every other scope on the wire is a platform span's, whichever library opened it.
_FLEET_SCOPES = ("blizzard.hub.fleet_spans", "blizzard.runner.runner_spans")


def _platform_spans(sink: OtlpSink) -> list[tuple[dict[str, str], object]]:
    """Each platform span with its resource attributes."""
    found = []
    for resource_spans in sink.resource_spans():
        resource = {kv.key: kv.value.string_value for kv in resource_spans.resource.attributes}
        for scope_spans in resource_spans.scope_spans:
            if scope_spans.scope.name not in _FLEET_SCOPES:
                found.extend((resource, span) for span in scope_spans.spans)
    return found


def _stop(proc: subprocess.Popen[str]) -> None:
    """SIGTERM and wait — a graceful stop flushes what the daemon still buffers."""
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=30.0)


def _hub(hub_dir: Path, port: int, env: dict[str, str], *, platform: bool) -> subprocess.Popen[str]:
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    config = HubConfig.load(hub_dir)
    tracing = TracingConfig(platform=platform, platform_sample_ratio=1.0)
    config = dataclasses.replace(config, tracing=tracing)
    config.config_path.write_text(config.to_toml())
    return subprocess.Popen(
        [hub_bin, "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=daemon_log_sink(hub_dir / "daemon.log"),
        stderr=subprocess.STDOUT,
        text=True,
    )


def _drive_hub(tmp_path: Path, *, platform: bool) -> list[tuple[dict[str, str], object]]:
    port = _free_port()
    hub_dir = tmp_path / "hub"
    with otlp_sink() as sink:
        env = {**os.environ, "OTEL_EXPORTER_OTLP_ENDPOINT": sink.url}
        proc = _hub(hub_dir, port, env, platform=platform)
        client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
        try:
            _await_http(proc, client, "/api/health", log=hub_dir / "daemon.log")
            client.get("/api/chunks/ch_service?token=planted-secret")
        finally:
            client.close()
            _stop(proc)
        assert "planted-secret" not in repr(sink.requests)
        return _platform_spans(sink)


def test_a_hub_with_platform_on_delivers_request_and_query_spans(tmp_path: Path) -> None:
    spans = _drive_hub(tmp_path, platform=True)
    assert spans, "no platform span reached the sink"
    assert {resource["service.name"] for resource, _ in spans} == {"blizzard-hub"}
    names = {span.name for _, span in spans}  # type: ignore[attr-defined]
    assert "GET /api/chunks/{chunk_id}" in names
    assert any(not name.startswith(("GET ", "POST ", "sweep ")) for name in names), "no query span"


def test_a_hub_with_platform_unset_delivers_no_platform_span(tmp_path: Path) -> None:
    assert _drive_hub(tmp_path, platform=False) == []


def test_a_runner_host_delivers_server_spans_over_tcp_and_the_socket_and_tick_spans(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port), otlp_sink() as sink:
        config: RunnerConfig = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, tracing=TracingConfig(platform=True, platform_sample_ratio=1.0))
        config.config_path.write_text(config.to_toml())
        env = {
            **os.environ,
            "BLIZZARD_MOCK_HARNESS_FENCE": "1",
            "BZ_RUNNER_TICK_SECONDS": "0.5",
            "OTEL_EXPORTER_OTLP_ENDPOINT": sink.url,
        }
        log = config.root / "daemon.log"
        proc = subprocess.Popen(
            [str(Path(sys.executable).parent / "blizzard-runner"), "host", "--dir", str(config.root)],
            env=env,
            stdout=daemon_log_sink(log),
            stderr=subprocess.STDOUT,
            text=True,
        )
        tcp = httpx.Client(base_url=f"http://127.0.0.1:{config.port}", timeout=10.0)
        uds = httpx.Client(
            base_url="http://runner", transport=httpx.HTTPTransport(uds=str(config.socket_path)), timeout=10.0
        )
        try:
            _await_http(proc, tcp, "/api/health", log=log)
            assert tcp.get("/api/health?token=planted-secret").status_code == 200
            assert uds.get("/api/health").status_code == 200
            assert poll_until(lambda: read_daemon_log(log).count('"tick end"') >= 2, timeout=30.0)
        finally:
            tcp.close()
            uds.close()
            _stop(proc)
        spans = _platform_spans(sink)
        assert spans, read_daemon_log(log)
        assert {resource["service.name"] for resource, _ in spans} == {"blizzard-runner"}
        names = [span.name for _, span in spans]  # type: ignore[attr-defined]
        assert names.count("GET /api/health") >= 2
        assert "tick" in names
        assert any(name == "Reap" for name in names)
        assert "planted-secret" not in repr(sink.requests)


def _cli_export(trace: int) -> str:
    span = {
        "traceId": f"{trace:032x}",
        "spanId": f"{0x00F067AA0BA902B7:016x}",
        "name": "blizzard artifact create",
        "kind": 3,
        "startTimeUnixNano": "1000",
        "endTimeUnixNano": "2000",
        "attributes": [{"key": "blizzard.cli.command", "value": {"stringValue": "artifact create"}}],
    }
    return json.dumps({"resourceSpans": [{"scopeSpans": [{"scope": {"name": "blizzard.cli"}, "spans": [span]}]}]})


def test_a_worker_span_posted_over_tcp_and_the_socket_reaches_the_real_exporter(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    hub_port = _free_port()
    token = "service-lease-token"
    with mock_hub(bin_dir, hub_port), otlp_sink() as sink:
        config: RunnerConfig = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, tracing=TracingConfig(platform=True, platform_sample_ratio=1.0))
        config.config_path.write_text(config.to_toml())
        env = {**os.environ, "BLIZZARD_MOCK_HARNESS_FENCE": "1", "OTEL_EXPORTER_OTLP_ENDPOINT": sink.url}
        log = config.root / "daemon.log"
        proc = subprocess.Popen(
            [str(Path(sys.executable).parent / "blizzard-runner"), "host", "--dir", str(config.root)],
            env=env,
            stdout=daemon_log_sink(log),
            stderr=subprocess.STDOUT,
            text=True,
        )
        tcp = httpx.Client(base_url=f"http://127.0.0.1:{config.port}", timeout=10.0)
        uds = httpx.Client(
            base_url="http://runner", transport=httpx.HTTPTransport(uds=str(config.socket_path)), timeout=10.0
        )
        headers = {"Content-Type": "application/json", "X-Blizzard-Lease-Token": token}
        try:
            _await_http(proc, tcp, "/api/health", log=log)
            assert poll_until(lambda: '"tick end"' in read_daemon_log(log), timeout=30.0)
            store = make_store(config.db_url)
            now = datetime.now(UTC)
            store.record_lease(
                NewLease(
                    lease_id="lease_svc",
                    chunk_id="ch_svc",
                    graph_id="gr_1",
                    node_id="nd_build",
                    node_name="build",
                    epoch=1,
                    runner_id=config.runner_id,
                    retries_max=2,
                    created_at=now,
                )
            )
            store.record_lease_token("lease_svc", TokenHash(token).hex, now)
            body = _cli_export(trace_id(StepKey.attempt("ch_svc", 1)))
            assert tcp.post("/v1/traces", content=body, headers=headers).status_code == 200
            assert uds.post("/v1/traces", content=body, headers=headers).status_code == 200
        finally:
            tcp.close()
            uds.close()
            _stop(proc)
        cli = [(r, s) for r, s in _platform_spans(sink) if r["service.name"] == "blizzard-cli"]
        assert len(cli) == 2, read_daemon_log(log)


def test_claude_code_metrics_and_logs_posted_over_tcp_and_the_socket_reach_the_real_exporters(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    hub_port = _free_port()
    token = "service-lease-token"
    with mock_hub(bin_dir, hub_port), otlp_sink() as sink:
        config: RunnerConfig = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        tracing = TracingConfig(platform=True, platform_sample_ratio=1.0, harness_telemetry=True)
        config = dataclasses.replace(config, tracing=tracing)
        config.config_path.write_text(config.to_toml())
        env = {**os.environ, "BLIZZARD_MOCK_HARNESS_FENCE": "1", "OTEL_EXPORTER_OTLP_ENDPOINT": sink.url}
        log = config.root / "daemon.log"
        proc = subprocess.Popen(
            [str(Path(sys.executable).parent / "blizzard-runner"), "host", "--dir", str(config.root)],
            env=env,
            stdout=daemon_log_sink(log),
            stderr=subprocess.STDOUT,
            text=True,
        )
        tcp = httpx.Client(base_url=f"http://127.0.0.1:{config.port}", timeout=10.0)
        uds = httpx.Client(
            base_url="http://runner", transport=httpx.HTTPTransport(uds=str(config.socket_path)), timeout=10.0
        )
        try:
            _await_http(proc, tcp, "/api/health", log=log)
            assert poll_until(lambda: '"tick end"' in read_daemon_log(log), timeout=30.0)
            store = make_store(config.db_url)
            now = datetime.now(UTC)
            store.record_lease(
                NewLease(
                    lease_id="lease_svc",
                    chunk_id="ch_svc",
                    graph_id="gr_1",
                    node_id="nd_build",
                    node_name="build",
                    epoch=1,
                    runner_id=config.runner_id,
                    retries_max=2,
                    created_at=now,
                )
            )
            store.record_lease_token("lease_svc", TokenHash(token).hex, now)
            for client in (tcp, uds):
                for path, signal in (("/v1/metrics", "metrics"), ("/v1/logs", "logs")):
                    for content_type, body in (
                        ("application/x-protobuf", claude_code_telemetry.protobuf_body(signal)),
                        ("application/json", claude_code_telemetry.json_body(signal)),
                    ):
                        headers = {"Content-Type": content_type, "X-Blizzard-Lease-Token": token}
                        assert client.post(path, content=body, headers=headers).status_code == 200
        finally:
            tcp.close()
            uds.close()
            _stop(proc)
        points = [
            (resource_metrics.resource, point)
            for request in sink.metric_requests
            for resource_metrics in request.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
            for point in metric.sum.data_points
        ]
        records = [
            (resource_logs.resource, record)
            for request in sink.log_requests
            for resource_logs in request.resource_logs
            for scope_logs in resource_logs.scope_logs
            for record in scope_logs.log_records
        ]
        assert len(points) == 8, read_daemon_log(log)
        assert len(records) == 24, read_daemon_log(log)
        for resource, item in [*points, *records]:
            names = {kv.key: kv.value.string_value for kv in resource.attributes}
            assert names["service.name"] == "blizzard-claude-code"
            stamped = {kv.key: kv.value.string_value for kv in item.attributes}
            assert stamped["blizzard.lease.id"] == "lease_svc"
            assert stamped["blizzard.runner.id"] == config.runner_id
