"""The hub live-event stream — ``GET /api/events/stream`` (SSE), excluded from the OpenAPI schema.

A subscriber registers with the :class:`~blizzard.hub.events.broker.EventBroker`, replays the buffered
tail newer than its ``Last-Event-ID``, then streams live until it disconnects. Ids are monotonic, so an
event caught in both the replay and the live queue is emitted once. A periodic keepalive comment keeps
intermediaries from idling the connection out."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from blizzard.auth_core import FLEET_VIEW
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import ActivityFeed, ActivityRow, EventFeed, EventRow
from blizzard.hub.events.broker import EventBroker
from blizzard.wire.activity import ActivityResponse, ActivityView
from blizzard.wire.events import EventsResponse, EventView

router = APIRouter(prefix="/api", tags=["meta"])

_RESERVED_COMMENT = ": blizzard hub event stream\n\n"
#: Keepalive cadence for an idle connection — shorter than typical proxy idle timeouts.
_KEEPALIVE_SECONDS = 15.0


async def _stream(
    broker: EventBroker | None,
    request: Request,
    *,
    last_event_id: int,
    shutdown: asyncio.Event | None = None,
) -> AsyncIterator[bytes]:
    """Yield the reserved comment, the buffered replay tail, then live events forever. Subscribing
    *before* reading the replay tail means an event published in the window between the two is caught by
    one side or the other; dedup by monotonic id makes the seam exact. Each live-wait races ``shutdown``
    against the queue read, so the generator returns promptly instead of on its next keepalive wake. It
    unsubscribes on any exit."""
    if broker is None:
        # The store-free export/unit app carries no broker: open cleanly and idle.
        yield _RESERVED_COMMENT.encode()
        return

    shutdown = shutdown if shutdown is not None else asyncio.Event()
    sub = broker.subscribe()
    last_sent = last_event_id
    try:
        yield _RESERVED_COMMENT.encode()
        for event in broker.replay_since(last_event_id):
            yield event.framed().encode()
            last_sent = event.id
        while True:
            if await request.is_disconnected():
                return
            get_task = asyncio.ensure_future(sub.queue.get())
            shutdown_task = asyncio.ensure_future(shutdown.wait())
            try:
                done, _ = await asyncio.wait(
                    {get_task, shutdown_task}, timeout=_KEEPALIVE_SECONDS, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for task in (get_task, shutdown_task):
                    if not task.done():
                        task.cancel()
            if shutdown_task in done:
                return
            if get_task not in done:
                yield b": keepalive\n\n"
                continue
            event = get_task.result()
            if event.id <= last_sent:
                continue  # already emitted in the replay tail (dedup at the seam)
            yield event.framed().encode()
            last_sent = event.id
    finally:
        broker.unsubscribe(sub)


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
    last_event_id = _parse_last_event_id(request)
    return StreamingResponse(
        _stream(broker, request, last_event_id=last_event_id, shutdown=shutdown), media_type="text/event-stream"
    )


def _to_event_view(row: EventRow) -> EventView:
    """Map a domain :class:`EventRow` (an ``event_log`` row or a projected escalation) to
    its wire view."""
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
    events = services.chunks.list_events(
        severity=severity, runner_id=runner_id, chunk_id=chunk_id, since=since_utc, limit=limit
    )
    # The same predicates over the escalation projection: it is always `critical` and names no runner,
    # so a `severity`/`runner_id` filter excludes it wholesale; `chunk_id`/`since` narrow per row.
    escalations = services.chunks.list_open_escalations()
    if severity is not None and severity != "critical":
        escalations = []
    if runner_id is not None:
        escalations = []
    if chunk_id is not None:
        escalations = [e for e in escalations if e.chunk_id == chunk_id]
    if since_utc is not None:
        escalations = [e for e in escalations if e.recorded_at >= since_utc]
    feed = EventFeed.of(events, escalations).rows[:limit]
    return EventsResponse(events=[_to_event_view(row) for row in feed])


def _to_activity_view(row: ActivityRow) -> ActivityView:
    """Map a domain :class:`ActivityRow` to its wire view."""
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

    ``since`` defaults to 24h before the injected clock's own ``now()``, never ``datetime.now()``. A
    tz-naive ``since`` is coerced to UTC so it never raises against the store's aware timestamps."""
    since_utc = as_utc(since) if since is not None else services.clock.now() - timedelta(hours=24)
    chunk_changed = services.chunks.activity_facts_since(since_utc, limit=limit)
    events = services.chunks.list_events(since=since_utc, limit=limit)
    runner_changed = services.registry.list_pause_facts_since(since_utc, limit=limit)
    feed = ActivityFeed.of(chunk_changed, events, runner_changed, limit=limit).rows
    return ActivityResponse(activity=[_to_activity_view(row) for row in feed])


def _parse_last_event_id(request: Request) -> int:
    """The reconnect cursor from the ``Last-Event-ID`` header (or ``?last_event_id=``)."""
    raw = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0
