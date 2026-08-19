"""Per-kind SSE frame wire models for the runner's stream (blizzard#317 Phase 2), beside the
hub's own vocabulary in :mod:`blizzard.wire.sse`; mirrored by the golden corpus's runner scope
at ``contracts/sse/runner/``. D6: frames are thin id-and-cause notifications, and every model
reuses :class:`~blizzard.wire.sse.SseFramePayload`'s present-when-meaningful serialization.
``key``/``node_name`` mirror the hub's own frame shape for a future runner event log; no
runner-side consumer reads either today — `local-panel` invalidates by ``chunk_id`` alone."""

from __future__ import annotations

from typing import ClassVar, Literal

from blizzard.wire.sse import SseFramePayload

#: What caused a ``lease-changed`` frame: ``created``/``spawned`` are not closures, ``dormant`` is
#: an open-lease park; the other seven mirror ``ClosedLeaseRecord.reason``'s closure vocabulary.
LeaseChangeCause = Literal[
    "created",
    "spawned",
    "dormant",
    "transitioned",
    "reaped",
    "failed",
    "escalated",
    "parked",
    "released",
    "preempted",
]

#: What caused an ``ask-changed`` frame — a worker's question recorded, or its answer
#: landing (the park resume the answer drives).
AskChangeCause = Literal["asked", "answered"]

#: What caused an ``escalation-changed`` frame — opened at an exhausted retry budget, or
#: closed by supersession (a fresh lease minted, or the hub resolving it terminally).
EscalationChangeCause = Literal["opened", "closed"]

#: What caused a ``takeover-changed`` frame.
TakeoverChangeCause = Literal["opened", "closed"]

#: What caused an ``environment-changed`` frame.
EnvironmentChangeCause = Literal["bound", "released"]


class LeaseChangedPayload(SseFramePayload):
    lease_id: str
    chunk_id: str
    cause: LeaseChangeCause
    node_name: str | None = None
    key: str | None = None


class AskChangedPayload(SseFramePayload):
    lease_id: str
    chunk_id: str
    question_id: str
    cause: AskChangeCause
    key: str | None = None


class EscalationChangedPayload(SseFramePayload):
    chunk_id: str
    cause: EscalationChangeCause
    lease_id: str | None = None
    key: str | None = None


class TakeoverChangedPayload(SseFramePayload):
    chunk_id: str
    takeover_id: str
    cause: TakeoverChangeCause
    key: str | None = None


class EnvironmentChangedPayload(SseFramePayload):
    chunk_id: str
    environment_id: str
    cause: EnvironmentChangeCause
    key: str | None = None


class FactChangedPayload(SseFramePayload):
    """A hub-bound fact was enqueued or acked (``bzh:facts-not-status``) — mirrors the hub's
    own ``event-logged`` shape: ``chunk_id``/``lease_id`` ride as a present ``null`` rather
    than omitted, since a runner-wide fact (e.g. a chunk-less ``event.recorded``) legitimately
    carries neither. Never a heartbeat — those ride elsewhere, elapsed-time-derived (D7)."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    key: str | None = None

    _null_when_absent: ClassVar[frozenset[str]] = frozenset({"chunk_id", "lease_id"})


#: Keyed by the broker's own event-type constants, duplicated here as literals rather than
#: imported, since importing back would cycle (mirrors ``blizzard.wire.sse.SSE_FRAME_MODELS``).
RUNNER_SSE_FRAME_MODELS: dict[str, type[SseFramePayload]] = {
    "lease-changed": LeaseChangedPayload,
    "ask-changed": AskChangedPayload,
    "escalation-changed": EscalationChangedPayload,
    "takeover-changed": TakeoverChangedPayload,
    "environment-changed": EnvironmentChangedPayload,
    "fact-changed": FactChangedPayload,
}
