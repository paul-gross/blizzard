"""The hub's SSE frame vocabulary — the frame kinds and the causes a frame names, one definition
shared by the broker that publishes a frame and the wire that describes it."""

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
    EVENT_LOGGED = "event-logged"


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

#: What fact family drove a ``chunk-changed`` frame — each emit site names
#: its own cause statically. ``edited`` is the one cause with no durable fact behind it.
ChunkChangeCause = Literal[ActivityChunkChangeCause, Literal["edited"]]

#: What a ``runner-changed`` frame reports — see
#: the hub event broker's ``publish_runner_changed``.
RunnerChangeKind = Literal[
    "added",
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
