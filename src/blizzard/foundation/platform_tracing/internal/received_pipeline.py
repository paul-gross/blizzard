"""The enabled received-telemetry export: admitted data points and log records rebuilt as SDK values and handed
to the SDK's own OTLP/HTTP exporters, behind the same URL redaction every platform span leaves through.

Neither export runs on the caller's thread. Log
records go through the SDK's own batch processor. Metrics go through a bounded queue drained by one thread: the
SDK has no batch processor for points already aggregated, and its periodic reader would re-aggregate them."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence

from opentelemetry._logs import LogRecord, SeverityNumber
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk._logs import ReadWriteLogRecord
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, LogRecordExporter
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    Buckets,
    ExponentialHistogram,
    ExponentialHistogramDataPoint,
    Gauge,
    Histogram,
    HistogramDataPoint,
    Metric,
    MetricExporter,
    MetricExportResult,
    MetricsData,
    NumberDataPoint,
    ResourceMetrics,
    ScopeMetrics,
    Sum,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import TraceFlags

from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.internal.redaction import redacted
from blizzard.foundation.platform_tracing.received import ReceivedDataPoint, ReceivedLogRecord

_log = get_logger(__name__)

#: OTLP numbers temporality from 1 (delta); anything else reads as cumulative.
_DELTA = 1
#: Data points the metrics queue holds before it drops its oldest batch — the SDK batch processors' own default.
MAX_QUEUED_POINTS = 2048

_MetricKey = tuple[str, str, str, str, int, bool]


def _temporality(value: int) -> AggregationTemporality:
    return AggregationTemporality.DELTA if value == _DELTA else AggregationTemporality.CUMULATIVE


def _metric(key: _MetricKey, points: Sequence[ReceivedDataPoint]) -> Metric:
    name, description, unit, kind, temporality, monotonic = key
    if kind == "exponential_histogram":
        data: Sum | Gauge | Histogram | ExponentialHistogram = ExponentialHistogram(
            data_points=[_exponential_point(point) for point in points],
            aggregation_temporality=_temporality(temporality),
        )
    elif kind == "histogram":
        data = Histogram(
            data_points=[
                HistogramDataPoint(
                    attributes=redacted(point.attributes),  # pyright: ignore[reportArgumentType]
                    start_time_unix_nano=point.start_time_ns,
                    time_unix_nano=point.time_ns,
                    count=point.count,
                    sum=point.total,
                    bucket_counts=point.bucket_counts,
                    explicit_bounds=point.bounds,
                    min=point.minimum,  # pyright: ignore[reportArgumentType]
                    max=point.maximum,  # pyright: ignore[reportArgumentType]
                )
                for point in points
            ],
            aggregation_temporality=_temporality(temporality),
        )
    else:
        numbers = [
            NumberDataPoint(
                attributes=redacted(point.attributes),  # pyright: ignore[reportArgumentType]
                start_time_unix_nano=point.start_time_ns,
                time_unix_nano=point.time_ns,
                value=point.value,
            )
            for point in points
        ]
        data = (
            Gauge(data_points=numbers)
            if kind == "gauge"
            else Sum(data_points=numbers, aggregation_temporality=_temporality(temporality), is_monotonic=monotonic)
        )
    return Metric(name=name, description=description, unit=unit, data=data)


def _exponential_point(point: ReceivedDataPoint) -> ExponentialHistogramDataPoint:
    buckets = point.exponential
    assert buckets is not None, "an exponential histogram point carries its buckets"
    return ExponentialHistogramDataPoint(
        attributes=redacted(point.attributes),  # pyright: ignore[reportArgumentType]
        start_time_unix_nano=point.start_time_ns,
        time_unix_nano=point.time_ns,
        count=point.count,
        sum=point.total,
        scale=buckets.scale,
        zero_count=buckets.zero_count,
        positive=Buckets(buckets.positive_offset, buckets.positive_counts),
        negative=Buckets(buckets.negative_offset, buckets.negative_counts),
        flags=0,
        min=point.minimum,  # pyright: ignore[reportArgumentType]
        max=point.maximum,  # pyright: ignore[reportArgumentType]
    )


def _metrics_data(points: Sequence[ReceivedDataPoint], resource: Resource) -> MetricsData:
    """Points grouped by scope, then by the metric they belong to; each keeps its temporality and timestamps."""
    scopes: dict[tuple[str, str], dict[_MetricKey, list[ReceivedDataPoint]]] = defaultdict(lambda: defaultdict(list))
    for point in points:
        key = (point.metric_name, point.description, point.unit, point.kind, point.temporality, point.monotonic)
        scopes[(point.scope_name, point.scope_version)][key].append(point)
    scope_metrics = [
        ScopeMetrics(
            scope=InstrumentationScope(name, version or None),
            metrics=[_metric(key, grouped) for key, grouped in metrics.items()],
            schema_url="",
        )
        for (name, version), metrics in scopes.items()
    ]
    return MetricsData(
        resource_metrics=[ResourceMetrics(resource=resource, scope_metrics=scope_metrics, schema_url="")]
    )


def _emitted(record: ReceivedLogRecord, resource: Resource) -> ReadWriteLogRecord:
    sdk_record = LogRecord(
        timestamp=record.time_ns,
        observed_timestamp=record.observed_time_ns or record.time_ns,
        trace_id=record.trace_id or 0,
        span_id=record.span_id or 0,
        trace_flags=TraceFlags(record.trace_flags),
        severity_text=record.severity_text or None,
        severity_number=SeverityNumber(record.severity_number) if 0 < record.severity_number <= 24 else None,
        body=record.body,
        attributes=redacted(record.attributes),  # pyright: ignore[reportArgumentType]
    )
    return ReadWriteLogRecord(
        sdk_record, resource, InstrumentationScope(record.scope_name, record.scope_version or None)
    )


class _MetricQueue:
    """Batches of data points waiting for the metric exporter, drained in order by one daemon thread. Full, it
    drops its oldest batch, as the SDK batch processors do; a failed export is dropped with a warning."""

    def __init__(self, exporter: MetricExporter, max_points: int = MAX_QUEUED_POINTS) -> None:
        self._exporter = exporter
        self._max_points = max_points
        self._batches: deque[tuple[MetricsData, int]] = deque()
        self._queued = 0
        self._exporting = False
        self._stopped = False
        self._condition = threading.Condition()
        self._thread = threading.Thread(target=self._drain, name="blizzard-received-metrics", daemon=True)
        self._thread.start()

    def put(self, data: MetricsData, points: int) -> None:
        with self._condition:
            if self._stopped:
                _log.warning("received metrics arrived after shutdown", count=points)
                return
            while self._batches and self._queued + points > self._max_points:
                _, dropped = self._batches.popleft()
                self._queued -= dropped
                _log.warning("received metrics queue full, oldest dropped", count=dropped)
            self._batches.append((data, points))
            self._queued += points
            self._condition.notify_all()

    def _drain(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: bool(self._batches) or self._stopped)
                if not self._batches:
                    return
                data, points = self._batches.popleft()
                self._queued -= points
                self._exporting = True
            try:
                result = self._exporter.export(data)
                if result is not MetricExportResult.SUCCESS:
                    _log.warning("received metrics were not exported", count=points)
            except Exception:
                _log.warning("received metrics were not exported", count=points, exc_info=True)
            finally:
                with self._condition:
                    self._exporting = False
                    self._condition.notify_all()

    def shutdown(self, timeout: float) -> None:
        """Wait at most ``timeout`` seconds for what is queued to leave. Past it, what is still queued is
        discarded with a logged count so the thread exits; the exporter is shut down only once the thread has."""
        deadline = time.monotonic() + timeout
        with self._condition:
            self._condition.wait_for(lambda: not self._batches and not self._exporting, timeout)
            if self._batches:
                _log.warning("received metrics discarded at shutdown", count=self._queued)
                self._batches.clear()
                self._queued = 0
            self._stopped = True
            self._condition.notify_all()
        self._thread.join(max(0.0, deadline - time.monotonic()))
        if self._thread.is_alive():
            _log.warning("received metrics export still in flight at shutdown, exporter left open")
            return
        self._exporter.shutdown()


class EnabledReceivedTelemetryExport:
    def __init__(
        self,
        resource: Resource,
        metric_exporter: MetricExporter | None,
        log_exporter: LogRecordExporter | None,
    ) -> None:
        self._resource = resource
        self._metrics = _MetricQueue(metric_exporter) if metric_exporter is not None else None
        self._logs = BatchLogRecordProcessor(log_exporter) if log_exporter is not None else None

    @classmethod
    def build(
        cls,
        *,
        resource: Mapping[str, str],
        metric_exporter: MetricExporter | None,
        log_exporter: LogRecordExporter | None,
        metrics: bool,
        logs: bool,
    ) -> EnabledReceivedTelemetryExport:
        return cls(
            Resource.create(dict(resource)),
            (metric_exporter or OTLPMetricExporter()) if metrics else None,
            (log_exporter or OTLPLogExporter()) if logs else None,
        )

    def forward_metrics(self, points: Sequence[ReceivedDataPoint], resource_service_name: str) -> None:
        if self._metrics is None or not points:
            return
        resource = self._resource.merge(Resource({"service.name": resource_service_name}))
        self._metrics.put(_metrics_data(points, resource), len(points))

    def forward_logs(self, records: Sequence[ReceivedLogRecord], resource_service_name: str) -> None:
        if self._logs is None or not records:
            return
        resource = self._resource.merge(Resource({"service.name": resource_service_name}))
        for record in records:
            self._logs.on_emit(_emitted(record, resource))

    def shutdown(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        if self._logs is not None:
            self._logs.force_flush(int(timeout * 1000))
            self._logs.shutdown()
        if self._metrics is not None:
            self._metrics.shutdown(max(0.0, deadline - time.monotonic()))
