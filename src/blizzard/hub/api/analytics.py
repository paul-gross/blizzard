"""Analytics operator-plane routes: a forced re-derive (blizzard#254 D7) over the
standing sweep's own per-segment replacement unit, and the read-only events/counts
surfaces (blizzard#255) over the derived projection. Reads gate on
:data:`~blizzard.auth_core.TRANSCRIPT_READ`; ``/re-derive`` alone on the mutating
:data:`~blizzard.auth_core.ANALYTICS_ADMIN`, per-route rather than router-wide
(blizzard#255 D2). Operator-plane, never ``/api/fleet/...``."""

from __future__ import annotations

import json
from collections.abc import Iterator
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


def _criteria(
    *,
    kind: str | None,
    tool: str | None,
    path_prefix: str | None,
    node_id: str | None,
    graph_id: str | None,
    source: str | None,
    since: datetime | None,
    until: datetime | None,
    extractor_version: str | None,
) -> EventQueryCriteria:
    return EventQueryCriteria(
        extractor_version=extractor_version or EXTRACTOR_VERSION,
        kind=kind,
        tool=tool,
        path_prefix=path_prefix,
        node_id=node_id,
        graph_id=graph_id,
        source=source,
        since=as_utc(since) if since is not None else None,
        until=as_utc(until) if until is not None else None,
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


def _events_response(page: EventPage) -> AnalyticsEventsResponse:
    return AnalyticsEventsResponse(events=[_event_view(e) for e in page.events], next_cursor=page.next_cursor)


def _counts_response(rows: list[CountRow]) -> AnalyticsCountsResponse:
    return AnalyticsCountsResponse(counts=[AnalyticsCountView(key=row.key, count=row.count) for row in rows])


@router.get("/events", response_model=AnalyticsEventsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))])
def list_events(
    services: Annotated[HubServices, Depends(get_services)],
    kind: Annotated[str | None, Query()] = None,
    tool: Annotated[str | None, Query()] = None,
    path_prefix: Annotated[str | None, Query()] = None,
    node_id: Annotated[str | None, Query()] = None,
    graph_id: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    extractor_version: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> AnalyticsEventsResponse:
    """A bounded, keyset-paginated page of events, every filter combinable, defaulting
    to the current extractor version — a caller wanting an older one names it, since
    mixing versions would double-count the same occurrence."""
    criteria = _criteria(
        kind=kind,
        tool=tool,
        path_prefix=path_prefix,
        node_id=node_id,
        graph_id=graph_id,
        source=source,
        since=since,
        until=until,
        extractor_version=extractor_version,
    )
    try:
        page = services.analytics_events.events(criteria, cursor=cursor, limit=limit)
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
    kind: Annotated[str | None, Query()] = None,
    tool: Annotated[str | None, Query()] = None,
    path_prefix: Annotated[str | None, Query()] = None,
    node_id: Annotated[str | None, Query()] = None,
    graph_id: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    extractor_version: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    """Every event matching the same filters as the paginated events endpoint, streamed
    one JSON object per line in the same order — the bulk-export encoding. Internally
    paginated so an arbitrarily large result set never loads into memory at once, unlike
    the single-page JSON response."""
    criteria = _criteria(
        kind=kind,
        tool=tool,
        path_prefix=path_prefix,
        node_id=node_id,
        graph_id=graph_id,
        source=source,
        since=since,
        until=until,
        extractor_version=extractor_version,
    )

    def lines() -> Iterator[bytes]:
        cursor: str | None = None
        while True:
            page = services.analytics_events.events(criteria, cursor=cursor, limit=_STREAM_BATCH_SIZE)
            for record in page.events:
                yield (_event_view(record).model_dump_json() + "\n").encode()
            if page.next_cursor is None:
                return
            cursor = page.next_cursor

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@router.get("/counts/files", response_model=AnalyticsCountsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))])
def counts_by_file(
    services: Annotated[HubServices, Depends(get_services)],
    tool: Annotated[str | None, Query()] = None,
    path_prefix: Annotated[str | None, Query()] = None,
    node_id: Annotated[str | None, Query()] = None,
    graph_id: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    extractor_version: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Occurrence counts by file path among ``file_read`` events, honoring every other
    filter — ``kind`` is not offered here, since this count is always scoped to
    ``file_read``."""
    criteria = _criteria(
        kind=None,
        tool=tool,
        path_prefix=path_prefix,
        node_id=node_id,
        graph_id=graph_id,
        source=source,
        since=since,
        until=until,
        extractor_version=extractor_version,
    )
    return _counts_response(services.analytics_events.counts_by_file(criteria))


@router.get("/counts/skills", response_model=AnalyticsCountsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))])
def counts_by_skill(
    services: Annotated[HubServices, Depends(get_services)],
    node_id: Annotated[str | None, Query()] = None,
    graph_id: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    extractor_version: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Occurrence counts by skill name among ``skill_invocation`` events, honoring every
    other filter — ``kind``/``tool``/``path_prefix`` are not offered: the kind is fixed
    and a skill invocation carries no path."""
    criteria = _criteria(
        kind=None,
        tool=None,
        path_prefix=None,
        node_id=node_id,
        graph_id=graph_id,
        source=source,
        since=since,
        until=until,
        extractor_version=extractor_version,
    )
    return _counts_response(services.analytics_events.counts_by_skill(criteria))


@router.get(
    "/counts/agent-types", response_model=AnalyticsCountsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))]
)
def counts_by_agent_type(
    services: Annotated[HubServices, Depends(get_services)],
    kind: Annotated[str | None, Query()] = None,
    tool: Annotated[str | None, Query()] = None,
    path_prefix: Annotated[str | None, Query()] = None,
    node_id: Annotated[str | None, Query()] = None,
    graph_id: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    extractor_version: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Occurrence counts by the enclosing sidechain's agent type, across every kind
    matching the filters — how much activity happened under which agent type, not
    narrowed to spawn events alone. Filter ``kind=agent_spawn`` on the raw events
    endpoint instead for counts of spawns by the type each one spawned."""
    criteria = _criteria(
        kind=kind,
        tool=tool,
        path_prefix=path_prefix,
        node_id=node_id,
        graph_id=graph_id,
        source=source,
        since=since,
        until=until,
        extractor_version=extractor_version,
    )
    return _counts_response(services.analytics_events.counts_by_agent_type(criteria))


@router.get("/counts/nodes", response_model=AnalyticsCountsResponse, dependencies=[Depends(require(TRANSCRIPT_READ))])
def counts_by_node(
    services: Annotated[HubServices, Depends(get_services)],
    kind: Annotated[str | None, Query()] = None,
    tool: Annotated[str | None, Query()] = None,
    path_prefix: Annotated[str | None, Query()] = None,
    graph_id: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    extractor_version: Annotated[str | None, Query()] = None,
) -> AnalyticsCountsResponse:
    """Occurrence counts by node id, across every kind matching the filters."""
    criteria = _criteria(
        kind=kind,
        tool=tool,
        path_prefix=path_prefix,
        node_id=None,
        graph_id=graph_id,
        source=source,
        since=since,
        until=until,
        extractor_version=extractor_version,
    )
    return _counts_response(services.analytics_events.counts_by_node(criteria))
