"""Per-kind SSE frame wire models — the one description each frame kind's
payload has, mirrored by the golden corpus at ``contracts/sse/``.

Every model is ``extra="forbid"``. Presence-vs-null is load-bearing and not uniform, so each
model owns its serialization (:meth:`SseFramePayload.to_payload`): an optional field is
omitted when unset unless named in :attr:`SseFramePayload._null_when_absent`."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from blizzard.foundation.event_log import EventLogSeverity
from blizzard.foundation.hub_event_types import (
    ChunkChangeCause,
    HubEventType,
    RunnerChangeKind,
)


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
    #: The routed runner's registered name at publish time; omitted whenever ``runner_id`` is.
    runner_name: str | None = None
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
    #: The runner's registered name at publish time; omitted when the registry holds none.
    runner_name: str | None = None
    kind: RunnerChangeKind
    by: str | None = None
    reason: str | None = None
    key: str | None = None


class EventLoggedPayload(SseFramePayload):
    severity: EventLogSeverity
    kind: str
    chunk_id: str | None
    runner_id: str | None
    #: The runner's registered name at publish time; omitted whenever ``runner_id`` is ``null``.
    runner_name: str | None = None
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
