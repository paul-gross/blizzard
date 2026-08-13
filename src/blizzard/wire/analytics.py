"""Analytics wire bodies — the forced re-derive verb's request and response over
``POST /api/analytics/re-derive`` (blizzard#254 D7), the read-only events and counts
surfaces over ``GET /api/analytics/events`` and ``GET /api/analytics/counts/*``
(blizzard#255), and the operational datasets over ``GET /api/analytics/durations/*``,
``.../spend/*``, and ``.../outcomes/*`` (blizzard#256)."""

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


class AnalyticsDurationView(BaseModel):
    """One grouping key's step-duration rollup (blizzard#256 D2/D3) — ``key`` is a node
    id or a graph id, whichever dataset served it. The seconds fields are hub-observed
    wall-clock latency, not a runner-measured instant: a store-and-forward flush or a
    parked gate/ask both stretch it past the step's actual work time (D3)."""

    key: str
    completed_steps: int
    total_seconds: float
    avg_seconds: float


class AnalyticsDurationsResponse(BaseModel):
    """Every grouping key matching the filters, key ascending — a total order two
    identical calls agree on, the same convention the counts responses use."""

    durations: list[AnalyticsDurationView]


class AnalyticsSpendView(BaseModel):
    """One grouping key's usage/cost rollup (blizzard#256 D6) — ``key`` is a node id or
    a graph id, whichever dataset served it. The same lower-bound + PARTIAL contract
    ``GET /api/spend`` publishes: ``cost_usd`` sums only the rows that carried a cost
    envelope, and ``cost_partial`` is ``True`` iff any summed row lacked one."""

    key: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool


class AnalyticsSpendResponse(BaseModel):
    """Every grouping key matching the filters, key ascending — a total order two
    identical calls agree on, the same convention the durations/counts responses use."""

    spend: list[AnalyticsSpendView]


class AnalyticsChunkSpendView(BaseModel):
    """One chunk's own usage/cost rollup (blizzard#256 D8) — the per-chunk grouping's
    unbounded, cursor-paged row."""

    chunk_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool


class AnalyticsChunkSpendResponse(BaseModel):
    """A bounded page (blizzard#256 D8) — ``next_cursor`` is ``None`` exactly when this
    page is the last one; a caller drives a full bulk read by following it until absent."""

    spend: list[AnalyticsChunkSpendView]
    next_cursor: str | None


class AnalyticsOutcomeView(BaseModel):
    """One node's judged-choice distribution and attempt-failure count (blizzard#256 D4),
    never blended — a judged failure consumes no retry budget, an ended attempt does; a
    kick-back counts as neither. The two counts' differing time windows are documented in
    ``docs/deployment.md`` (review round 1 F10)."""

    node_id: str
    choice_counts: dict[str, int]
    attempt_failures: int


class AnalyticsOutcomesResponse(BaseModel):
    """Every node matching the filters, node id ascending — a total order two identical
    calls agree on, the same convention the durations/spend responses use."""

    outcomes: list[AnalyticsOutcomeView]


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
