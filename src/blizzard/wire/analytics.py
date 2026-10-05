"""Analytics wire bodies — the forced re-derive verb's request and response over
``POST /api/analytics/re-derive``, the read-only events and counts
surfaces over ``GET /api/analytics/events`` and ``GET /api/analytics/counts/*``,
and the operational datasets over ``GET /api/analytics/durations/*``,
``.../spend/*``, and ``.../outcomes/*``."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AnalyticsEventView(BaseModel):
    """One derived event, wire-shaped — ``payload`` is parsed from its
    stored JSON-text form into a plain object, so a consumer never double-decodes a JSON
    string within JSON."""

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
    harness_id: str | None
    harness_version: str | None
    model: str | None
    effort: str | None


class AnalyticsEventsResponse(BaseModel):
    """A bounded page — ``next_cursor`` is ``None`` exactly when this
    page is the last one; a caller drives a full bulk read by following it until absent."""

    events: list[AnalyticsEventView]
    next_cursor: str | None


class AnalyticsCountView(BaseModel):
    """One grouping key and how many events fell under it. ``key`` names
    whichever dimension this response is grouped by — a file path, a skill name, an
    agent type, or a node id. ``graph_name`` and ``node_name`` name what a node-keyed row
    counts: null on the files, skills, and agent-types dimensions, which have no graph or
    node, and null where the node id no longer resolves. Under ``by_name`` the row is the
    roll-up of every minted id sharing the name pair, and ``key`` is ``<graph_name>/<node_name>``."""

    key: str
    count: int
    graph_name: str | None = None
    node_name: str | None = None


class AnalyticsCountsResponse(BaseModel):
    """Every grouping key matching the filters, most-frequent first with the key
    ascending as the tiebreak — a total order two identical calls agree on."""

    counts: list[AnalyticsCountView]


class AnalyticsDurationView(BaseModel):
    """One grouping key's step-duration rollup — ``key`` is a node
    id or a graph id. Hub-observed wall-clock, not runner-measured: a parked gate
    stretches it, a delayed store-and-forward mint-report *flush* compresses it toward
    zero instead."""

    key: str
    completed_steps: int
    total_seconds: float
    avg_seconds: float


class AnalyticsDurationsResponse(BaseModel):
    """Every grouping key matching the filters, key ascending — a total order two
    identical calls agree on, the same convention the counts responses use."""

    durations: list[AnalyticsDurationView]


class AnalyticsSpendView(BaseModel):
    """One grouping key's usage/cost rollup — ``key`` is a node id or a graph id, whichever dataset
    served it. The same contract ``GET /api/spend`` publishes: ``cost_partial`` is ``True`` iff some summed
    row carried neither a billed nor an estimated amount, and ``estimated_cost_usd`` is ``None`` unless some
    summed row carried one. ``graph_name`` and ``node_name`` name what the row sums, null where an
    id no longer resolves and ``node_name`` always null on a graph row: a node row names the node's
    own graph, a graph row names the chunk's *current* pin, so a migrated chunk's two rows can
    disagree. Under ``by_name`` the row rolls up every minted id sharing the name, and ``key`` is
    ``<graph_name>/<node_name>`` for a node and ``graph_name`` for a graph."""

    key: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool
    estimated_cost_usd: float | None = None
    graph_name: str | None = None
    node_name: str | None = None


class AnalyticsSpendResponse(BaseModel):
    """Every grouping key matching the filters, key ascending — a total order two
    identical calls agree on, the same convention the durations/counts responses use."""

    spend: list[AnalyticsSpendView]


class AnalyticsChunkSpendView(BaseModel):
    """One chunk's own usage/cost rollup — the per-chunk grouping's
    unbounded, cursor-paged row. ``cost_partial`` is ``True`` iff some summed row carried neither a billed
    nor an estimated amount, and ``estimated_cost_usd`` is ``None`` unless some summed row carried one."""

    chunk_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool
    estimated_cost_usd: float | None = None


class AnalyticsChunkSpendResponse(BaseModel):
    """A bounded page — ``next_cursor`` is ``None`` exactly when this
    is the last page. Not a point-in-time snapshot: each page's sums are recomputed at
    fetch time, so an earlier page's chunk can be invalidated by a usage fact recorded
    while a later page still streams."""

    spend: list[AnalyticsChunkSpendView]
    next_cursor: str | None


class AnalyticsOutcomeView(BaseModel):
    """One node's judged-choice distribution and attempt-failure count,
    never blended — a judged failure consumes no retry budget, an ended (superseded by a
    strictly newer lease) attempt does; a still-open final attempt counts as neither, nor
    does a kick-back. The two counts' differing time windows are in ``docs/deployment/analytics.md``."""

    node_id: str
    choice_counts: dict[str, int]
    attempt_failures: int


class AnalyticsOutcomesResponse(BaseModel):
    """Every node matching the filters, node id ascending — a total order two identical
    calls agree on, the same convention the durations/spend responses use."""

    outcomes: list[AnalyticsOutcomeView]


class ReDeriveRequest(BaseModel):
    """Scope the call to one visible segment (a genuine force, bypassing the candidate check),
    one chunk's candidates, or every candidate (both unset) — never both a segment and a
    chunk. ``limit`` bounds a chunk/all-scoped call; a single segment always derives
    exactly one, so it ignores ``limit``."""

    segment_id: str | None = None
    chunk_id: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


class ReDeriveResponse(BaseModel):
    """How many segments this call derived, and how many still-candidate segments
    remain in scope — the caller drives to convergence by calling again while
    ``remaining`` is nonzero. ``not_visible``: the named segment is not visible; nothing derived."""

    derived: int
    remaining: int
    not_visible: bool = False
