"""Operational event-log wire bodies — the ``GET /api/events`` read (issue #125).

The hub's durable operational event feed: a typed, severity-ranked record of the
operationally-significant things that happen to runners and workers (non-clean worker
exits, spawn/push/attach failures, stalls), unified at read time with the currently-open
escalations so ``needs_human`` is one event kind in the same feed rather than a separate
surface. The board's **Events tab** renders these newest-and-most-severe first.

A projected escalation row carries a **negative** ``id`` (it is not an ``event_log``
row) — see :func:`blizzard.hub.domain.work.derive_event_feed`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EventView(BaseModel):
    """One operational event on the wire — an ``event_log`` row or a projected open
    escalation. ``chunk_id``/``lease_id``/``node_name`` are absent for a runner-scoped
    event; ``detail`` is the event-specific JSON payload the fixed fields don't carry."""

    id: int
    recorded_at: str  # iso-utc
    severity: str  # info | warning | critical
    kind: str
    runner_id: str
    chunk_id: str | None = None
    lease_id: str | None = None
    node_name: str | None = None
    message: str
    detail: dict[str, Any] | None = None


class EventsResponse(BaseModel):
    """The operational event feed, newest-and-most-severe first (bounded)."""

    events: list[EventView] = []
