"""Per-kind SSE frame wire models — the one description each frame kind's
payload has, mirrored by the golden corpus at ``contracts/sse/``.

Every model is ``extra="forbid"``. Presence-vs-null is load-bearing and not uniform, so each
model owns its serialization (:meth:`SseFramePayload.to_payload`): an optional field is
omitted when unset unless named in :attr:`SseFramePayload._null_when_absent`."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from blizzard.foundation.event_log import EventLogSeverity
from blizzard.foundation.hub_event_types import HubEventType

#: What fact family drove a ``chunk-changed`` frame — each emit site names
#: its own cause statically.
ChunkChangeCause = Literal[
    "minted",
    "promoted",
    "edited",
    "grouped",
    "claimed",
    "node-completed",
    "migrated",
    "decision-submitted",
    "decision-resolved",
    "question-asked",
    "question-answered",
    "escalated",
    "requeued",
    "restarted",
    "detached",
    "paused",
    "resumed",
    "stopped",
    "completed",
    "hub-advanced",
    "deleted",
]

#: The causes the activity read backfills from a durable chunk fact: all but ``edited``, which records none.
ActivityChunkChangeCause = Literal[
    "minted",
    "promoted",
    "grouped",
    "claimed",
    "node-completed",
    "migrated",
    "decision-submitted",
    "decision-resolved",
    "question-asked",
    "question-answered",
    "escalated",
    "requeued",
    "restarted",
    "detached",
    "paused",
    "resumed",
    "stopped",
    "completed",
    "hub-advanced",
    "deleted",
]

#: What a ``runner-changed`` frame reports — see
#: :func:`blizzard.hub.events.broker.EventBroker.publish_runner_changed`.
RunnerChangeKind = Literal[
    "registered",
    "heartbeat",
    "paused",
    "resumed",
    "locally-paused",
    "locally-resumed",
    "external-usage",
    "retired",
    "reinstated",
    "token-revoked",
]


class SseFramePayload(BaseModel):
    """Base for every SSE frame kind's payload model."""

    model_config = ConfigDict(extra="forbid")

    #: Field names that stay in the payload as a present ``null`` when unset, rather than
    #: being omitted. Empty by default; a subclass overrides per its own fields.
    _null_when_absent: ClassVar[frozenset[str]] = frozenset()

    def to_payload(self) -> dict[str, object]:
        """Present-when-meaningful: an optional field is omitted when it is ``None``,
        except the fields named in :attr:`_null_when_absent`, which stay present."""
        return {
            name: value
            for name, value in self.model_dump().items()
            if value is not None or name in self._null_when_absent
        }


class ChunkChangedPayload(SseFramePayload):
    chunk_id: str
    status: str
    prev_status: str | None = None
    prev_node: str | None = None
    node: str | None = None
    runner_id: str | None = None
    cause: ChunkChangeCause | None = None
    graph_id: str | None = None
    #: Who deleted the chunk — rides the ``deleted`` cause, mirroring
    #: :attr:`RunnerChangedPayload.by`; omitted on every other cause.
    by: str | None = None
    key: str | None = None


class QuestionAskedPayload(SseFramePayload):
    chunk_id: str
    question_id: str
    key: str | None = None


class QuestionAnsweredPayload(SseFramePayload):
    chunk_id: str
    question_id: str
    key: str | None = None


class DecisionOpenedPayload(SseFramePayload):
    chunk_id: str
    decision_id: str
    key: str | None = None


class DecisionResolvedPayload(SseFramePayload):
    chunk_id: str
    decision_id: str
    key: str | None = None


class QueueChangedPayload(SseFramePayload):
    pass


class RunnerChangedPayload(SseFramePayload):
    runner_id: str
    kind: RunnerChangeKind
    by: str | None = None
    reason: str | None = None
    key: str | None = None


class EventLoggedPayload(SseFramePayload):
    severity: EventLogSeverity
    kind: str
    chunk_id: str | None
    runner_id: str | None
    key: str | None = None

    _null_when_absent: ClassVar[frozenset[str]] = frozenset({"chunk_id", "runner_id"})


#: Keyed by :class:`HubEventType`. The key set is pinned against the broker's vocabulary by
#: ``tests/test_sse_contract.py::TestCorpusClosure``.
SSE_FRAME_MODELS: dict[str, type[SseFramePayload]] = {
    HubEventType.CHUNK_CHANGED: ChunkChangedPayload,
    HubEventType.QUESTION_ASKED: QuestionAskedPayload,
    HubEventType.QUESTION_ANSWERED: QuestionAnsweredPayload,
    HubEventType.DECISION_OPENED: DecisionOpenedPayload,
    HubEventType.DECISION_RESOLVED: DecisionResolvedPayload,
    HubEventType.QUEUE_CHANGED: QueueChangedPayload,
    HubEventType.RUNNER_CHANGED: RunnerChangedPayload,
    HubEventType.EVENT_LOGGED: EventLoggedPayload,
}
