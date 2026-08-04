"""The activity feed — ``GET /api/activity`` (issue #213).

A bounded, merged read over the same fact-derived vocabulary a live ``chunk-changed`` /
``event-logged`` / ``runner-changed`` frame carries — shaped by
:func:`blizzard.hub.domain.work.derive_activity_feed`.
"""

from __future__ import annotations

from pydantic import BaseModel


class ActivityView(BaseModel):
    """One activity-feed row on the wire — present-when-meaningful: a field its source
    doesn't fill is absent, never a placeholder ``None``-as-a-present-field.

    ``type`` is one of ``"chunk-changed"`` / ``"event-logged"`` / ``"runner-changed"``;
    ``key`` is the identity of the underlying fact, the merge's own recency tiebreak,
    never a stable frame id. ``status``/``prev_status``/``node``/``prev_node`` stay
    absent for every row this phase produces — present in the shape for a later one."""

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
