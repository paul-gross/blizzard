"""Derived trace and span ids for a chunk's trace — the cross-daemon id contract.

Ids are derived, never random, so a replay, a later slice and an inline span made elsewhere land in
the same trace without anything crossing the wire. Contract:
``blizzard-product:/delivered/tracing/fleet-spans/spec/spans.md`` §Identity. The ``v1`` prefixes are the
contract version; changing the derivation is breaking."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

# OpenTelemetry's ``TraceFlags.SAMPLED`` bit, carried by every derived context.
SAMPLED = 0x01

_TRACE_PREFIX = "blizzard-trace/v2/"
_SPAN_PREFIX = "blizzard-span/v1/"
_CHUNK_SPAN_PREFIX = "blizzard-chunk-span/v2/"
_TRACE_BYTES = 16
_SPAN_BYTES = 8


class SpanRole(StrEnum):
    """A span's role within its step's trace — the spec's role column."""

    STEP = "step"
    GATE = "gate"
    QUEUE = "queue"
    CLAIM = "claim"
    ASK = "ask"
    PAUSE = "pause"
    PICKUP = "pickup"
    HUB_EXEC = "hub-exec"


class RunnerSpanRole(StrEnum):
    """A runner span's role within a lease's slice of its step's trace.

    Contract: ``blizzard-product:/delivered/tracing/runner-spans/spec/spans.md``."""

    WORKER = "runner/worker"
    INVOCATION = "runner/invocation"
    ASK_PARK = "runner/ask-park"
    PAUSE_PARK = "runner/pause-park"
    OVERLOAD = "runner/overload"
    TAKEOVER = "runner/takeover"


class ChunkRole(StrEnum):
    """A chunk-level span's role: the chunk span, its completion marker, and the waits no step owns."""

    CHUNK = "chunk"
    COMPLETED = "chunk/completed"
    BACKLOG = "chunk/backlog-wait"
    ESCALATION = "chunk/escalation-wait"
    PAUSE = "chunk/pause-wait"


@dataclass(frozen=True)
class StepKey:
    """The identity of one step: an attempt at a node, or one human decision.

    Neither store has a node-step id; the hub already names an attempt by ``(chunk_id, epoch)``,
    and a gate additionally by its decision."""

    chunk_id: str
    epoch: int
    decision_id: str | None = None

    @classmethod
    def attempt(cls, chunk_id: str, epoch: int) -> StepKey:
        return cls(chunk_id, epoch)

    @classmethod
    def gate(cls, chunk_id: str, epoch: int, decision_id: str) -> StepKey:
        return cls(chunk_id, epoch, decision_id)

    def text(self) -> str:
        base = f"{self.chunk_id}/{self.epoch}"
        if self.decision_id is None:
            return base
        return f"{base}/gate/{self.decision_id}"


def nonzero(digest: bytes) -> bytes:
    """The zero-id rule: an all-zero digest has its last byte set to ``01``."""
    if any(digest):
        return digest
    return digest[:-1] + b"\x01"


def chunk_trace_id(chunk_id: str) -> int:
    """The chunk's 128-bit trace id — the first 16 bytes of ``SHA-256("blizzard-trace/v2/" + chunk id)``."""
    digest = hashlib.sha256((_TRACE_PREFIX + chunk_id).encode("utf-8")).digest()[:_TRACE_BYTES]
    return int.from_bytes(nonzero(digest), "big")


def trace_id(key: StepKey) -> int:
    """A step's trace id: its chunk's, so every step of a chunk lands in one trace."""
    return chunk_trace_id(key.chunk_id)


def instant_text(at: datetime) -> str:
    """A UTC instant as ``YYYY-MM-DDTHH:MM:SS.ffffffZ``."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def chunk_span_id(chunk_id: str, role: ChunkRole = ChunkRole.CHUNK, at: datetime | None = None) -> int:
    """A chunk-level span's 64-bit id — the first 8 bytes of
    ``SHA-256("blizzard-chunk-span/v2/" + chunk id + "/" + role + "/" + instant)``; the chunk span has no instant."""
    text = f"{_CHUNK_SPAN_PREFIX}{chunk_id}/{role.value}/{instant_text(at) if at is not None else ''}"
    digest = hashlib.sha256(text.encode("utf-8")).digest()[:_SPAN_BYTES]
    return int.from_bytes(nonzero(digest), "big")


def chunk_context(chunk_id: str, role: ChunkRole = ChunkRole.CHUNK, at: datetime | None = None) -> DerivedContext:
    return DerivedContext(chunk_trace_id(chunk_id), chunk_span_id(chunk_id, role, at))


def span_id(key: StepKey, role: SpanRole | RunnerSpanRole, discriminator: str = "") -> int:
    """A span's 64-bit id — the first 8 bytes of
    ``SHA-256("blizzard-span/v1/" + key + "/" + role + "/" + discriminator)``.

    ``discriminator`` is the source row's own id, empty for a role that occurs once per step."""
    text = f"{_SPAN_PREFIX}{key.text()}/{role.value}/{discriminator}"
    digest = hashlib.sha256(text.encode("utf-8")).digest()[:_SPAN_BYTES]
    return int.from_bytes(nonzero(digest), "big")


def step_traceparent(chunk_id: str, epoch: int) -> str:
    """The W3C ``traceparent`` of a step's root span — what a worker's environment carries so its
    commands nest inside the step. Contract: ``blizzard-product:/delivered/tracing/platform-spans/spec/nesting.md``."""
    root = DerivedContext.of(StepKey.attempt(chunk_id, epoch), SpanRole.STEP)
    return format_traceparent(root.trace_id, root.span_id, root.trace_flags)


def format_traceparent(trace_id: int, span_id: int, trace_flags: int) -> str:
    """A version-``00`` W3C ``traceparent`` carrying the given ids and flags."""
    return f"00-{trace_id:032x}-{span_id:016x}-{trace_flags:02x}"


_HEX = frozenset("0123456789abcdef")


def _hex_field(text: str, width: int) -> int | None:
    if len(text) != width or not _HEX.issuperset(text):
        return None
    return int(text, 16)


def parse_traceparent(value: str) -> DerivedContext | None:
    """The context a W3C ``traceparent`` carries, or ``None`` for anything malformed.

    A zero trace or span id and the reserved version ``ff`` are malformed; a version above ``00``
    may carry further fields, which are ignored."""
    parts = value.strip().split("-")
    if len(parts) < 4:
        return None
    version, trace, span, flags = parts[:4]
    version_number = _hex_field(version, 2)
    if version_number is None or version_number == 0xFF or (version_number == 0 and len(parts) != 4):
        return None
    trace_number = _hex_field(trace, 32)
    span_number = _hex_field(span, 16)
    flag_number = _hex_field(flags, 2)
    if not trace_number or not span_number or flag_number is None:
        return None
    return DerivedContext(trace_number, span_number, flag_number)


@dataclass(frozen=True)
class DerivedContext:
    """One span's derived context; every derived context is sampled."""

    trace_id: int
    span_id: int
    trace_flags: int = SAMPLED

    @classmethod
    def of(cls, key: StepKey, role: SpanRole | RunnerSpanRole, discriminator: str = "") -> DerivedContext:
        return cls(trace_id(key), span_id(key, role, discriminator))


def step_root(key: StepKey) -> DerivedContext:
    return DerivedContext.of(key, SpanRole.STEP if key.decision_id is None else SpanRole.GATE)
