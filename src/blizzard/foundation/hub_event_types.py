"""The hub's SSE frame vocabulary, its frame kinds and the causes a frame names —
one definition, shared by both daemons."""

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
    EVENT_LOGGED = "event-logged"


#: The frame types with a row of their own type in ``GET /api/activity`` — the only frames the feed lists, live or not.
ActivityHubEventType = Literal["chunk-changed", "event-logged", "runner-changed"]

#: The causes the activity read backfills from a durable chunk fact: all but ``edited``, which records none.
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

#: What fact family drove a ``chunk-changed`` frame — each emit site names
#: its own cause statically. ``edited`` is the one cause with no durable fact behind it.
ChunkChangeCause = Literal[ActivityChunkChangeCause, "edited"]

#: The runner kinds the activity read backfills from a durable pause fact — the only
#: ``runner-changed`` frames the feed lists, live or reconstructed.
ActivityRunnerChangeKind = Literal[
    "paused",
    "resumed",
    "locally-paused",
    "locally-resumed",
]

#: What a ``runner-changed`` frame reports — see
#: the hub event broker's ``publish_runner_changed``.
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

#: What a ``host-changed`` frame reports — see the hub event broker's ``publish_host_changed``.
HostChangeKind = Literal[
    "added",
    "reported",
    "adopted",
    "released",
    "token-enrolled",
    "token-revoked",
    "retired",
]
