"""The board's Event log activity feed — ``GET /api/activity`` (issue #213).

The page-load backfill counterpart to the live SSE stream (``events/stream.py``): a
bounded, merged read over the same fact-derived vocabulary a live ``chunk-changed`` /
``event-logged`` / ``runner-changed`` frame carries, reshaped by
:func:`blizzard.hub.domain.work.derive_activity_feed` from :class:`~blizzard.hub.domain.work.ActivityRow`.
"""

from __future__ import annotations

from pydantic import BaseModel


class ActivityView(BaseModel):
    """One activity-feed row on the wire — the same present-when-meaningful shape
    :class:`~blizzard.hub.domain.work.ActivityRow` carries, absent (never placeholder
    ``None``-as-a-present-field) wherever a source doesn't fill a field.

    ``type`` is one of ``"chunk-changed"`` / ``"event-logged"`` / ``"runner-changed"``;
    ``key`` is the identity of the underlying fact, the merge's own recency tiebreak,
    never a stable frame id. ``status``/``prev_status``/``node``/``prev_node`` stay
    absent for every row this phase produces (no graph resolution is threaded through
    yet) — present in the shape for forward compatibility with a later phase."""

    type: str
    key: str
    at: str  # iso-utc
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
    severity: str | None = None
    kind: str | None = None
    # runner-changed
    by: str | None = None
    reason: str | None = None


class ActivityResponse(BaseModel):
    """The activity feed, newest-first (bounded)."""

    activity: list[ActivityView] = []
