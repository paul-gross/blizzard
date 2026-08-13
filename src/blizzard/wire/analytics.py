"""Analytics wire bodies — the forced re-derive verb's request and response over
``POST /api/analytics/re-derive`` (blizzard#254 D7), plus the read-only events and
counts surfaces over ``GET /api/analytics/events`` and ``GET /api/analytics/counts/*``
(blizzard#255)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AnalyticsEventView(BaseModel):
    """One derived event, wire-shaped (blizzard#255) — ``payload`` is parsed from its
    stored JSON-text form (``bzh:sql-portable`` binds the store, not the wire) into a
    plain object, so a consumer never double-decodes a JSON string within JSON."""

    id: int
    kind: str
    subject: str | None
    tool: str | None
    payload: dict[str, object]
    chunk_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    graph_id: str
    depth: int
    agent_type: str | None
    occurred_at: str | None


class AnalyticsEventsResponse(BaseModel):
    """A bounded page (blizzard#255) — ``next_cursor`` is ``None`` exactly when this
    page is the last one; a caller drives a full bulk read by following it until absent."""

    events: list[AnalyticsEventView]
    next_cursor: str | None


class AnalyticsCountView(BaseModel):
    """One grouping key and how many events fell under it (blizzard#255). ``key`` is
    whichever column the counts endpoint serving it groups by — a file path, a skill
    name, an agent type, or a node id."""

    key: str
    count: int


class AnalyticsCountsResponse(BaseModel):
    """Every grouping key matching the filters, most-frequent first with the key
    ascending as the tiebreak — a total order two identical calls agree on."""

    counts: list[AnalyticsCountView]


class ReDeriveRequest(BaseModel):
    """Scope the call to one segment (a genuine force, bypassing the candidate check),
    one chunk's candidates, or every candidate (both unset) — never both a segment and a
    chunk. ``limit`` bounds a chunk/all-scoped call; a single segment always derives
    exactly one, so it ignores ``limit``."""

    segment_id: str | None = None
    chunk_id: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


class ReDeriveResponse(BaseModel):
    """How many segments this call derived, and how many still-candidate segments
    remain in scope — the caller drives to convergence by calling again while
    ``remaining`` is nonzero."""

    derived: int
    remaining: int
