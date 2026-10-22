"""Operational event-log wire bodies — the ``GET /api/events`` read.

A typed record of the operationally-significant things that happen to
runners and workers, with the currently-open escalations projected into the same feed as
one more event kind. A projected escalation row carries a **negative** ``id``.
"""

from __future__ import annotations

from typing import Annotated, Any

from blizzard.foundation.event_log import EventLogSeverity
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


class EventView(WireModel):
    """One operational event on the wire — an ``event_log`` row or a projected open
    escalation. ``chunk_id``/``lease_id``/``node_name`` are absent for a runner-scoped
    event; ``runner_id`` and ``runner_name`` are absent for a projected escalation (``null``,
    never ``""``); ``detail`` is the event-specific JSON payload."""

    id: int
    recorded_at: str  # iso-utc
    severity: Annotated[EventLogSeverity | Unrecognized, OPEN]
    kind: str
    runner_id: str | None = None
    runner_name: str | None = None
    chunk_id: str | None = None
    lease_id: str | None = None
    node_name: str | None = None
    message: str
    detail: dict[str, Any] | None = None


class EventsResponse(WireModel):
    """The operational event feed, newest first (bounded)."""

    events: list[EventView] = []
