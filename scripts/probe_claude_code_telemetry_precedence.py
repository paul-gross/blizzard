"""Measure which source wins when Claude Code's OpenTelemetry destination is set twice, and record what it sends.

Run from the blizzard repo root with ``uv run python scripts/probe_claude_code_telemetry_precedence.py``. It spends no
tokens: the model endpoint is a closed loopback port, so the one turn fails fast and Claude Code still exports its
startup telemetry. Each cell runs ``claude -p`` under a disposable ``CLAUDE_CONFIG_DIR``, home and project, with two
local OTLP/HTTP sinks as observers: ``process`` is the destination named in the process env, ``settings`` the one named
in a settings document's ``env``. A cell's reading per signal is the sink that received it. Cells:

- ``flag-same-name``: process env against ``--settings`` env, same variable names.
- ``user-same-name``: process env against the user settings file's env, same variable names. Skipped after a
  fallback turn, which runs on the real config dir.
- ``generic-in-settings``: a process signal-specific endpoint against a settings generic endpoint.
- ``beta-endpoint``: the undocumented detailed-tracing endpoint, on the ``settings`` sink, against the documented
  exporters for all three signals. A sink counts a request in any encoding, since that endpoint is sent OTLP/JSON.
- ``switch-off``: the baseline's destinations with neither ``CLAUDE_CODE_ENABLE_TELEMETRY`` nor
  ``CLAUDE_CODE_ENHANCED_TELEMETRY_BETA`` set, read for whether anything is exported before those switches.
- ``enable-only``: the baseline without ``CLAUDE_CODE_ENHANCED_TELEMETRY_BETA``, as when the runner captures metrics or
  logs but not traces.
- ``baseline``: the process env alone, which also records scope names and whether spans parent on ``TRACEPARENT``, and
  saves one sanitized protobuf export body per signal as test fixtures.

Managed settings are not exercised: ``/etc/claude-code`` is machine-wide and would reach other workers on this host.
If the baseline emits nothing without a model reply, ``--fallback-model-turn`` runs one cheapest-model turn on the
real login and the output reports that it did. Prints a JSON reading and exits non-zero only if the probe could not
observe telemetry at all.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue

_SIGNALS = ("traces", "metrics", "logs")
_REQUEST_TYPES = {
    "traces": ExportTraceServiceRequest,
    "metrics": ExportMetricsServiceRequest,
    "logs": ExportLogsServiceRequest,
}
#: Per signal, the export request's resource field and each resource's scope field.
_FIELDS = {
    "traces": ("resource_spans", "scope_spans"),
    "metrics": ("resource_metrics", "scope_metrics"),
    "logs": ("resource_logs", "scope_logs"),
}
_TURN_TIMEOUT_SECONDS = 60
_EXPORT_INTERVAL_MS = "500"
_TRACE_ID = "0af7651916cd43dd8448eb211c80319c"
_PARENT_SPAN_ID = "b7ad6b7169203331"
_TRACEPARENT = f"00-{_TRACE_ID}-{_PARENT_SPAN_ID}-01"
#: Attribute keys whose values identify a person, a session or this machine, replaced in saved fixtures.
_SANITIZED_KEY_PREFIXES = (
    "user.",
    "organization.",
    "session.",
    "prompt.",
    "message.",
    "host.",
    "os.",
    "terminal.",
    "process.",
)
_SANITIZED_VALUE = "sanitized"
_DEFAULT_FIXTURES = Path("tests/fixtures/claude_code_telemetry")


@dataclass
class _Sink:
    """An OTLP/HTTP receiver that records every export it is sent, by signal.

    ``content_types`` holds every request's content type, in any encoding; ``bodies`` keeps only the protobuf ones,
    which are the bodies parsed for scopes, parenting and fixtures.
    """

    server: ThreadingHTTPServer
    bodies: dict[str, list[bytes]] = field(default_factory=lambda: {signal: [] for signal in _SIGNALS})
    content_types: dict[str, list[str]] = field(default_factory=lambda: {signal: [] for signal in _SIGNALS})

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def requests(self, signal: str) -> list:
        parsed = []
        for body in self.bodies[signal]:
            request = _REQUEST_TYPES[signal]()
            request.ParseFromString(body)
            parsed.append(request)
        return parsed


def _start_sink() -> _Sink:
    holder: dict[str, _Sink] = {}

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _body(self) -> bytes:
            if self.headers.get("Transfer-Encoding", "").lower() != "chunked":
                return self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = b""
            while size := int(self.rfile.readline().strip(), 16):
                body += self.rfile.read(size)
                self.rfile.readline()
            self.rfile.readline()
            return body

        def do_POST(self) -> None:
            body = self._body()
            signal = self.path.removeprefix("/v1/")
            content_type = self.headers.get("Content-Type", "")
            if signal in holder["sink"].bodies:
                holder["sink"].content_types[signal].append(content_type)
                if "protobuf" in content_type:
                    holder["sink"].bodies[signal].append(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    holder["sink"] = _Sink(server)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return holder["sink"]


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _signal_env(endpoints: dict[str, str]) -> dict[str, str]:
    """The exporter, protocol and signal-specific endpoint variables for each signal named in ``endpoints``."""
    env: dict[str, str] = {}
    for signal, url in endpoints.items():
        env[f"OTEL_{signal.upper()}_EXPORTER"] = "otlp"
        env[f"OTEL_EXPORTER_OTLP_{signal.upper()}_PROTOCOL"] = "http/protobuf"
        env[f"OTEL_EXPORTER_OTLP_{signal.upper()}_ENDPOINT"] = f"{url}/v1/{signal}"
    return env


def _base_env(root: Path, config: Path, fallback: bool) -> dict[str, str]:
    env = {
        "PATH": os.environ["PATH"],
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
        "OTEL_METRIC_EXPORT_INTERVAL": _EXPORT_INTERVAL_MS,
        "OTEL_LOGS_EXPORT_INTERVAL": _EXPORT_INTERVAL_MS,
        "OTEL_TRACES_EXPORT_INTERVAL": _EXPORT_INTERVAL_MS,
        "TRACEPARENT": _TRACEPARENT,
    }
    if fallback:
        env["HOME"] = os.environ["HOME"]
        return env
    env.update(
        HOME=str(root / "home"),
        CLAUDE_CONFIG_DIR=str(config),
        ANTHROPIC_API_KEY="probe-key",
        ANTHROPIC_BASE_URL=f"http://127.0.0.1:{_closed_port()}",
        CLAUDE_CODE_MAX_RETRIES="0",
    )
    return env


@dataclass(frozen=True)
class _Cell:
    name: str
    process: dict[str, str]
    settings_flag_env: dict[str, str] | None = None
    user_settings_env: dict[str, str] | None = None
    unset: tuple[str, ...] = ()


def _cells(process: _Sink, settings: _Sink) -> list[_Cell]:
    process_env = _signal_env(dict.fromkeys(_SIGNALS, process.url))
    settings_env = _signal_env(dict.fromkeys(_SIGNALS, settings.url))
    generic_in_settings = {"OTEL_EXPORTER_OTLP_ENDPOINT": settings.url, "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf"}
    beta = {"ENABLE_BETA_TRACING_DETAILED": "1", "BETA_TRACING_ENDPOINT": settings.url, **process_env}
    return [
        _Cell("baseline", process_env),
        _Cell("flag-same-name", process_env, settings_flag_env=settings_env),
        _Cell("user-same-name", process_env, user_settings_env=settings_env),
        _Cell("generic-in-settings", process_env, settings_flag_env=generic_in_settings),
        _Cell("beta-endpoint", beta),
        _Cell("switch-off", process_env, unset=("CLAUDE_CODE_ENABLE_TELEMETRY", "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA")),
        _Cell("enable-only", process_env, unset=("CLAUDE_CODE_ENHANCED_TELEMETRY_BETA",)),
    ]


def _run_cell(cell: _Cell, root: Path, binary: str, model: str, fallback: bool) -> None:
    directory = root / cell.name
    config, project = directory / "config", directory / "project"
    for path in (config, project, directory / "home"):
        path.mkdir(parents=True)
    command = [binary, "-p", "hi", "--output-format", "json"]
    if fallback:
        command += ["--model", model]
    if cell.user_settings_env is not None:
        (config / "settings.json").write_text(json.dumps({"env": cell.user_settings_env}))
    if cell.settings_flag_env is not None:
        flag = directory / "flag-settings.json"
        flag.write_text(json.dumps({"env": cell.settings_flag_env}))
        command += ["--settings", str(flag)]
    env = {k: v for k, v in (_base_env(directory, config, fallback) | cell.process).items() if k not in cell.unset}
    with contextlib.suppress(subprocess.TimeoutExpired):
        subprocess.run(
            command,
            cwd=project,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_TURN_TIMEOUT_SECONDS,
            check=False,
        )


def _scopes(sink: _Sink, signal: str) -> list[str]:
    resources_field, scopes_field = _FIELDS[signal]
    return sorted(
        {
            scope.scope.name
            for request in sink.requests(signal)
            for resource in getattr(request, resources_field)
            for scope in getattr(resource, scopes_field)
        }
    )


def _received(sink: _Sink) -> dict[str, dict[str, int]]:
    """Per signal, the number of requests received in each content type."""
    return {
        signal: {content_type: sink.content_types[signal].count(content_type) for content_type in set(types)}
        for signal, types in sink.content_types.items()
    }


def _reading(process: _Sink, settings: _Sink) -> dict[str, str]:
    """Per signal, the sink that received it in any encoding: ``process``, ``settings``, ``both`` or ``none``."""
    reading = {}
    for signal in _SIGNALS:
        sources = [name for name, sink in (("process", process), ("settings", settings)) if sink.content_types[signal]]
        reading[signal] = "both" if len(sources) == 2 else (sources[0] if sources else "none")
    return reading


def _spans(sink: _Sink) -> list:
    return [
        span
        for request in sink.requests("traces")
        for resource in request.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]


def _traceparent_reading(sink: _Sink) -> dict[str, object]:
    spans = _spans(sink)
    in_trace = [span for span in spans if span.trace_id.hex() == _TRACE_ID]
    parented = [span for span in in_trace if span.parent_span_id.hex() == _PARENT_SPAN_ID]
    return {"spans": len(spans), "in_env_trace": len(in_trace), "parented_on_env_span": len(parented)}


def _sanitize(attributes: list[KeyValue]) -> None:
    for attribute in attributes:
        if attribute.key.startswith(_SANITIZED_KEY_PREFIXES):
            attribute.value.CopyFrom(AnyValue(string_value=_SANITIZED_VALUE))


def _sanitized_export(sink: _Sink, signal: str) -> bytes:
    resources_field, scopes_field = _FIELDS[signal]
    request = sink.requests(signal)[0]
    for resource in getattr(request, resources_field):
        _sanitize(resource.resource.attributes)
        for scope in getattr(resource, scopes_field):
            if signal == "traces":
                for span in scope.spans:
                    _sanitize(span.attributes)
            elif signal == "logs":
                for record in scope.log_records:
                    _sanitize(record.attributes)
            else:
                for metric in scope.metrics:
                    for point in _data_points(metric):
                        _sanitize(point.attributes)
    return request.SerializeToString()


def _data_points(metric: object) -> list:
    kind = metric.WhichOneof("data")  # type: ignore[attr-defined]
    return list(getattr(metric, kind).data_points) if kind else []


def _save_fixtures(sink: _Sink, directory: Path) -> list[str]:
    directory.mkdir(parents=True, exist_ok=True)
    saved = []
    for signal in _SIGNALS:
        if sink.bodies[signal]:
            path = directory / f"{signal}.pb"
            path.write_bytes(_sanitized_export(sink, signal))
            saved.append(str(path))
    return saved


def _claude_version(binary: str) -> str:
    return subprocess.run([binary, "--version"], capture_output=True, text=True, check=True).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--claude-model", default="haiku")
    parser.add_argument("--fixtures-dir", type=Path, default=_DEFAULT_FIXTURES)
    parser.add_argument("--fallback-model-turn", action="store_true")
    args = parser.parse_args()
    if shutil.which(args.claude) is None:
        raise RuntimeError(f"{args.claude} is not on PATH")
    temp = os.environ.get("BLIZZARD_TMPDIR")
    report: dict[str, object] = {"claude_version": _claude_version(args.claude), "fallback_model_turn": False}
    cells: dict[str, object] = {}
    baseline_sink: _Sink | None = None
    with tempfile.TemporaryDirectory(dir=temp) as work:
        root = Path(work)
        fallback = False
        for name in (
            "baseline",
            "flag-same-name",
            "user-same-name",
            "generic-in-settings",
            "beta-endpoint",
            "switch-off",
            "enable-only",
        ):
            if name == "user-same-name" and fallback:
                cells[name] = {"skipped": "a fallback turn reads the real config dir, never this cell's user settings"}
                continue
            process, settings = _start_sink(), _start_sink()
            cell = next(c for c in _cells(process, settings) if c.name == name)
            _run_cell(cell, root, args.claude, args.claude_model, fallback)
            if name == "baseline" and not any(process.bodies.values()) and args.fallback_model_turn:
                fallback = True
                report["fallback_model_turn"] = True
                _run_cell(_Cell("baseline-fallback", cell.process), root, args.claude, args.claude_model, True)
            result: dict[str, object] = {"reached": _reading(process, settings)}
            if name == "baseline":
                baseline_sink = process
                result["scopes"] = {signal: _scopes(process, signal) for signal in _SIGNALS}
                result["traceparent"] = _traceparent_reading(process)
            if name == "beta-endpoint":
                result["beta_endpoint_received"] = _received(settings)
                result["documented_exporter_received"] = _received(process)
            cells[name] = result
            for sink in (process, settings):
                sink.server.shutdown()
        if baseline_sink is not None:
            report["fixtures"] = _save_fixtures(baseline_sink, args.fixtures_dir)
    report["cells"] = cells
    print(json.dumps(report, indent=2))
    observed = baseline_sink is not None and any(baseline_sink.bodies.values())
    raise SystemExit(0 if observed else 1)


if __name__ == "__main__":
    main()
