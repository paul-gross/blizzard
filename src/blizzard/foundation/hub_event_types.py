"""The hub's SSE frame-kind vocabulary — one definition, shared by the broker that publishes a
frame and the wire that describes it."""

from __future__ import annotations

from enum import StrEnum


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
