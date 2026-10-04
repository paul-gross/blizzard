"""Fleet-trace proof for the e2e tier: a real ``otelcol-contrib`` between a real hub (and runner) and a file.

The collector starts before the hub; its file is read back against the dictionary and the span spec, runner
``worker`` spans nesting on hub step roots by id (:func:`assert_runner_nesting`). Platform spans share the
file; they are partitioned out by instrumentation scope (:func:`is_fleet`), so the skeleton reads fleet spans
only and :meth:`FleetCollector.platform_spans` reads the rest."""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
import sqlalchemy

from blizzard.foundation import trace_attributes as shared
from blizzard.foundation.platform_tracing import attributes as platform_attr
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_ids import (
    ChunkRole,
    SpanRole,
    StepKey,
    chunk_span_id,
    chunk_trace_id,
    lifetime_context,
    span_id,
)
from blizzard.hub.domain.observability.tracing import attributes as hub_attr
from blizzard.runner.composition import RunnerProcess, build_runner_platform_tracing, build_runner_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.tracing import attributes as runner_attr
from blizzard.runner.store.schema import route_tokens
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
_CHUNK_ROLES = {role.value for role in ChunkRole}
_ROOTS = {ChunkRole.WORK.value, ChunkRole.LIFETIME.value}
_WORKER_ROLE = "runner/worker"
_INVOCATION_ROLE = "runner/invocation"


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
        return role_of_name(self.name, self.attributes)

    @property
    def is_root(self) -> bool:
        return self.parent_span_id is None

    @property
    def is_step_root(self) -> bool:
        return self.role in _ROOT_ROLES

    @property
    def is_chunk_level(self) -> bool:
        return self.role in _CHUNK_ROLES

    @property
    def is_runner(self) -> bool:
        return scope_of_role(self.role) == runner_attr.INSTRUMENTATION_SCOPE


@dataclass
class StepTrace:
    """One step's exported spans: its root and the children parented on it."""

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


def is_fleet(span: ExportedSpan) -> bool:
    """Whether a span is the fleet sweep's — its scope is one the dictionary's fleet ``spans`` publish."""
    return span.scope in {entry["scope"] for entry in dictionary()["spans"]}


def traces_of(spans: Sequence[ExportedSpan]) -> list[StepTrace]:
    """The hub's steps in the export, each a root and the children parented on it, ordered by root start."""
    roots = {(s.trace_id, s.span_id): StepTrace(s) for s in spans if s.is_step_root}
    for span in spans:
        if span.is_runner or span.is_step_root or span.is_chunk_level:
            continue
        roots[(span.trace_id, span.parent_span_id or "")].children.append(span)
    return sorted(roots.values(), key=lambda t: (t.root.start_ns, t.root.name))


def chunk_level(spans: Sequence[ExportedSpan]) -> list[ExportedSpan]:
    """The work root, the lifetime trace's spans and any completion marker in the export."""
    return [s for s in spans if s.is_chunk_level]


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
            role = role_of_name(span.name, span.attributes)
        except StopIteration:
            raise AssertionError(f"span name {span.name!r} is not in the dictionary") from None
        where = f"span {span.name!r}"
        check_bag(span.attributes, role, where)
        assert span.scope == scope_of_role(role), f"{where}: scope {span.scope!r}"
        # The SDK adds its own `telemetry.sdk.*`; the contract is that the published ones are all there.
        assert resource_keys <= set(span.resource), f"{where}: resource lacks {resource_keys - set(span.resource)}"
        assert span.resource.get("blizzard.trace.schema_version") == d["schema_version"], where
        assert span.status in {"UNSET", "ERROR"}, where
        assert span.is_root == (role in _ROOTS), f"{where}: {role} span {'is' if span.is_root else 'is not'} a root"
        service = next(e for e in d["spans"] if e["role"] == role).get("service")
        assert (
            (span.resource.get("service.name") == service)
            if service
            else (span.resource.get("service.name") != hub_attr.CHUNK_SERVICE_NAME)
        ), f"{where}: service.name {span.resource.get('service.name')!r}"
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
    off the hub's decisions surface): the chunk's work trace id, the work root as its parent; every child hangs on
    that root in that trace; every link names the derived root of an exported step."""
    for trace in traces:
        root = trace.root
        chunk, epoch = root.attributes["blizzard.chunk.id"], root.attributes["blizzard.step.epoch"]
        if root.role == "gate":
            keys = [StepKey.gate(chunk, epoch, decision_id) for decision_id in decision_ids]
            key = next((k for k in keys if f"{span_id(k, SpanRole.GATE):016x}" == root.span_id), None)
            assert key is not None, f"gate root {root.span_id} derives from none of decisions {list(decision_ids)}"
            role = SpanRole.GATE
        else:
            key, role = StepKey.attempt(chunk, epoch), SpanRole.STEP
        assert root.trace_id == f"{chunk_trace_id(chunk):032x}", f"{root.name}: trace id is not chunk {chunk}'s"
        assert root.parent_span_id == f"{chunk_span_id(chunk):016x}", (
            f"{root.name}: parent is not chunk {chunk}'s work root"
        )
        assert root.span_id == f"{span_id(key, role):016x}", (
            f"{root.name}: span id does not re-derive from {key.text()}"
        )
        for child in trace.children:
            assert child.trace_id == root.trace_id and child.parent_span_id == root.span_id, child.name
    root_ids = {t.root.span_id for t in traces}
    for trace in traces:
        for link in trace.root.links:
            assert link.span_id in root_ids, f"{trace.root.name}: link to {link.span_id} is not an exported root"


@dataclass(frozen=True)
class ChunkTraces:
    """The two roots a finished chunk was told under."""

    lifetime: ExportedSpan
    work: ExportedSpan


def assert_chunk_trace(
    spans: Sequence[ExportedSpan], chunk_id: str, *, outcome: str, steps: int, waits: Sequence[str] = ("backlog wait",)
) -> ChunkTraces:
    """The chunk was told as two traces. The work trace has a parentless ``chunk work`` root with the derived ids,
    ``steps`` step roots as its direct children, no wait and no marker, and a link to the lifetime root. The
    lifetime trace has a parentless ``chunk`` root with its own derived ids, a span per step linking to that step's
    root, and the ``waits`` parented on the root. Every lifetime span leaves as ``blizzard-chunk``; every other
    fleet span of the chunk, runner spans included, is in the work trace."""
    work_trace, lifetime_trace = f"{chunk_trace_id(chunk_id):032x}", f"{lifetime_context(chunk_id).trace_id:032x}"
    mine = [s for s in spans if s.attributes.get(shared.CHUNK_ID) == chunk_id]
    assert {s.trace_id for s in mine} == {work_trace, lifetime_trace}, (
        f"chunk {chunk_id} spans are in traces {sorted({s.trace_id for s in mine})}"
    )
    work_spans = [s for s in mine if s.trace_id == work_trace]
    life_spans = [s for s in mine if s.trace_id == lifetime_trace]
    (work,) = [s for s in work_spans if s.role == ChunkRole.WORK]
    assert (work.name, work.span_id, work.parent_span_id) == ("chunk work", f"{chunk_span_id(chunk_id):016x}", None)
    assert work.attributes[hub_attr.CHUNK_OUTCOME] == outcome and work.attributes[hub_attr.CHUNK_STEPS] == steps
    assert not [s for s in work_spans if s.is_chunk_level and s.role != ChunkRole.WORK], "chunk-level spans in work"
    roots = [s for s in work_spans if s.is_step_root]
    assert len(roots) == steps and {r.parent_span_id for r in roots} == {work.span_id}
    assert work.start_ns == min(r.start_ns for r in roots) and work.end_ns >= max(r.end_ns for r in roots)
    root = lifetime_context(chunk_id)
    assert [(link.trace_id, link.span_id, link.reason) for link in work.links] == [
        (lifetime_trace, f"{root.span_id:016x}", "lifetime")
    ]

    (life,) = [s for s in life_spans if s.role == ChunkRole.LIFETIME]
    assert (life.name, life.span_id, life.parent_span_id) == ("chunk", f"{root.span_id:016x}", None)
    assert life.attributes[hub_attr.CHUNK_OUTCOME] == outcome and life.attributes[hub_attr.CHUNK_STEPS] == steps
    assert life.start_ns <= work.start_ns and life.end_ns == work.end_ns
    assert {s.resource.get("service.name") for s in life_spans} == {hub_attr.CHUNK_SERVICE_NAME}
    assert hub_attr.CHUNK_SERVICE_NAME not in {s.resource.get("service.name") for s in work_spans}
    told = [s for s in life_spans if s.role == ChunkRole.STEP]
    assert len(told) == steps and {s.parent_span_id for s in told} == {life.span_id}
    by_id = {r.span_id: r for r in roots}
    for step in told:
        (link,) = step.links
        target = by_id.get(link.span_id)
        assert link.trace_id == work_trace and link.reason == "work" and target is not None, step.name
        owned = [c for c in work_spans if c.parent_span_id == target.span_id]
        before = [c.start_ns for c in owned if c.role in ("queue", "claim")]
        pickup = [c.end_ns for c in owned if c.role == "pickup"]
        assert step.name == target.attributes[shared.NODE_NAME]
        assert min([target.start_ns, *before]) <= step.start_ns <= target.start_ns, step.name
        assert target.end_ns <= step.end_ns <= max([target.end_ns, *pickup]), step.name
        for key in (
            hub_attr.WAIT_QUEUE_MS,
            hub_attr.WAIT_CLAIM_MS,
            hub_attr.WAIT_ASK_MS,
            hub_attr.WAIT_PAUSE_MS,
            hub_attr.WAIT_PICKUP_MS,
        ):
            assert step.attributes[key] == target.attributes[key], f"{step.name}: {key}"
        assert step.attributes[shared.STEP_EPOCH] == target.attributes[shared.STEP_EPOCH]
        assert step.attributes[hub_attr.STEP_OUTCOME] == target.attributes[hub_attr.STEP_OUTCOME]
        assert step.attributes[hub_attr.STEP_KIND] == ("gate" if target.role == "gate" else "step")
    assert {link.span_id for step in told for link in step.links} == set(by_id), "a step root has no lifetime span"
    ordered = sorted(told, key=lambda s: s.start_ns)
    assert all(a.end_ns <= b.start_ns for a, b in pairwise(ordered)), "lifetime step spans overlap"
    waited = sorted(
        s.name for s in life_spans if s.role not in (ChunkRole.LIFETIME, ChunkRole.STEP, ChunkRole.COMPLETED)
    )
    assert waited == sorted(waits), f"chunk-level waits {waited}"
    assert {s.parent_span_id for s in life_spans if s is not life} == {life.span_id}
    covered = sorted((s.start_ns, s.end_ns, s.name) for s in life_spans if s is not life)
    assert all(a[1] <= b[0] for a, b in pairwise(covered)), f"lifetime spans overlap: {covered}"
    return ChunkTraces(lifetime=life, work=work)


# --------------------------------------------------------------------------- #
# Skeleton: what the spec says each step looks like


@dataclass(frozen=True)
class StepExpect:
    """One step root's expected skeleton: name, outcome, status, destination, child span and event names, and
    the previous-step link reason (``None`` for a first step).

    ``events`` and ``children`` compare as exact multisets; timing-varying counts use ``min_events`` /
    ``min_children``. ``invocation`` events are never compared."""

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


def assert_runner_nesting(spans: Sequence[ExportedSpan], *, workers: int) -> list[ExportedSpan]:
    """Every runner span sits in its chunk's trace; ``worker`` spans parent on a hub step root, others on a worker."""
    roots = {(s.trace_id, s.span_id): s for s in spans if s.role == "step"}
    runner = [s for s in spans if s.is_runner]
    worker_ids = {(s.trace_id, s.span_id) for s in runner if s.role == _WORKER_ROLE}
    assert len(worker_ids) == workers, f"expected {workers} runner worker spans, saw {len(worker_ids)}"
    for span in runner:
        assert span.trace_id == f"{chunk_trace_id(span.attributes[shared.CHUNK_ID]):032x}", (
            f"runner span {span.name!r}: trace {span.trace_id} is not its chunk's"
        )
        if span.role == _WORKER_ROLE:
            root = roots.get((span.trace_id, span.parent_span_id or ""))
            assert root is not None, f"{span.name!r}: parent {span.parent_span_id} is no exported step root"
        else:
            assert (span.trace_id, span.parent_span_id) in worker_ids, (
                f"runner span {span.name!r}: parent {span.parent_span_id} is no worker span in its trace"
            )
    for trace_id_, worker_id in worker_ids:
        invocations = [s for s in runner if s.parent_span_id == worker_id and s.role == _INVOCATION_ROLE]
        assert invocations, f"worker span {worker_id} in trace {trace_id_} carries no invoke_agent span"
    return runner


@dataclass(frozen=True)
class InvocationExpect:
    """What a node's ``invoke_agent`` spans carry: harness, reported model, declared model (``None`` if none)."""

    harness_id: str
    response_model: str
    request_model: str | None = None


def assert_invocations(runner: Sequence[ExportedSpan], expect: Mapping[str, InvocationExpect]) -> None:
    invocations = [s for s in runner if s.role == _INVOCATION_ROLE]
    nodes = {s.attributes[shared.NODE_NAME] for s in invocations}
    assert nodes == set(expect), f"invoke_agent spans for nodes {sorted(nodes)}, expected {sorted(expect)}"
    for span in invocations:
        node = span.attributes[shared.NODE_NAME]
        want = expect[node]
        where = f"{span.name!r} (node {node}, generation {span.attributes.get(runner_attr.INVOCATION_GENERATION)})"
        attrs = span.attributes
        assert attrs.get(runner_attr.GEN_AI_OPERATION_NAME) == runner_attr.INVOKE_AGENT, where
        assert attrs.get(shared.HARNESS_ID) == want.harness_id, f"{where}: harness {attrs.get(shared.HARNESS_ID)!r}"
        assert attrs.get(shared.GEN_AI_RESPONSE_MODEL) == want.response_model, (
            f"{where}: response model {attrs.get(shared.GEN_AI_RESPONSE_MODEL)!r}"
        )
        if want.request_model is not None:
            assert attrs.get(runner_attr.GEN_AI_REQUEST_MODEL) == want.request_model, (
                f"{where}: request model {attrs.get(runner_attr.GEN_AI_REQUEST_MODEL)!r}"
            )
        for tokens in (shared.GEN_AI_INPUT_TOKENS, shared.GEN_AI_OUTPUT_TOKENS):
            assert attrs.get(tokens, 0) > 0, f"{where}: {tokens} is {attrs.get(tokens)!r}"


# --------------------------------------------------------------------------- #
# Platform spans: what a worker command's request leaves in the file

_SERVER_SCOPE = "fastapi"
_CLIENT_SCOPE = "opentelemetry.instrumentation.httpx"
_QUERY_SCOPE = "opentelemetry.instrumentation.sqlalchemy"
_HUB_SERVICE = hub_attr.DEFAULT_SERVICE_NAME
_RUNNER_SERVICE = runner_attr.DEFAULT_SERVICE_NAME
#: The worker command whose runner request the runner passes through to the hub.
PASSTHROUGH_COMMAND = "runner work-items"


def told_until(fleet: Sequence[ExportedSpan]) -> dict[str, int]:
    """Per trace, the end of the last step root exported so far. A step is told only when it closes, so a platform
    span that began after this instant belongs to a step still running, whose root has not been told yet."""
    until: dict[str, int] = {}
    for span in fleet:
        if span.is_step_root:
            until[span.trace_id] = max(until.get(span.trace_id, 0), span.end_ns)
    return until


def assert_platform_nesting(
    fleet: Sequence[ExportedSpan], platform: Sequence[ExportedSpan], *, chained: str = PASSTHROUGH_COMMAND
) -> None:
    """Every worker command's span sits in a step trace of ``fleet`` and reaches that step's root through its
    ancestors; its runner request is its child. A command that began after the last step root told in its trace is
    in a step that has not closed, so its root is not exported yet and it is left out. A ``chained`` command's runner request carries on to the hub: a
    runner→hub client span under it, a hub server span under that, and a query under the hub's request."""
    by_id = {(s.trace_id, s.span_id): s for s in (*fleet, *platform)}
    kids: dict[tuple[str, str], list[ExportedSpan]] = {}
    for span in platform:
        if span.parent_span_id is not None:
            kids.setdefault((span.trace_id, span.parent_span_id), []).append(span)
    roots = {(s.trace_id, s.span_id): s for s in fleet if s.role == "step"}

    def under(span: ExportedSpan, scope: str, service: str) -> list[ExportedSpan]:
        return [c for c in kids.get((span.trace_id, span.span_id), []) if c.scope == scope and _service(c) == service]

    def descendants(span: ExportedSpan) -> Iterator[ExportedSpan]:
        for child in kids.get((span.trace_id, span.span_id), []):
            yield child
            yield from descendants(child)

    until = told_until(fleet)
    commands = [
        s for s in platform if s.scope == platform_attr.CLI_SCOPE and s.start_ns < until.get(s.trace_id, s.start_ns + 1)
    ]
    assert commands, "the file holds no worker command span"
    chains = 0
    for command in commands:
        name = command.attributes.get(platform_attr.CLI_COMMAND)
        chunk = str(command.attributes.get(shared.CHUNK_ID))
        assert command.trace_id == f"{chunk_trace_id(chunk):032x}", f"command {name!r}: not in chunk {chunk}'s trace"
        node = command
        while (node.trace_id, node.span_id) not in roots:
            assert node.parent_span_id is not None, f"command {name!r}: its ancestry ends at {node.name!r}"
            parent = by_id.get((node.trace_id, node.parent_span_id))
            assert parent is not None, f"command {name!r}: ancestor {node.parent_span_id} was never exported"
            node = parent
        servers = under(command, _SERVER_SCOPE, _RUNNER_SERVICE)
        assert servers, f"command {name!r}: no runner request is its child"
        if name != chained:
            continue
        clients = [c for server in servers for c in under(server, _CLIENT_SCOPE, _RUNNER_SERVICE)]
        assert clients, f"command {name!r}: the runner's request made no hub client span under it"
        hub = [h for client in clients for h in under(client, _SERVER_SCOPE, _HUB_SERVICE)]
        assert hub, f"command {name!r}: no hub request span is under the runner's client span"
        assert any(d.scope == _QUERY_SCOPE for h in hub for d in descendants(h)), (
            f"command {name!r}: no query span is under the hub's request"
        )
        chains += 1
    assert chains, f"no {chained!r} command span is in the file to carry the chain"


def _service(span: ExportedSpan) -> object:
    return span.resource.get(shared.SERVICE_NAME)


# --------------------------------------------------------------------------- #
# Leaks: what must never be in the file

#: The env var that names the directory a scenario's worker scripts write their lease token to.
PLANT_DIR_VAR = "BLIZZARD_E2E_PLANT_DIR"
#: Prepended to a worker script: writes the lease token the worker was handed to ``$PLANT_DIR_VAR/<lease id>-<pid>``.
PLANT_LEASE_TOKEN_SCRIPT = (
    "import os as _os, pathlib as _pathlib\n"
    f"_plant_dir = _os.environ.get({PLANT_DIR_VAR!r})\n"
    "if _plant_dir:\n"
    "    _name = _os.environ['BLIZZARD_LEASE_ID'] + '-' + str(_os.getpid())\n    _pathlib.Path(_plant_dir, _name).write_text(_os.environ['BLIZZARD_LEASE_TOKEN'])\n"
)
#: The body a worker submits as an artifact, distinctive enough that a trace carrying it is a leak.
SENTINEL_ARTIFACT_BODY = "LEAK-SENTINEL artifact body that no span may carry"


def planted_lease_tokens(plant_dir: Path) -> list[str]:
    """The lease tokens the workers wrote into ``plant_dir``."""
    return [path.read_text() for path in sorted(plant_dir.iterdir())] if plant_dir.is_dir() else []


def stashed_route_tokens(config: RunnerConfig) -> list[str]:
    """The route tokens the runner's own store holds."""
    engine = sqlalchemy.create_engine(config.db_url)
    try:
        with engine.connect() as connection:
            return [row[0] for row in connection.execute(sqlalchemy.select(route_tokens.c.token))]
    finally:
        engine.dispose()


def enroll_runner(hub: httpx.Client, config: RunnerConfig) -> RunnerConfig:
    """``config`` with a bearer the hub resolves to this runner. The hub continues a trace only for a caller whose
    credential resolves, so a runner that is not enrolled leaves no hub span under its requests."""
    registered = hub.post(
        "/api/fleet/runners",
        json={
            "runner_id": config.runner_id,
            "workspace_id": config.workspace_id,
            "url": f"http://{config.host}:{config.port}",
        },
    )
    assert registered.status_code == 201, registered.text
    enrolled = hub.post(f"/api/runners/{config.runner_id}/enrollments")
    assert enrolled.status_code == 201, enrolled.text
    return dataclasses.replace(config, hub_token=enrolled.json()["token"])


# --------------------------------------------------------------------------- #
# The running collector


def whole_chunk_decision_wait(*, settle_seconds: int, sweep_seconds: int, longest_chunk_seconds: int) -> int:
    """A ``decision_wait`` that holds a chunk's whole trace (``docs/deployment/tracing.md``, Platform spans, Tail
    sampling): counted from the chunk's first span, it spans the chunk, the hub's settle and a sweep, with a second
    to spare. Only a short-lived chunk, as in a test, can afford one."""
    return longest_chunk_seconds + settle_seconds + sweep_seconds + 1


class FleetCollector:
    """One collector per test: ``endpoint`` for the hub's ``OTEL_EXPORTER_OTLP_ENDPOINT``, ``spans()`` once the
    scenario's chunk has closed. ``binary`` is ``None`` where no usable collector exists, and then the hub runs
    untraced and :meth:`require` skips the traces subtest."""

    def __init__(self, binary: str | None, workdir: Path, *, decision_wait_seconds: int | None = None) -> None:
        self.binary = binary
        self._decision_wait = decision_wait_seconds
        self._workdir = workdir
        self._export = workdir / "traces.jsonl"
        self._received = workdir / "received.jsonl"
        self._log = workdir / "collector.log"
        self._proc: subprocess.Popen[str] | None = None
        self._port = 0
        self._workers = 0
        self._spans: list[ExportedSpan] | None = None
        self._all: list[ExportedSpan] | None = None

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
            self._tail_sampling() + "receivers:\n"
            "  otlp:\n"
            "    protocols:\n"
            "      http:\n"
            f"        endpoint: 127.0.0.1:{self._port}\n"
            "exporters:\n"
            "  file:\n"
            f"    path: {self._export}\n"
            f"{self._received_exporter()}"
            "service:\n"
            "  telemetry:\n"
            "    metrics:\n"
            "      level: none\n"
            "  pipelines:\n"
            "    traces:\n"
            f"{self._processors()}"
            "      exporters: [file]\n"
            f"{self._received_pipeline()}"
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

    def _tail_sampling(self) -> str:
        """The overlay's ``tail_sampling`` processor, where the run has one: it keeps a trace only if it holds a
        fleet step root, the shape of the root-keyed policies operators write, so a trace split by its
        ``decision_wait`` differs in content, not just timing."""
        if self._decision_wait is None:
            return ""
        return (
            "processors:\n"
            "  tail_sampling:\n"
            f"    decision_wait: {self._decision_wait}s\n"
            "    policies:\n"
            "      - name: keep-step-roots\n"
            "        type: ottl_condition\n"
            "        ottl_condition:\n"
            "          error_mode: ignore\n"
            "          span:\n"
            f"            - 'attributes[\"{hub_attr.STEP_OUTCOME}\"] != nil'\n"
        )

    def _received_exporter(self) -> str:
        return "" if self._decision_wait is None else f"  file/received:\n    path: {self._received}\n"

    def _received_pipeline(self) -> str:
        """A second pipeline over the same receiver with no sampler: what was sent, to read the sampler's work against."""
        if self._decision_wait is None:
            return ""
        return (
            "    traces/received:\n"
            "      receivers: [otlp]\n"
            "      processors: [batch]\n"
            "      exporters: [file/received]\n"
        )

    def _processors(self) -> str:
        """The traces pipeline's processor line: the sampler ahead of the documented ``batch``."""
        return "" if self._decision_wait is None else "      processors: [tail_sampling, batch]\n"

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

    def _read(self, path: Path | None = None) -> list[ExportedSpan]:
        path = path or self._export
        if not path.exists():
            return []
        try:
            return parse_export(path.read_text())
        except json.JSONDecodeError:  # a line the exporter is still writing
            return []

    def _read_fleet(self, path: Path | None = None) -> list[ExportedSpan]:
        return [s for s in self._read(path) if is_fleet(s)]

    @property
    def _arrived(self) -> Path:
        """Where spans show up first: the unsampled copy where a sampler holds the main file's back."""
        return self._export if self._decision_wait is None else self._received

    @staticmethod
    def _counts(spans: Sequence[ExportedSpan]) -> tuple[int, int]:
        return sum(1 for s in spans if s.is_step_root), sum(1 for s in spans if s.is_runner and s.role == _WORKER_ROLE)

    def await_workers(self, workers: int, *, drive: Callable[[], None] | None = None, timeout: float = 60.0) -> None:
        if self._proc is None:
            return
        self._workers = workers
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if drive is not None:
                drive()
            if self._counts(self._read_fleet(self._arrived))[1] >= workers:
                return
            time.sleep(0.25)

    def spans(self, *, roots: int, exact: bool = True, timeout: float = 60.0) -> list[ExportedSpan]:
        """Wait until ``roots`` step roots, and the runner ``worker`` spans :meth:`await_workers` expects, are in the
        file; stop the collector so the file is complete, and parse it. Later calls read the same parse.

        ``exact`` demands no more roots than that; a scenario whose chunk is still looping when it stops reads
        only the first ``roots`` steps (the caller's :func:`assert_skeleton` takes the matching prefix)."""
        if self._spans is None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                seen_roots, seen_workers = self._counts(self._read_fleet())
                if seen_roots >= roots and seen_workers >= self._workers:
                    break
                time.sleep(0.25)
            self.stop()
            self._all = self._read()
            self._spans = [s for s in self._all if is_fleet(s)]
        seen = [s.name for s in self._spans if s.is_step_root]
        assert len(seen) == roots or (not exact and len(seen) > roots), (
            f"expected {roots} step roots in the collector's file, saw {len(seen)}: {seen}"
        )
        return self._spans

    def received_spans(self, *, roots: int, timeout: float = 60.0) -> list[ExportedSpan]:
        """Every span the collector received, sampler or no: wait until ``roots`` step roots and the runner ``worker``
        spans :meth:`await_workers` expects have arrived, then read the unsampled copy."""
        assert self._decision_wait is not None, "only a tail-sampling collector keeps a copy of what it received"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            seen_roots, seen_workers = self._counts(self._read_fleet(self._received))
            if seen_roots >= roots and seen_workers >= self._workers:
                break
            time.sleep(0.25)
        return self._read(self._received)

    def kept_spans(self) -> list[ExportedSpan]:
        """Every span the sampler kept: stop the collector so its file is complete, then read it."""
        self.stop()
        return self._read()

    def platform_spans(self) -> list[ExportedSpan]:
        """Every non-fleet span in the file, read by the same :meth:`spans` call that completed it."""
        assert self._all is not None, "read the fleet spans first — that is what completes the file"
        return [s for s in self._all if not is_fleet(s)]

    def assert_no_leaks(self, planted: Mapping[str, Sequence[str]]) -> None:
        """No planted value is anywhere in the raw file — every resource, span, event and attribute — read by the
        same :meth:`spans` call that completed it. A kind with nothing planted fails: that scan would pass empty."""
        assert self._all is not None, "read the fleet spans first — that is what completes the file"
        raw = self._export.read_text()
        assert raw, "the collector's file is empty"
        for kind, values in planted.items():
            assert values and all(values), f"nothing was planted for {kind}: the scan would pass vacuously"
            for value in values:
                assert value not in raw, f"the exported file carries a planted {kind}"

    def traces(self, *, roots: int, exact: bool = True, decision_ids: Sequence[str] = ()) -> list[StepTrace]:
        """The step traces, once they have passed the shape and identity checks."""
        spans = self.spans(roots=roots, exact=exact)
        assert_conforms(spans)
        traces = traces_of(spans)
        assert_identity(traces, decision_ids=decision_ids)
        return traces

    def runner_spans(self, *, roots: int, workers: int, exact: bool = True) -> list[ExportedSpan]:
        """The runner spans, once every span conforms and they nest on the hub roots."""
        spans = self.spans(roots=roots, exact=exact)
        assert_conforms(spans)
        assert_identity(traces_of(spans))
        return assert_runner_nesting(spans, workers=workers)


@contextlib.contextmanager
def fleet_collector(workdir: Path, *, decision_wait_seconds: int | None = None) -> Iterator[FleetCollector]:
    """A collector running the documented config; ``decision_wait_seconds`` puts a tail-sampling stage with that
    ``decision_wait`` ahead of its ``batch``."""
    collector = FleetCollector(resolve_collector(), workdir, decision_wait_seconds=decision_wait_seconds)
    if collector.available:
        collector.start()
    try:
        yield collector
    finally:
        collector.stop()


class RunnerSweep:
    """The runner's real sweep via its composition root; :meth:`plant` before the first tick, then :meth:`drain`.

    ``process`` is the one traced graph the scenario's ticks and local API share, so a worker command's request
    lands in the runner's platform spans; ``None`` where there is no collector, and the scenario runs untraced."""

    def __init__(self, process: RunnerProcess | None, collector: FleetCollector) -> None:
        self.process = process
        self._sweep = process.trace_sweep if process is not None else None
        self._collector = collector

    def plant(self) -> None:
        if self._sweep is not None:
            self._sweep.sweep()

    def drain(self, *, workers: int) -> None:
        if self._sweep is not None:
            self._collector.await_workers(workers, drive=self._sweep.sweep)


@contextlib.contextmanager
def runner_sweep(
    config: RunnerConfig, collector: FleetCollector, *, settle_seconds: int = 0, sweep_seconds: int = 1
) -> Iterator[RunnerSweep]:
    """The runner's one process graph, traced like the hub: fleet sweep and platform spans (at a zero root sample
    ratio, so only spans parented on a step's context export) to ``collector``."""
    if not collector.available:
        yield RunnerSweep(None, collector)
        return
    environ = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = collector.endpoint
    environ["OTEL_BSP_SCHEDULE_DELAY"] = "200"  # inline platform spans reach the file before the sweep's roots do
    traced = dataclasses.replace(
        config,
        tracing=TracingConfig(
            sweep_seconds=sweep_seconds, settle_seconds=settle_seconds, platform=True, platform_sample_ratio=0.0
        ),
    )
    with mock.patch.dict(os.environ, environ, clear=True):
        process = build_runner_process(
            traced, environ=environ, platform_tracing=build_runner_platform_tracing(traced, environ)
        )
    try:
        assert process.trace_sweep is not None, "the collector endpoint did not enable the runner's trace sweep"
        yield RunnerSweep(process, collector)
    finally:
        process.close()
