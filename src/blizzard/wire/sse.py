"""Per-kind SSE frame wire models (issue #235) — the producer's own description of each
frame kind's payload — the one description the wire has, mirrored by the golden corpus
at ``contracts/sse/``.

Every model is ``extra="forbid"``: a golden case carrying a field the model does not
declare fails to parse, which is the contract test's parse half.

Presence-vs-null is load-bearing and not uniform across this wire, so each model owns
its own serialization (:meth:`SseFramePayload.to_payload`) rather than a blanket
``model_dump(exclude_none=True)``: every optional field is omitted when unset except
:attr:`EventLoggedPayload.chunk_id`, which stays a present ``null`` for a runner-scoped
event (``broker.py``'s ``publish_event_logged``, issue #213) — named in
:attr:`SseFramePayload._null_when_absent`.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

#: What fact family drove a ``chunk-changed`` frame (issue #212) — each emit site names
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
    "detached",
    "paused",
    "resumed",
    "stopped",
    "hub-advanced",
]

#: What a ``runner-changed`` frame reports (issue #151) — see
#: :func:`blizzard.hub.events.broker.EventBroker.publish_runner_changed`.
RunnerChangeKind = Literal[
    "registered", "heartbeat", "paused", "resumed", "locally-paused", "locally-resumed", "external-usage"
]


class SseFramePayload(BaseModel):
    """Base for every SSE frame kind's payload model."""

    model_config = ConfigDict(extra="forbid")

    #: Field names that stay in the payload as a present ``null`` when unset, rather than
    #: being omitted. Empty for every kind but ``event-logged``.
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
    severity: str
    kind: str
    chunk_id: str | None
    runner_id: str
    key: str | None = None

    _null_when_absent: ClassVar[frozenset[str]] = frozenset({"chunk_id"})


#: Keyed by the broker's own SSE event-type constants (``blizzard.hub.events.broker``) —
#: duplicated here as literals rather than imported, since the broker imports this
#: module and importing back would cycle. The contract test's corpus-closure assertion
#: proves the two stay in lockstep.
SSE_FRAME_MODELS: dict[str, type[SseFramePayload]] = {
    "chunk-changed": ChunkChangedPayload,
    "question-asked": QuestionAskedPayload,
    "question-answered": QuestionAnsweredPayload,
    "decision-opened": DecisionOpenedPayload,
    "decision-resolved": DecisionResolvedPayload,
    "queue-changed": QueueChangedPayload,
    "runner-changed": RunnerChangedPayload,
    "event-logged": EventLoggedPayload,
}
