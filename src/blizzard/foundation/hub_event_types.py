"""The hub's SSE frame kinds and the causes a frame names."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal


class HubEventType(StrEnum):
    """Every hub SSE frame kind — the ``event:`` name a frame carries."""

    CHUNK_CHANGED = "chunk-changed"
    QUESTION_ASKED = "question-asked"
    QUESTION_ANSWERED = "question-answered"
    DECISION_OPENED = "decision-opened"
    DECISION_RESOLVED = "decision-resolved"
    QUEUE_CHANGED = "queue-changed"
    RUNNER_CHANGED = "runner-changed"
    HOST_CHANGED = "host-changed"
    FLOW_CHANGED = "flow-changed"
    EVENT_LOGGED = "event-logged"


#: The frame types that have a durable activity row of their own type.
ActivityHubEventType = Literal["chunk-changed", "event-logged", "runner-changed"]

#: The causes backed by a durable chunk fact: all but ``edited``, which records none.
ActivityChunkChangeCause = Literal[
    "minted",
    "promoted",
    "demoted",
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
    "released",
    "paused",
    "resumed",
    "stopped",
    "completed",
    "hub-advanced",
    "deleted",
]

#: What fact family drove a ``chunk-changed`` frame. ``edited`` is the one cause with no durable fact behind it.
ChunkChangeCause = Literal[ActivityChunkChangeCause, "edited"]

#: The runner kinds backed by a durable pause fact.
ActivityRunnerChangeKind = Literal[
    "paused",
    "resumed",
    "locally-paused",
    "locally-resumed",
]

#: What a ``runner-changed`` frame reports.
RunnerChangeKind = Literal[
    ActivityRunnerChangeKind,
    "added",
    "registered",
    "heartbeat",
    "external-usage",
    "retired",
    "reinstated",
    "token-revoked",
]

#: What a ``host-changed`` frame reports.
HostChangeKind = Literal[
    "added",
    "reported",
    "adopted",
    "released",
    "token-enrolled",
    "token-revoked",
    "retired",
]

#: What a ``flow-changed`` frame reports: the kind of the flow fact written, one frame per fact.
FlowChangeKind = Literal["submitted", "turn-started", "turn-ended", "landed", "withdrawn"]
