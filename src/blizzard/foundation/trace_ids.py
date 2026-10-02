"""Derived trace and span ids for a step's trace — the cross-daemon id contract.

Ids are derived, never random, so a replay, a later slice and an inline span made elsewhere land in
the same trace without anything crossing the wire. Contract:
``blizzard-product:/plans/tracing/fleet-spans/spec/spans.md`` §Identity. The ``v1`` prefixes are the
contract version; changing the derivation is breaking."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum

# OpenTelemetry's ``TraceFlags.SAMPLED`` bit, carried by every derived context.
SAMPLED = 0x01

_TRACE_PREFIX = "blizzard-trace/v1/"
_SPAN_PREFIX = "blizzard-span/v1/"
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

    Contract: ``blizzard-product:/plans/tracing/runner-spans/spec/spans.md``."""

    WORKER = "runner/worker"
    INVOCATION = "runner/invocation"
    ASK_PARK = "runner/ask-park"
    PAUSE_PARK = "runner/pause-park"
    OVERLOAD = "runner/overload"
    TAKEOVER = "runner/takeover"


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


def trace_id(key: StepKey) -> int:
    """The step's 128-bit trace id — the first 16 bytes of ``SHA-256("blizzard-trace/v1/" + key)``."""
    digest = hashlib.sha256((_TRACE_PREFIX + key.text()).encode("utf-8")).digest()[:_TRACE_BYTES]
    return int.from_bytes(nonzero(digest), "big")


def span_id(key: StepKey, role: SpanRole | RunnerSpanRole, discriminator: str = "") -> int:
    """A span's 64-bit id — the first 8 bytes of
    ``SHA-256("blizzard-span/v1/" + key + "/" + role + "/" + discriminator)``.

    ``discriminator`` is the source row's own id, empty for a role that occurs once per step."""
    text = f"{_SPAN_PREFIX}{key.text()}/{role.value}/{discriminator}"
    digest = hashlib.sha256(text.encode("utf-8")).digest()[:_SPAN_BYTES]
    return int.from_bytes(nonzero(digest), "big")


def step_traceparent(chunk_id: str, epoch: int) -> str:
    """The W3C ``traceparent`` of a step's root span — what a worker's environment carries so its
    commands nest inside the step. Contract: ``blizzard-product:/plans/tracing/platform-spans/spec/nesting.md``."""
    root = DerivedContext.of(StepKey.attempt(chunk_id, epoch), SpanRole.STEP)
    return f"00-{root.trace_id:032x}-{root.span_id:016x}-{root.trace_flags:02x}"


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
