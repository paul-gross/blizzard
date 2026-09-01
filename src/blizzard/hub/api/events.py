"""The hub live-event stream — ``GET /api/events/stream`` (SSE), excluded from the OpenAPI schema.

A subscriber registers with :class:`~blizzard.hub.events.broker.EventBroker`, replays the
buffered tail newer than its ``Last-Event-ID``, then streams live until it disconnects — ids
are monotonic, so an event caught in both is emitted once. :class:`Cursor`/:class:`Stream` are
the runner-shared machinery (D1); only the reserved open-of-stream comment names this daemon."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from blizzard.auth_core import FLEET_VIEW
from blizzard.foundation.events.broker import EventBroker
from blizzard.foundation.events.stream import Cursor, Stream
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import ActivityFeed, ActivityRow, EventFeed, EventRow
from blizzard.wire.activity import ActivityResponse, ActivityView
from blizzard.wire.events import EventsResponse, EventView

router = APIRouter(prefix="/api", tags=["meta"])

_RESERVED_COMMENT = ": blizzard hub event stream\n\n"


@router.get("/events/stream", include_in_schema=False)
async def events_stream(
    request: Request, identity: Annotated[ResolvedIdentity, Depends(require(FLEET_VIEW))]
) -> StreamingResponse:
    """Subscribe to the live event stream, resuming from ``Last-Event-ID`` if present.

    ``identity`` is unused beyond the gate itself — ``require(FLEET_VIEW)`` already
    raised 401/403 before this body runs."""
    del identity
    broker: EventBroker | None = getattr(request.app.state, "events", None)
    shutdown: asyncio.Event | None = getattr(request.app.state, "shutdown", None)
    stream = Stream(broker, request, Cursor.of(request), _RESERVED_COMMENT, shutdown)
    return StreamingResponse(stream.frames(), media_type="text/event-stream")


@dataclass(frozen=True)
class Events:
    rows: Sequence[EventRow]

    def response(self) -> EventsResponse:
        return EventsResponse(events=[self._view(row) for row in self.rows])

    def _view(self, row: EventRow) -> EventView:
        return EventView(
            id=row.id,
            recorded_at=iso_utc(row.recorded_at),
            severity=row.severity,
            kind=row.kind,
            runner_id=row.runner_id,
            chunk_id=row.chunk_id,
            lease_id=row.lease_id,
            node_name=row.node_name,
            message=row.message,
            detail=row.detail,
        )


@router.get(
    "/events",
    response_model=EventsResponse,
    dependencies=[Depends(reject_runner_principal), Depends(require(FLEET_VIEW))],
)
def list_events(
    services: Annotated[HubServices, Depends(get_services)],
    severity: Annotated[str | None, Query()] = None,
    runner_id: Annotated[str | None, Query()] = None,
    chunk_id: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> EventsResponse:
    """The ``event_log`` unified with open escalations (issue #125), most-severe-newest first, bounded.

    The ``severity`` / ``runner_id`` / ``chunk_id`` / ``since`` filters apply to the ``event_log`` half;
    the open-escalation projection is always unioned in. A tz-naive ``since`` is coerced to UTC so the
    projection's aware ``recorded_at`` comparison below never raises against it."""
    since_utc = as_utc(since) if since is not None else None
    events = services.chunks.events.list_events(
        severity=severity, runner_id=runner_id, chunk_id=chunk_id, since=since_utc, limit=limit
    )
    # The same predicates over the escalation projection: it is always `critical` and names no runner,
    # so a `severity`/`runner_id` filter excludes it wholesale; `chunk_id`/`since` narrow per row.
    escalations = services.chunks.escalations.list_open_escalations()
    if severity is not None and severity != "critical":
        escalations = []
    if runner_id is not None:
        escalations = []
    if chunk_id is not None:
        escalations = [e for e in escalations if e.chunk_id == chunk_id]
    if since_utc is not None:
        escalations = [e for e in escalations if e.recorded_at >= since_utc]
    return Events(EventFeed.of(events, escalations).rows[:limit]).response()


@dataclass(frozen=True)
class Activity:
    rows: Sequence[ActivityRow]

    def response(self) -> ActivityResponse:
        return ActivityResponse(activity=[self._view(row) for row in self.rows])

    def _view(self, row: ActivityRow) -> ActivityView:
        return ActivityView(
            type=row.type,
            key=row.key,
            at=iso_utc(row.at),
            chunk_id=row.chunk_id,
            status=row.status,
            prev_status=row.prev_status,
            node=row.node,
            prev_node=row.prev_node,
            runner_id=row.runner_id,
            cause=row.cause,
            graph_id=row.graph_id,
            severity=row.severity,
            kind=row.kind,
            by=row.by,
            reason=row.reason,
        )


@router.get(
    "/activity",
    response_model=ActivityResponse,
    dependencies=[Depends(reject_runner_principal), Depends(require(FLEET_VIEW))],
)
def list_activity(
    services: Annotated[HubServices, Depends(get_services)],
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> ActivityResponse:
    """The activity backfill (issue #213) — the three already-bounded per-source activity reads merged,
    sorted newest-first, and capped.

    ``since`` defaults to 24h before the server's current time. A tz-naive ``since`` is coerced to
    UTC so it never raises against the store's aware timestamps."""
    since_utc = as_utc(since) if since is not None else services.clock.now() - timedelta(hours=24)
    chunk_changed = services.chunks.events.activity_facts_since(since_utc, limit=limit)
    events = services.chunks.events.list_events(since=since_utc, limit=limit)
    runner_changed = services.registry.list_pause_facts_since(since_utc, limit=limit)
    return Activity(ActivityFeed.of(chunk_changed, events, runner_changed, limit=limit).rows).response()
