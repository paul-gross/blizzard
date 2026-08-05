"""Operational event-log wire bodies — the ``GET /api/events`` read (issue #125).

A typed, severity-ranked record of the operationally-significant things that happen to
runners and workers, with the currently-open escalations projected into the same feed as
one more event kind. A projected escalation row carries a **negative** ``id``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EventView(BaseModel):
    """One operational event on the wire — an ``event_log`` row or a projected open
    escalation. ``chunk_id``/``lease_id``/``node_name`` are absent for a runner-scoped
    event; ``runner_id`` is absent for a projected escalation (issue #155 — ``null``,
    never ``""``); ``detail`` is the event-specific JSON payload."""

    id: int
    recorded_at: str  # iso-utc
    severity: str  # info | warning | critical
    kind: str
    runner_id: str | None = None
    chunk_id: str | None = None
    lease_id: str | None = None
    node_name: str | None = None
    message: str
    detail: dict[str, Any] | None = None


class EventsResponse(BaseModel):
    """The operational event feed, newest-and-most-severe first (bounded)."""

    events: list[EventView] = []
