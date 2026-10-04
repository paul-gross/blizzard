"""An OTLP/HTTP sink served inside the test — decodes every trace, metrics and logs export it receives.

Shared by the OTLP binding's component test and the trace crash scenario. ``status`` is the lever:
any non-200 value refuses the request with that code. Use 500 to refuse at once — the SDK retries
502, 503 and 504 until its timeout."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, Span

TRACES_PATH = "/v1/traces"
METRICS_PATH = "/v1/metrics"
LOGS_PATH = "/v1/logs"


@dataclass
class OtlpSink:
    url: str
    status: int = 200
    requests: list[ExportTraceServiceRequest] = field(default_factory=list)
    metric_requests: list[ExportMetricsServiceRequest] = field(default_factory=list)
    log_requests: list[ExportLogsServiceRequest] = field(default_factory=list)

    @property
    def traces_endpoint(self) -> str:
        return f"{self.url}{TRACES_PATH}"

    def resource_spans(self) -> list[ResourceSpans]:
        return [rs for request in self.requests for rs in request.resource_spans]

    def spans(self) -> list[Span]:
        return [span for rs in self.resource_spans() for ss in rs.scope_spans for span in ss.spans]


@contextmanager
def otlp_sink() -> Iterator[OtlpSink]:
    """A running sink on a free loopback port, stopped on exit."""
    sink: OtlpSink

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received = {
                TRACES_PATH: (ExportTraceServiceRequest, sink.requests),
                METRICS_PATH: (ExportMetricsServiceRequest, sink.metric_requests),
                LOGS_PATH: (ExportLogsServiceRequest, sink.log_requests),
            }
            if sink.status != 200 or self.path not in received:
                self.send_response(sink.status if sink.status != 200 else 404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            message, kept = received[self.path]
            request = message()
            request.ParseFromString(body)
            kept.append(request)  # type: ignore[arg-type]
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    sink = OtlpSink(url=f"http://127.0.0.1:{server.server_address[1]}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield sink
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
