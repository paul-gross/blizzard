"""Analytics operator-plane routes: a forced re-derive (blizzard#254 D7) over the
standing sweep's own per-segment replacement unit, and the read-only events/counts
surfaces (blizzard#255) over the derived projection. Reads gate on
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
from blizzard.hub.domain.analytics.queries import (
    CountRow,
    EventPage,
    EventQueryCriteria,
    EventRecord,
    IReadAnalyticsEventQueries,
    MalformedCursor,
)
from blizzard.wire.analytics import (
    AnalyticsCountsResponse,
    AnalyticsCountView,
    AnalyticsEventsResponse,
    AnalyticsEventView,
    ReDeriveRequest,
    ReDeriveResponse,
)

router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(reject_runner_principal)])

#: Batch size the NDJSON stream pages internally — independent of any client-visible
#: page size, so a bulk export never holds the whole result set in memory at once.
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
    """The filter block every events and counts route exposes — which work the read
    covers, over which window, at which extractor version."""

    graph_id: str | None
    source: str | None
    since: datetime | None
    until: datetime | None
    extractor_version: str | None

    @classmethod
    def of(
        cls,
        graph_id: Annotated[str | None, Query()] = None,
        source: Annotated[str | None, Query()] = None,
        since: Annotated[datetime | None, Query()] = None,
        until: Annotated[datetime | None, Query()] = None,
        extractor_version: Annotated[str | None, Query()] = None,
    ) -> ScopeFilters:
        return cls(graph_id, source, since, until, extractor_version)

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
            graph_id=self.graph_id,
            source=self.source,
            since=as_utc(self.since) if self.since is not None else None,
            until=as_utc(self.until) if self.until is not None else None,
        )


@dataclass(frozen=True)
class EventFilters:
    """Every filter this API owes, for a route that pins none of them: the shared scope
    plus the four that narrow by an event's own shape."""

    scope: ScopeFilters
    kind: str | None
    tool: str | None
    subject_prefix: str | None
    node_id: str | None

    @classmethod
    def of(
        cls,
        scope: Annotated[ScopeFilters, Depends(ScopeFilters.of)],
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
    scope: Annotated[ScopeFilters, Depends(ScopeFilters.of)],
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
    scope: Annotated[ScopeFilters, Depends(ScopeFilters.of)],
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
    scope: Annotated[ScopeFilters, Depends(ScopeFilters.of)],
    kind: Annotated[str | None, Query()] = None,
    tool: Annotated[str | None, Query()] = None,
    subject_prefix: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Occurrence counts by node id, across every kind matching the filters.
    ``node_id`` is not offered: it would select a single group, not narrow the count."""
    criteria = scope.criteria(kind=kind, tool=tool, subject_prefix=subject_prefix)
    return _counts_response(services.analytics_events.counts_by_node(criteria))
