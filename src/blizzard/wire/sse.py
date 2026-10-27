"""Per-kind SSE frame wire models — one payload model per frame kind, mirrored by the golden
corpus at ``contracts/sse/``."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from blizzard.foundation.event_log import EventLogSeverity
from blizzard.foundation.hub_event_types import (
    ChunkChangeCause,
    HostChangeKind,
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
        return {
            name: value
            for name, value in self.model_dump().items()
            if value is not None or name in self._null_when_absent
        }


class ChunkChangedPayload(SseFramePayload):
    chunk_id: str
    status: str
    #: ``status`` is terminal — the hub's judgment, so a client never decides it from the value.
    terminal: bool
    prev_status: str | None = None
    prev_node: str | None = None
    node: str | None = None
    runner_id: str | None = None
    #: The routed runner's registered name at publish time; omitted whenever ``runner_id`` is.
    runner_name: str | None = None
    cause: ChunkChangeCause | None = None
    graph_id: str | None = None
    #: Who deleted the chunk — set on the ``deleted`` cause; omitted on every other cause.
    by: str | None = None
    key: str | None = None
    #: The releasing runner's own reason — rides the ``released`` cause; omitted on every other.
    reason: str | None = None
    #: The id of the chunk's project.
    project_id: str | None = None


class QuestionAskedPayload(SseFramePayload):
    chunk_id: str
    question_id: str
    key: str | None = None
    #: The id of the chunk's project.
    project_id: str | None = None


class QuestionAnsweredPayload(SseFramePayload):
    chunk_id: str
    question_id: str
    key: str | None = None
    #: The id of the chunk's project.
    project_id: str | None = None


class DecisionOpenedPayload(SseFramePayload):
    chunk_id: str
    decision_id: str
    key: str | None = None
    #: The id of the chunk's project.
    project_id: str | None = None


class DecisionResolvedPayload(SseFramePayload):
    chunk_id: str
    decision_id: str
    key: str | None = None
    #: The id of the chunk's project.
    project_id: str | None = None


class QueueChangedPayload(SseFramePayload):
    pass


class RunnerChangedPayload(SseFramePayload):
    runner_id: str
    #: The runner's registered name at publish time; omitted when the registry holds none.
    runner_name: str | None = None
    kind: RunnerChangeKind
    #: Actor on ``added``, pause/resume (local included) and retirement; absent on other kinds.
    by: str | None = None
    reason: str | None = None
    key: str | None = None


class HostChangedPayload(SseFramePayload):
    host_id: str
    #: The host's name at publish time.
    host_name: str | None = None
    kind: HostChangeKind
    by: str | None = None
    key: str | None = None


class EventLoggedPayload(SseFramePayload):
    severity: EventLogSeverity
    kind: str
    chunk_id: str | None
    runner_id: str | None
    #: The runner's name when supplied to the recorder; omitted if no name was supplied.
    runner_name: str | None = None
    key: str | None = None
    #: The id of the named chunk's project; omitted when no chunk is named.
    project_id: str | None = None

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
    HubEventType.HOST_CHANGED: HostChangedPayload,
    HubEventType.EVENT_LOGGED: EventLoggedPayload,
}
