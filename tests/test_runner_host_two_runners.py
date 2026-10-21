"""One host serving two runners (component tier): every runner's graph reads the host's one ``HostStores``, and each
runner's identity reaches telemetry explicitly — a worker export by the runner prefix it arrived under, a platform
span by the runner whose tracer opened it or its parent."""

from __future__ import annotations

import dataclasses
from collections import Counter
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult, MetricsData, NumberDataPoint
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from blizzard.foundation.platform_tracing.handle import build_platform_tracing
from blizzard.foundation.tokens import TokenHash
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_ids import StepKey, trace_id
from blizzard.runner.app import build_hosted_app
from blizzard.runner.composition import HostProcess, RunnerProcess, build_host_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import BuiltRunner, ResolvedToken
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.runner.tracing.attributes import RUNNER_ID
from blizzard.runner.tracing.platform import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    host_stamp,
)
from blizzard.runner.tracing.received_export import build_received_telemetry_export
from tests import claude_code_telemetry
from tests.host_files import host_config_of
from tests.runner_fakes import REGISTERED_AT, FakeHub, migrated_store_at

pytestmark = pytest.mark.component

_ENDPOINT = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}
_RUNNERS = ("rn_a", "rn_b")
_PROTOBUF = {"Content-Type": "application/x-protobuf"}


class _CapturingMetricExporter(MetricExporter):
    def __init__(self) -> None:
        super().__init__()
        self.exported: list[MetricsData] = []

    def export(self, metrics_data: MetricsData, timeout_millis: float = 10_000, **kwargs: object) -> MetricExportResult:
        self.exported.append(metrics_data)
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: object) -> None:
        return None

    def points(self) -> list[NumberDataPoint]:
        return [
            point
            for data in self.exported
            for resource_metrics in data.resource_metrics
            for scope_metrics in resource_metrics.scope_metrics
            for metric in scope_metrics.metrics
            for point in metric.data.data_points  # type: ignore[union-attr]
        ]


@dataclasses.dataclass
class _Exports:
    spans: InMemorySpanExporter = dataclasses.field(default_factory=InMemorySpanExporter)
    metrics: _CapturingMetricExporter = dataclasses.field(default_factory=_CapturingMetricExporter)
    logs: InMemoryLogRecordExporter = dataclasses.field(default_factory=InMemoryLogRecordExporter)


def _host(tmp_path: Path, exports: _Exports) -> HostProcess:
    config = RunnerConfig(
        root=tmp_path,
        db_url=HostLayout(tmp_path).default_db_url,
        hub_url="http://127.0.0.1:9",
        tracing=TracingConfig(platform=True, platform_sample_ratio=1.0, harness_telemetry=True),
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)
    resource = {"service.name": "blizzard-runner"}
    return build_host_process(
        host_config_of(config),
        [BuiltRunner(rid, dataclasses.replace(config, runner_id=rid), ResolvedToken()) for rid in _RUNNERS],
        platform_tracing=build_platform_tracing(
            config.tracing,
            _ENDPOINT,
            resource=resource,
            scope=PLATFORM_INSTRUMENTATION_SCOPE,
            scope_version=PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
            stamp=host_stamp(),
            exporter=exports.spans,
        ),
        received_telemetry=build_received_telemetry_export(
            config.tracing, _ENDPOINT, resource=resource, metric_exporter=exports.metrics, log_exporter=exports.logs
        ),
    )


def _register_with_a_lease(runner: RunnerProcess) -> None:
    """``runner`` as registered, holding one open lease whose worker presents the runner's id as its token."""
    identity = RunnerIdentity(runner.runner_id, f"name-{runner.runner_id}", REGISTERED_AT)
    runner.stores.identity.record_runner_identity(identity)
    runner.identity.hold(identity)
    runner.stores.lease_record.record_lease(
        NewLease(
            lease_id=f"lease_{runner.runner_id}",
            chunk_id=f"ch_{runner.runner_id}",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=REGISTERED_AT,
        )
    )
    runner.stores.tokens.record_lease_token(f"lease_{runner.runner_id}", TokenHash(runner.runner_id).hex, REGISTERED_AT)


def _traces_body(runner_id: str) -> bytes:
    """The recorded harness trace export, re-rooted on ``runner_id``'s lease's own trace."""
    message = ExportTraceServiceRequest()
    message.ParseFromString(claude_code_telemetry.protobuf_body("traces"))
    for resource in message.resource_spans:
        for scope_spans in resource.scope_spans:
            for span in scope_spans.spans:
                span.trace_id = trace_id(StepKey.attempt(f"ch_{runner_id}", 1)).to_bytes(16, "big")
    return message.SerializeToString()


def _stamp(span: ReadableSpan) -> object:
    return (span.attributes or {}).get(RUNNER_ID)


def test_every_runners_graph_reads_the_hosts_one_host_stores(tmp_path: Path) -> None:
    host = _host(tmp_path, _Exports())
    hosted = build_hosted_app(host.runners[0].config, host)
    try:
        for runner in host.runners:
            assert not hasattr(runner, "host_stores")
            ctx = LoopWiring.of(runner.config).context(FakeHub(), runner)
            assert ctx.host_stores is host.host_stores
        assert [resume.host_stores for resume in hosted.resumes] == [host.host_stores] * len(_RUNNERS)
        for served in hosted.app.state.runners.served:
            assert served.seams.runner_status._daemon_liveness is host.host_stores.daemon_liveness
    finally:
        hosted.close()
        host.close()


def test_each_runners_worker_exports_and_platform_spans_carry_that_runners_identity(tmp_path: Path) -> None:
    exports = _Exports()
    host = _host(tmp_path, exports)
    hosted = build_hosted_app(host.runners[0].config, host)
    try:
        for runner in host.runners:
            _register_with_a_lease(runner)
        with TestClient(hosted.app) as client:
            for runner_id in _RUNNERS:
                headers = {**_PROTOBUF, "X-Blizzard-Lease-Token": runner_id}
                for signal, body in (
                    ("traces", _traces_body(runner_id)),
                    ("metrics", claude_code_telemetry.protobuf_body("metrics")),
                    ("logs", claude_code_telemetry.protobuf_body("logs")),
                ):
                    response = client.post(f"/runners/{runner_id}/v1/{signal}", content=body, headers=headers)
                    assert response.status_code == 200, (runner_id, signal, response.text)
        for runner in host.runners:
            with (
                runner.tracer.root(f"tick {runner.runner_id}"),
                host.platform_tracing.tracer.child(f"step {runner.runner_id}"),
            ):
                pass
        with host.platform_tracing.tracer.root("host-tick"):
            pass
        host.received_telemetry.shutdown(5.0)
        host.platform_tracing.shutdown(5.0)
        spans = list(exports.spans.get_finished_spans())

        worker = [span for span in spans if span.resource.attributes.get("service.name") != "blizzard-runner"]
        assert worker and {_stamp(span) for span in worker} == set(_RUNNERS)
        for runner_id in _RUNNERS:
            own_trace = trace_id(StepKey.attempt(f"ch_{runner_id}", 1))
            assert {_stamp(span) for span in worker if span.context and span.context.trace_id == own_trace} == {
                runner_id
            }
        by_point = Counter((point.attributes or {}).get(RUNNER_ID) for point in exports.metrics.points())
        by_record = Counter(
            (record.log_record.attributes or {}).get(RUNNER_ID) for record in exports.logs.get_finished_logs()
        )
        for stamped in (by_point, by_record):
            assert set(stamped) == set(_RUNNERS) and stamped["rn_a"] == stamped["rn_b"] > 0

        for runner_id in _RUNNERS:
            assert [_stamp(span) for span in spans if span.name in (f"tick {runner_id}", f"step {runner_id}")] == [
                runner_id
            ] * 2
        assert [_stamp(span) for span in spans if span.name == "host-tick"] == [None]
    finally:
        hosted.close()
        host.close()
