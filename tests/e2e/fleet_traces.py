"""Fleet-trace proof for the e2e tier: a real ``otelcol-contrib`` between a real hub and a file.

The collector runs the documented operator config (``packaging/otel-collector/collector.yaml``) under a
test overlay that rebinds the receiver and swaps the exporters for one ``file`` exporter, so the receiver,
processor and pipeline shape stay the ones ``docs/deployment/tracing.md`` documents. A scenario starts it
before the hub (the export cursor opens at enable time, and a failed first export backs off for minutes),
drives its chunk, then reads the file back and checks it against the published dictionary and the span spec
(``blizzard-product:/plans/tracing/fleet-spans/spec/spans.md``). Expectations come from the spec, never from
captured output."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.trace_ids import SpanRole, StepKey, span_id, trace_id
from tests.repo_files import repo_root
from tests.support import daemon_log_sink, free_port, read_daemon_log
from tests.trace_contract_support import dictionary, otlp_type, required_by_role, role_of_name, scope_of_role

ENV_COLLECTOR = "BLIZZARD_OTELCOL"
_BINARY = "otelcol-contrib"
_DOCUMENTED_CONFIG = repo_root() / "packaging" / "otel-collector" / "collector.yaml"
# The documented config reads these at load time; none is ever dialed because the overlay drops both exporters.
_PLACEHOLDER_ENV = {
    "TRACE_STORE_ENDPOINT": "tempo.example:4317",
    "TRACE_STORE_INSECURE": "false",
    "HOSTED_TRACES_ENDPOINT": "https://api.example.com",
    "HOSTED_TRACES_API_KEY": "placeholder",
}
_ROOT_ROLES = {"step", "gate"}


def resolve_collector() -> str | None:
    """The collector binary: ``BLIZZARD_OTELCOL``, else ``otelcol-contrib`` on ``PATH``.

    A candidate counts only if ``--version`` exits 0 — a mise shim is on ``PATH`` everywhere but fails
    outside the pinned task."""
    candidate = os.environ.get(ENV_COLLECTOR) or shutil.which(_BINARY)
    if not candidate:
        return None
    try:
        probe = subprocess.run([candidate, "--version"], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return candidate if probe.returncode == 0 else None


# --------------------------------------------------------------------------- #
# The exported file, parsed to plain records


@dataclass(frozen=True)
class ExportedEvent:
    name: str
    attributes: dict[str, Any]


@dataclass(frozen=True)
class ExportedLink:
    trace_id: str
    span_id: str
    attributes: dict[str, Any]

    @property
    def reason(self) -> object:
        return self.attributes.get("blizzard.link.reason")


@dataclass(frozen=True)
class ExportedSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    status: str
    start_ns: int
    end_ns: int
    attributes: dict[str, Any]
    resource: dict[str, Any]
    scope: str
    events: tuple[ExportedEvent, ...] = ()
    links: tuple[ExportedLink, ...] = ()

    @property
    def role(self) -> str:
        return role_of_name(self.name)

    @property
    def is_root(self) -> bool:
        return self.parent_span_id is None


@dataclass
class StepTrace:
    """One exported trace: its root and the children parented on it."""

    root: ExportedSpan
    children: list[ExportedSpan] = field(default_factory=list)

    @property
    def event_names(self) -> list[str]:
        return [event.name for event in self.root.events]

    @property
    def child_names(self) -> list[str]:
        return sorted(child.name for child in self.children)


def unix_ns(instant: str) -> int:
    """An ISO-8601 instant, as the hub renders it, in unix nanoseconds — exact to the microsecond."""
    moment = datetime.fromisoformat(instant)
    return (moment - datetime(1970, 1, 1, tzinfo=UTC)) // timedelta(microseconds=1) * 1000


def _decode_value(value: dict[str, Any]) -> object:
    if "stringValue" in value:
        return value["stringValue"]
    if "boolValue" in value:
        return value["boolValue"]
    if "intValue" in value:
        return int(value["intValue"])  # OTLP/JSON carries int64 as a string
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "arrayValue" in value:
        return [_decode_value(v) for v in value["arrayValue"].get("values", [])]
    raise AssertionError(f"unrecognized OTLP attribute value {value!r}")


def _decode_attributes(raw: Sequence[dict[str, Any]] | None) -> dict[str, Any]:
    return {a["key"]: _decode_value(a["value"]) for a in raw or ()}


_STATUS = {0: "UNSET", 1: "OK", 2: "ERROR"}


def parse_export(text: str) -> list[ExportedSpan]:
    """Every span of a file exporter's OTLP/JSON lines, in file order."""
    spans: list[ExportedSpan] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        for resource_spans in json.loads(line).get("resourceSpans", []):
            resource = _decode_attributes(resource_spans.get("resource", {}).get("attributes"))
            for scope_spans in resource_spans.get("scopeSpans", []):
                scope = scope_spans.get("scope", {}).get("name", "")
                for raw in scope_spans.get("spans", []):
                    spans.append(
                        ExportedSpan(
                            trace_id=raw["traceId"],
                            span_id=raw["spanId"],
                            parent_span_id=raw.get("parentSpanId") or None,
                            name=raw["name"],
                            status=_STATUS[raw.get("status", {}).get("code", 0)],
                            start_ns=int(raw["startTimeUnixNano"]),
                            end_ns=int(raw["endTimeUnixNano"]),
                            attributes=_decode_attributes(raw.get("attributes")),
                            resource=resource,
                            scope=scope,
                            events=tuple(
                                ExportedEvent(e["name"], _decode_attributes(e.get("attributes")))
                                for e in sorted(raw.get("events", []), key=lambda e: int(e["timeUnixNano"]))
                            ),
                            links=tuple(
                                ExportedLink(
                                    link["traceId"], link["spanId"], _decode_attributes(link.get("attributes"))
                                )
                                for link in raw.get("links", [])
                            ),
                        )
                    )
    return spans


def traces_of(spans: Sequence[ExportedSpan]) -> list[StepTrace]:
    """The step traces in the export, ordered by their root's start."""
    roots = {s.trace_id: StepTrace(s) for s in spans if s.is_root}
    for span in spans:
        if not span.is_root:
            roots[span.trace_id].children.append(span)
    return sorted(roots.values(), key=lambda t: (t.root.start_ns, t.root.name))


# --------------------------------------------------------------------------- #
# Shape: the published dictionary


def assert_conforms(spans: Sequence[ExportedSpan]) -> None:
    """Every name, event, link reason, attribute key and type is the dictionary's; every required attribute rides."""
    d = dictionary()
    types = {a["name"]: a["type"] for a in d["attributes"]}
    on = {a["name"]: set(a["on"]) for a in d["attributes"]}
    reasons = {entry["name"] for entry in d["link_reasons"]}
    resource_keys = {a["name"] for a in d["resource_attributes"]}
    required = required_by_role()

    def check_bag(bag: dict[str, Any], role: str, where: str) -> None:
        for key, value in bag.items():
            assert key in types, f"{where}: attribute {key!r} is not in the dictionary"
            assert otlp_type(value) == types[key], f"{where}: {key} is {otlp_type(value)}, dictionary says {types[key]}"
            assert role in on[key], f"{where}: {key} is not published for {role!r}"
        for key, roles in required.items():
            if role in roles:
                assert key in bag, f"{where}: lacks required {key}"

    for span in spans:
        try:
            role = role_of_name(span.name)
        except StopIteration:
            raise AssertionError(f"span name {span.name!r} is not in the dictionary") from None
        where = f"span {span.name!r}"
        check_bag(span.attributes, role, where)
        assert span.scope == scope_of_role(role), f"{where}: scope {span.scope!r}"
        # The SDK adds its own `telemetry.sdk.*`; the contract is that the published ones are all there.
        assert resource_keys <= set(span.resource), f"{where}: resource lacks {resource_keys - set(span.resource)}"
        assert span.resource.get("blizzard.trace.schema_version") == d["schema_version"], where
        assert span.status in {"UNSET", "ERROR"}, where
        assert span.is_root == (role in _ROOT_ROLES), (
            f"{where}: {role} span {'is' if span.is_root else 'is not'} a root"
        )
        for event in span.events:
            assert event.name in d["event_names"], f"{where}: event {event.name!r} is not in the dictionary"
            check_bag(event.attributes, f"event:{event.name}", f"{where} event {event.name!r}")
        for link in span.links:
            assert link.reason in reasons, f"{where}: link reason {link.reason!r} is not in the dictionary"
            check_bag(link.attributes, "link", f"{where} link")


# --------------------------------------------------------------------------- #
# Identity: the derived ids


def assert_identity(traces: Sequence[StepTrace], *, decision_ids: Sequence[str] = ()) -> None:
    """Each root's ids re-derive from its own chunk id and epoch (and, for a gate, a decision id the caller read
    off the hub's decisions surface); every child hangs on that root in that trace; every link names the
    derived root of an exported trace."""
    for trace in traces:
        root = trace.root
        chunk, epoch = root.attributes["blizzard.chunk.id"], root.attributes["blizzard.step.epoch"]
        if root.role == "gate":
            keys = [StepKey.gate(chunk, epoch, decision_id) for decision_id in decision_ids]
            key = next((k for k in keys if f"{trace_id(k):032x}" == root.trace_id), None)
            assert key is not None, f"gate root {root.trace_id} derives from none of decisions {list(decision_ids)}"
            role = SpanRole.GATE
        else:
            key, role = StepKey.attempt(chunk, epoch), SpanRole.STEP
        assert root.trace_id == f"{trace_id(key):032x}", f"{root.name}: trace id does not re-derive from {key.text()}"
        assert root.span_id == f"{span_id(key, role):016x}", (
            f"{root.name}: span id does not re-derive from {key.text()}"
        )
        for child in trace.children:
            assert child.trace_id == root.trace_id and child.parent_span_id == root.span_id, child.name
    root_ids = {t.root.span_id for t in traces}
    for trace in traces:
        for link in trace.root.links:
            assert link.span_id in root_ids, f"{trace.root.name}: link to {link.span_id} is not an exported root"


# --------------------------------------------------------------------------- #
# Skeleton: what the spec says each step looks like


@dataclass(frozen=True)
class StepExpect:
    """One step root's expected skeleton: the span name, outcome, status and destination, the child span names
    and event names it carries, and the reason of the link to the previous step (``None`` for a first step).

    ``events`` and ``children`` are compared as exact multisets; a hub-poll count that varies with timing is
    asserted as ``min_events`` (and ``min_children``) instead. ``invocation`` events are never compared: how many a step carries is the
    harness's usage reporting, not the trace shape."""

    name: str
    outcome: str
    to_node: str | None = None
    status: str = "UNSET"
    children: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    min_events: tuple[tuple[str, int], ...] = ()
    min_children: tuple[tuple[str, int], ...] = ()
    link: str | None = None
    attributes: tuple[tuple[str, object], ...] = ()
    child_attributes: tuple[tuple[str, str, object], ...] = ()


def assert_skeleton(traces: Sequence[StepTrace], expected: Sequence[StepExpect]) -> None:
    seen = [t.root.name for t in traces][: len(expected)]
    assert seen == [e.name for e in expected], f"step roots, in order: saw {seen}"
    for trace, want in zip(traces, expected, strict=False):
        root = trace.root
        where = f"{want.name} (epoch {root.attributes.get('blizzard.step.epoch')})"
        assert root.attributes.get("blizzard.step.outcome") == want.outcome, (
            f"{where}: outcome was {root.attributes.get('blizzard.step.outcome')!r}"
        )
        assert root.attributes.get("blizzard.step.to_node.name") == want.to_node, (
            f"{where}: to_node was {root.attributes.get('blizzard.step.to_node.name')!r}"
        )
        assert root.status == want.status, f"{where}: status was {root.status!r}"
        counted_children = {name for name, _ in want.min_children}
        children = trace.child_names
        assert sorted(n for n in children if n not in counted_children) == sorted(want.children), (
            f"{where}: child spans {children}"
        )
        for name, minimum in want.min_children:
            assert children.count(name) >= minimum, f"{where}: wanted at least {minimum} {name!r} spans, saw {children}"
        names = sorted(n for n in trace.event_names if n != "invocation")
        counted = {name for name, _ in want.min_events}
        assert sorted(n for n in names if n not in counted) == sorted(want.events), f"{where}: events {names}"
        for name, minimum in want.min_events:
            assert names.count(name) >= minimum, f"{where}: wanted at least {minimum} {name!r} events, saw {names}"
        reasons = [link.reason for link in root.links]
        assert reasons == ([want.link] if want.link else []), f"{where}: link reasons {reasons}"
        for key, value in want.attributes:
            assert root.attributes.get(key) == value, f"{where}: {key}"
        for child_name, key, value in want.child_attributes:
            (child,) = [c for c in trace.children if c.name == child_name]
            assert child.attributes.get(key) == value, f"{where}: {child_name} {key}"


# --------------------------------------------------------------------------- #
# The running collector


class FleetCollector:
    """One collector per test: ``endpoint`` for the hub's ``OTEL_EXPORTER_OTLP_ENDPOINT``, ``spans()`` once the
    scenario's chunk has closed. ``binary`` is ``None`` where no usable collector exists, and then the hub runs
    untraced and :meth:`require` skips the traces subtest."""

    def __init__(self, binary: str | None, workdir: Path) -> None:
        self.binary = binary
        self._workdir = workdir
        self._export = workdir / "traces.jsonl"
        self._log = workdir / "collector.log"
        self._proc: subprocess.Popen[str] | None = None
        self._port = 0

    @property
    def available(self) -> bool:
        return self.binary is not None

    @property
    def endpoint(self) -> str:
        assert self._proc is not None, "the collector is not running"
        return f"http://127.0.0.1:{self._port}"

    def require(self) -> None:
        """Skip the calling (sub)test where there is no collector."""
        if not self.available:
            pytest.skip(f"no usable {_BINARY} (set {ENV_COLLECTOR}, or run under `mise run e2e`)")

    def start(self) -> None:
        assert self.binary is not None
        self._workdir.mkdir(parents=True, exist_ok=True)
        self._port = free_port()
        overlay = self._workdir / "overlay.yaml"
        overlay.write_text(
            "receivers:\n"
            "  otlp:\n"
            "    protocols:\n"
            "      http:\n"
            f"        endpoint: 127.0.0.1:{self._port}\n"
            "exporters:\n"
            "  file:\n"
            f"    path: {self._export}\n"
            "service:\n"
            "  telemetry:\n"
            "    metrics:\n"
            "      level: none\n"
            "  pipelines:\n"
            "    traces:\n"
            "      exporters: [file]\n"
        )
        self._proc = subprocess.Popen(
            [self.binary, f"--config={_DOCUMENTED_CONFIG}", f"--config={overlay}"],
            env={**os.environ, **_PLACEHOLDER_ENV},
            stdout=daemon_log_sink(self._log),
            stderr=subprocess.STDOUT,
            text=True,
        )
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise AssertionError(f"collector exited early ({self._proc.returncode}):\n{read_daemon_log(self._log)}")
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", self._port), timeout=0.5):
                return
            time.sleep(0.1)
        raise AssertionError(f"collector did not listen on {self._port}:\n{read_daemon_log(self._log)}")

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _read(self) -> list[ExportedSpan]:
        return parse_export(self._export.read_text()) if self._export.exists() else []

    def spans(self, *, roots: int, exact: bool = True, timeout: float = 60.0) -> list[ExportedSpan]:
        """Wait until ``roots`` step roots are in the file, stop the collector so the file is complete, and parse it.

        ``exact`` demands no more roots than that; a scenario whose chunk is still looping when it stops reads
        only the first ``roots`` steps (the caller's :func:`assert_skeleton` takes the matching prefix)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if sum(1 for s in self._read() if s.is_root) >= roots:
                break
            time.sleep(0.25)
        self.stop()
        spans = self._read()
        seen = [s.name for s in spans if s.is_root]
        assert len(seen) == roots or (not exact and len(seen) > roots), (
            f"expected {roots} step roots in the collector's file, saw {len(seen)}: {seen}"
        )
        return spans

    def traces(self, *, roots: int, exact: bool = True, decision_ids: Sequence[str] = ()) -> list[StepTrace]:
        """The step traces, once they have passed the shape and identity checks."""
        spans = self.spans(roots=roots, exact=exact)
        assert_conforms(spans)
        traces = traces_of(spans)
        assert_identity(traces, decision_ids=decision_ids)
        return traces


@contextlib.contextmanager
def fleet_collector(workdir: Path) -> Iterator[FleetCollector]:
    collector = FleetCollector(resolve_collector(), workdir)
    if collector.available:
        collector.start()
    try:
        yield collector
    finally:
        collector.stop()
