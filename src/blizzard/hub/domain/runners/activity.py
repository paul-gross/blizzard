"""The activity feed's common row — a historical fact in the vocabulary a live SSE frame carries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.event_log import EventLogSeverity
from blizzard.foundation.roles import dto


@dto
@dataclass(frozen=True)
class ActivityEntry:
    """One row of the activity feed — a historical fact reshaped into the
    same vocabulary a live SSE frame carries. ``type`` mirrors a frame-type constant as a
    plain string (``bzh:domain-core``); ``key`` is a table-qualified natural key used only
    as the sort tiebreak; ``at`` is the fact's own recorded instant."""

    type: str
    key: str
    at: datetime
    # chunk-changed
    chunk_id: str | None = None
    status: str | None = None
    prev_status: str | None = None
    node: str | None = None
    prev_node: str | None = None
    runner_id: str | None = None
    cause: str | None = None
    graph_id: str | None = None
    # event-logged
    severity: EventLogSeverity | None = None
    kind: str | None = None
    # runner-changed
    by: str | None = None
    reason: str | None = None
