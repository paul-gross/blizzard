"""Per-kind SSE frame wire models for the runner's stream, beside the
hub's own vocabulary in :mod:`blizzard.wire.sse`; mirrored by the golden corpus's runner scope
at ``contracts/sse/runner/``. Frames are thin id-and-cause notifications, and every model
reuses :class:`~blizzard.wire.sse.SseFramePayload`'s present-when-meaningful serialization."""

from __future__ import annotations

from typing import ClassVar

from blizzard.foundation.runner_event_types import (
    AskChangeCause,
    EnvironmentChangeCause,
    EscalationChangeCause,
    LeaseChangeCause,
    RunnerEventType,
    TakeoverChangeCause,
)
from blizzard.wire.sse import SseFramePayload


class LeaseChangedPayload(SseFramePayload):
    lease_id: str
    chunk_id: str
    cause: LeaseChangeCause


class AskChangedPayload(SseFramePayload):
    lease_id: str
    chunk_id: str
    question_id: str
    cause: AskChangeCause


class EscalationChangedPayload(SseFramePayload):
    chunk_id: str
    cause: EscalationChangeCause
    lease_id: str | None = None


class TakeoverChangedPayload(SseFramePayload):
    chunk_id: str
    takeover_id: str
    cause: TakeoverChangeCause


class EnvironmentChangedPayload(SseFramePayload):
    chunk_id: str
    environment_id: str
    cause: EnvironmentChangeCause


class FactChangedPayload(SseFramePayload):
    """A hub-bound fact was enqueued or acked — mirrors the hub's own ``event-logged``
    shape: ``chunk_id``/``lease_id`` ride as a present ``null`` rather than omitted,
    since a runner-wide fact (e.g. a chunk-less ``event.recorded``) legitimately carries
    neither. Never a heartbeat — those ride elsewhere, elapsed-time-derived."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None

    _null_when_absent: ClassVar[frozenset[str]] = frozenset({"chunk_id", "lease_id"})


#: Mirrors ``blizzard.wire.sse.SSE_FRAME_MODELS``.
RUNNER_SSE_FRAME_MODELS: dict[str, type[SseFramePayload]] = {
    RunnerEventType.LEASE_CHANGED: LeaseChangedPayload,
    RunnerEventType.ASK_CHANGED: AskChangedPayload,
    RunnerEventType.ESCALATION_CHANGED: EscalationChangedPayload,
    RunnerEventType.TAKEOVER_CHANGED: TakeoverChangedPayload,
    RunnerEventType.ENVIRONMENT_CHANGED: EnvironmentChangedPayload,
    RunnerEventType.FACT_CHANGED: FactChangedPayload,
}
