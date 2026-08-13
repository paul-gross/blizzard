"""Analytics operator-plane routes: a forced re-derive (blizzard#254 D7) over the
standing sweep's own per-segment replacement unit, the read-only events/counts surfaces
(blizzard#255) over the derived projection, and the operational datasets (blizzard#256)
— durations, spend, outcomes — over facts the hub already holds. Reads gate on
:data:`~blizzard.auth_core.TRANSCRIPT_READ`; ``/re-derive`` alone on the mutating
:data:`~blizzard.auth_core.ANALYTICS_ADMIN`, per-route rather than router-wide
(blizzard#255 D2). Operator-plane, never ``/api/fleet/...``."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from blizzard.auth_core import ANALYTICS_ADMIN, TRANSCRIPT_READ
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.analytics.extraction import EXTRACTOR_VERSION
from blizzard.hub.domain.analytics.operational import (
    ChunkSpendRecord,
    DurationStats,
    IReadOperationalAnalytics,
    OperationalCriteria,
    SpendStats,
)
from blizzard.hub.domain.analytics.queries import (
    CountRow,
    EventPage,
    EventQueryCriteria,
    EventRecord,
    IReadAnalyticsEventQueries,
    MalformedCursor,
)
from blizzard.wire.analytics import (
    AnalyticsChunkSpendResponse,
    AnalyticsChunkSpendView,
    AnalyticsCountsResponse,
    AnalyticsCountView,
    AnalyticsDurationsResponse,
    AnalyticsDurationView,
    AnalyticsEventsResponse,
    AnalyticsEventView,
    AnalyticsSpendResponse,
    AnalyticsSpendView,
    ReDeriveRequest,
    ReDeriveResponse,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(reject_runner_principal)])

#: Default internal page size for the NDJSON stream — never the whole result set at once.
_STREAM_BATCH_SIZE = 500


@router.post("/re-derive", response_model=ReDeriveResponse, dependencies=[Depends(require(ANALYTICS_ADMIN))])
def re_derive(request: ReDeriveRequest, services: Annotated[HubServices, Depends(get_services)]) -> ReDeriveResponse:
    """A segment scope forces that one segment regardless of its candidacy; a chunk or
    all scope derives up to ``limit`` of that scope's current candidates and reports how
    many remain, so the caller drives to convergence with repeated calls."""
    if request.segment_id is not None and request.chunk_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="segment_id and chunk_id are mutually exclusive"
        )
    service = services.event_derivation_service
    if request.segment_id is not None:
        service.derive_segment(request.segment_id)
        return ReDeriveResponse(derived=1, remaining=0)

    candidates = service.candidate_segment_ids(chunk_id=request.chunk_id)
    to_derive = candidates[: request.limit]
    for segment_id in to_derive:
        service.derive_segment(segment_id)
    return ReDeriveResponse(derived=len(to_derive), remaining=len(candidates) - len(to_derive))


# --- read-only events/counts (blizzard#255) --------------------------------------

# A count omits the query param for a field its own scope already pins, and the one that
# would select a single group rather than narrow the count; each route states which.


@dataclass(frozen=True)
class ScopeFilters:
    """The filter block every analytics route exposes — which work the read covers and
    over which window (blizzard#256 D7). Shared by the events/counts routes and the
    operational datasets alike; a route wanting the derived-event projection's own
    ``extractor_version`` composes :class:`EventScopeFilters` on top rather than this
    carrying a field only one projection means anything to."""

    graph_id: str | None
    source: str | None
    since: datetime | None
    until: datetime | None

    @classmethod
    def of(
        cls,
        graph_id: Annotated[str | None, Query()] = None,
        source: Annotated[str | None, Query()] = None,
        since: Annotated[datetime | None, Query()] = None,
        until: Annotated[datetime | None, Query()] = None,
    ) -> ScopeFilters:
        return cls(graph_id, source, since, until)

    def criteria(self) -> OperationalCriteria:
        return OperationalCriteria(
            graph_id=self.graph_id,
            source=self.source,
            since=as_utc(self.since) if self.since is not None else None,
            until=as_utc(self.until) if self.until is not None else None,
        )


@dataclass(frozen=True)
class EventScopeFilters:
    """:class:`ScopeFilters` plus ``extractor_version`` — meaningless outside the
    derived-event projection (blizzard#256 D7), so it stays off the shared block and
    composes on top of it here instead. Takes its five query params flat, rather than
    nesting a ``Depends(ScopeFilters.of)``, so FastAPI's per-dependant param ordering
    reproduces the pre-split parameter order byte-for-byte (P1's zero-spec-diff bar)."""

    scope: ScopeFilters
    extractor_version: str | None

    @classmethod
    def of(
        cls,
        graph_id: Annotated[str | None, Query()] = None,
        source: Annotated[str | None, Query()] = None,
        since: Annotated[datetime | None, Query()] = None,
        until: Annotated[datetime | None, Query()] = None,
        extractor_version: Annotated[str | None, Query()] = None,
    ) -> EventScopeFilters:
        return cls(ScopeFilters(graph_id, source, since, until), extractor_version)

    def criteria(
        self,
        *,
        kind: str | None = None,
        tool: str | None = None,
        subject_prefix: str | None = None,
        node_id: str | None = None,
    ) -> EventQueryCriteria:
        """A route passes exactly the narrowing filters it exposes; one it does not
        offer goes unnamed here, rather than named as an explicit ``None``."""
        return EventQueryCriteria(
            extractor_version=self.extractor_version or EXTRACTOR_VERSION,
            kind=kind,
            tool=tool,
            subject_prefix=subject_prefix,
            node_id=node_id,
            graph_id=self.scope.graph_id,
            source=self.scope.source,
            since=as_utc(self.scope.since) if self.scope.since is not None else None,
            until=as_utc(self.scope.until) if self.scope.until is not None else None,
        )


@dataclass(frozen=True)
class EventFilters:
    """Every filter this API owes, for a route that pins none of them: the shared scope
    plus the four that narrow by an event's own shape."""

    scope: EventScopeFilters
    kind: str | None
    tool: str | None
    subject_prefix: str | None
    node_id: str | None

    @classmethod
    def of(
        cls,
        scope: Annotated[EventScopeFilters, Depends(EventScopeFilters.of)],
        kind: Annotated[str | None, Query()] = None,
        tool: Annotated[str | None, Query()] = None,
        subject_prefix: Annotated[str | None, Query()] = None,
        node_id: Annotated[str | None, Query()] = None,
    ) -> EventFilters:
        return cls(scope, kind, tool, subject_prefix, node_id)

    @property
    def criteria(self) -> EventQueryCriteria:
        return self.scope.criteria(
            kind=self.kind, tool=self.tool, subject_prefix=self.subject_prefix, node_id=self.node_id
        )


def _event_view(record: EventRecord) -> AnalyticsEventView:
    return AnalyticsEventView(
        id=record.id,
        kind=record.kind,
        subject=record.subject,
        tool=record.tool,
        payload=json.loads(record.payload),
        chunk_id=record.chunk_id,
        node_id=record.node_id,
        epoch=record.epoch,
        spawn_generation=record.spawn_generation,
        graph_id=record.graph_id,
        depth=record.depth,
        agent_type=record.agent_type,
        occurred_at=iso_utc(record.occurred_at) if record.occurred_at is not None else None,
    )


def ndjson_lines(
    queries: IReadAnalyticsEventQueries, criteria: EventQueryCriteria, *, batch_size: int = _STREAM_BATCH_SIZE
) -> Iterator[bytes]:
    """One line per matching event, read a batch at a time and carrying each batch's
    cursor into the next. ``batch_size`` is that internal page size, never a client's."""
    # A read failing mid-stream truncates the body: there is no terminator line to omit,
    # because a truncated HTTP body is already distinguishable from a complete one.
    cursor: str | None = None
    while True:
        page = queries.events(criteria, cursor=cursor, limit=batch_size)
        for record in page.events:
            yield (_event_view(record).model_dump_json() + "\n").encode()
        if page.next_cursor is None:
            return
        cursor = page.next_cursor


def _events_response(page: EventPage) -> AnalyticsEventsResponse:
    return AnalyticsEventsResponse(events=[_event_view(e) for e in page.events], next_cursor=page.next_cursor)


def _counts_response(rows: list[CountRow]) -> AnalyticsCountsResponse:
    return AnalyticsCountsResponse(counts=[AnalyticsCountView(key=row.key, count=row.count) for row in rows])


@router.get("/events", response_model=AnalyticsEventsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))])
def list_events(
    services: Annotated[HubServices, Depends(get_services)],
    filters: Annotated[EventFilters, Depends(EventFilters.of)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> AnalyticsEventsResponse:
    """A bounded, keyset-paginated page of events, every filter combinable, defaulting
    to the current extractor version — a caller wanting an older one names it, since
    mixing versions would double-count the same occurrence."""
    try:
        page = services.analytics_events.events(filters.criteria, cursor=cursor, limit=limit)
    except MalformedCursor as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="malformed cursor") from exc
    return _events_response(page)


@router.get(
    "/events/ndjson",
    dependencies=[Depends(require(TRANSCRIPT_READ))],
    # Declared, not defaulted: FastAPI's default 200 for a `StreamingResponse` would
    # claim `application/json` in the spec the TS client is generated from.
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {"schema": {"type": "string"}}}}},
)
def stream_events(
    services: Annotated[HubServices, Depends(get_services)],
    filters: Annotated[EventFilters, Depends(EventFilters.of)],
) -> StreamingResponse:
    """Every event matching the same filters as the paginated events endpoint, streamed
    one JSON object per line in the same order — the bulk-export encoding. Internally
    paginated so an arbitrarily large result set never loads into memory at once, unlike
    the single-page JSON response."""
    return StreamingResponse(
        ndjson_lines(services.analytics_events, filters.criteria), media_type="application/x-ndjson"
    )


@router.get("/counts/files", response_model=AnalyticsCountsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))])
def counts_by_file(
    services: Annotated[HubServices, Depends(get_services)],
    scope: Annotated[EventScopeFilters, Depends(EventScopeFilters.of)],
    tool: Annotated[str | None, Query()] = None,
    subject_prefix: Annotated[str | None, Query()] = None,
    node_id: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Occurrence counts by file path among ``file_read`` events, honoring every other
    filter. ``kind`` is not offered: this count fixes it to ``file_read``."""
    criteria = scope.criteria(tool=tool, subject_prefix=subject_prefix, node_id=node_id)
    return _counts_response(services.analytics_events.counts_by_file(criteria))


@router.get("/counts/skills", response_model=AnalyticsCountsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))])
def counts_by_skill(
    services: Annotated[HubServices, Depends(get_services)],
    scope: Annotated[EventScopeFilters, Depends(EventScopeFilters.of)],
    node_id: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Occurrence counts by skill name among ``skill_invocation`` events, honoring every
    other filter. ``kind`` is not offered (this count fixes it), nor ``tool`` (that kind
    always records ``Skill``), nor ``subject_prefix`` — a skill name is a flat name, so a
    prefix of one narrows nothing a caller could not name outright."""
    return _counts_response(services.analytics_events.counts_by_skill(scope.criteria(node_id=node_id)))


@router.get(
    "/counts/agent-types", response_model=AnalyticsCountsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))]
)
def counts_by_agent_type(
    services: Annotated[HubServices, Depends(get_services)],
    filters: Annotated[EventFilters, Depends(EventFilters.of)],
) -> AnalyticsCountsResponse:
    """Occurrence counts by the enclosing sidechain's agent type, across every kind
    matching the filters — how much activity happened under which agent type, not
    narrowed to spawn events alone. Filter ``kind=agent_spawn`` on the raw events
    endpoint instead for counts of spawns by the type each one spawned."""
    return _counts_response(services.analytics_events.counts_by_agent_type(filters.criteria))


@router.get("/counts/nodes", response_model=AnalyticsCountsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))])
def counts_by_node(
    services: Annotated[HubServices, Depends(get_services)],
    scope: Annotated[EventScopeFilters, Depends(EventScopeFilters.of)],
    kind: Annotated[str | None, Query()] = None,
    tool: Annotated[str | None, Query()] = None,
    subject_prefix: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Occurrence counts by node id, across every kind matching the filters.
    ``node_id`` is not offered: it would select a single group, not narrow the count."""
    criteria = scope.criteria(kind=kind, tool=tool, subject_prefix=subject_prefix)
    return _counts_response(services.analytics_events.counts_by_node(criteria))


# --- operational datasets: durations, spend, outcomes (blizzard#256) ---------------
# Grouped by node or by graph, bounded by the graph's size (D8): the same single-envelope
# JSON shape the counts routes use, no cursor, no NDJSON variant. Per-chunk spend is the
# one exception — unbounded in a wide window, so it takes the cursor-paged JSON plus
# NDJSON shape `/events` uses instead.


def _durations_response(stats: list[DurationStats]) -> AnalyticsDurationsResponse:
    return AnalyticsDurationsResponse(
        durations=[
            AnalyticsDurationView(
                key=s.key, completed_steps=s.completed_steps, total_seconds=s.total_seconds, avg_seconds=s.avg_seconds
            )
            for s in stats
        ]
    )


@router.get(
    "/durations/nodes", response_model=AnalyticsDurationsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))]
)
def durations_by_node(
    services: Annotated[HubServices, Depends(get_services)], scope: Annotated[ScopeFilters, Depends(ScopeFilters.of)]
) -> AnalyticsDurationsResponse:
    """Completed-step duration rollups grouped by node (D2) — hub-observed wall-clock
    latency (D3), never a runner-measured one."""
    return _durations_response(services.operational_analytics.durations_by_node(scope.criteria()))


@router.get(
    "/durations/graphs", response_model=AnalyticsDurationsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))]
)
def durations_by_graph(
    services: Annotated[HubServices, Depends(get_services)], scope: Annotated[ScopeFilters, Depends(ScopeFilters.of)]
) -> AnalyticsDurationsResponse:
    """The same rollup grouped by the graph the step happened in (D2) — the transition's
    own ``graph_id``, never the chunk's current pin."""
    return _durations_response(services.operational_analytics.durations_by_graph(scope.criteria()))


def _spend_response(stats: list[SpendStats]) -> AnalyticsSpendResponse:
    return AnalyticsSpendResponse(
        spend=[
            AnalyticsSpendView(
                key=s.key,
                input_tokens=s.input_tokens,
                output_tokens=s.output_tokens,
                cache_read_tokens=s.cache_read_tokens,
                cache_create_tokens=s.cache_create_tokens,
                cost_usd=s.cost_usd,
                cost_partial=s.cost_partial,
            )
            for s in stats
        ]
    )


@router.get("/spend/nodes", response_model=AnalyticsSpendResponse, dependencies=[Depends(require(TRANSCRIPT_READ))])
def spend_by_node(
    services: Annotated[HubServices, Depends(get_services)], scope: Annotated[ScopeFilters, Depends(ScopeFilters.of)]
) -> AnalyticsSpendResponse:
    """Usage/cost rollups grouped by node (D6) — the same lower-bound + PARTIAL contract
    ``GET /api/spend`` publishes."""
    return _spend_response(services.operational_analytics.spend_by_node(scope.criteria()))


@router.get("/spend/graphs", response_model=AnalyticsSpendResponse, dependencies=[Depends(require(TRANSCRIPT_READ))])
def spend_by_graph(
    services: Annotated[HubServices, Depends(get_services)], scope: Annotated[ScopeFilters, Depends(ScopeFilters.of)]
) -> AnalyticsSpendResponse:
    """The same rollup grouped by each usage fact's chunk's *current* graph pin — a
    chunk that migrated attributes every usage fact it ever recorded to where it lives
    today (D6)."""
    return _spend_response(services.operational_analytics.spend_by_graph(scope.criteria()))


def _chunk_spend_view(record: ChunkSpendRecord) -> AnalyticsChunkSpendView:
    return AnalyticsChunkSpendView(
        chunk_id=record.chunk_id,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cache_read_tokens=record.cache_read_tokens,
        cache_create_tokens=record.cache_create_tokens,
        cost_usd=record.cost_usd,
        cost_partial=record.cost_partial,
    )


def chunk_spend_ndjson_lines(
    operational: IReadOperationalAnalytics,
    criteria: OperationalCriteria,
    *,
    batch_size: int = _STREAM_BATCH_SIZE,
) -> Iterator[bytes]:
    """One line per chunk's spend rollup, read a batch at a time and carrying each
    batch's cursor into the next — the per-chunk spend dataset's bulk-export encoding,
    mirroring :func:`ndjson_lines`."""
    cursor: str | None = None
    while True:
        page = operational.spend_by_chunk(criteria, cursor=cursor, limit=batch_size)
        for record in page.records:
            yield (_chunk_spend_view(record).model_dump_json() + "\n").encode()
        if page.next_cursor is None:
            return
        cursor = page.next_cursor


@router.get(
    "/spend/chunks", response_model=AnalyticsChunkSpendResponse, dependencies=[Depends(require(TRANSCRIPT_READ))]
)
def spend_by_chunk(
    services: Annotated[HubServices, Depends(get_services)],
    scope: Annotated[ScopeFilters, Depends(ScopeFilters.of)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> AnalyticsChunkSpendResponse:
    """A bounded, keyset-paginated page of per-chunk spend rollups (D8) — unbounded in a
    wide window, unlike the per-node/per-graph groupings, so this takes a cursor rather
    than a single envelope."""
    try:
        page = services.operational_analytics.spend_by_chunk(scope.criteria(), cursor=cursor, limit=limit)
    except MalformedCursor as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="malformed cursor") from exc
    return AnalyticsChunkSpendResponse(spend=[_chunk_spend_view(r) for r in page.records], next_cursor=page.next_cursor)


@router.get(
    "/spend/chunks/ndjson",
    dependencies=[Depends(require(TRANSCRIPT_READ))],
    # Declared, not defaulted: FastAPI's default 200 for a `StreamingResponse` would
    # claim `application/json` in the spec the TS client is generated from.
    response_class=StreamingResponse,
    responses={200: {"content": {"application/x-ndjson": {"schema": {"type": "string"}}}}},
)
def stream_chunk_spend(
    services: Annotated[HubServices, Depends(get_services)], scope: Annotated[ScopeFilters, Depends(ScopeFilters.of)]
) -> StreamingResponse:
    """Every chunk's spend rollup matching the same filters as the paginated route,
    streamed one JSON object per line in the same order."""
    return StreamingResponse(
        chunk_spend_ndjson_lines(services.operational_analytics, scope.criteria()), media_type="application/x-ndjson"
    )
