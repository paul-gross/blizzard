"""The hub live-event stream — ``GET /api/events/stream`` (SSE).

The hub re-broadcasts landed facts over Server-Sent Events; the board and each runner
subscribe outbound to keep live views streaming (live updates are hand-rolled
``EventSource`` → RxJS → signals, *not* the generated client, so the stream
route is excluded from the OpenAPI schema).

Live fan-out (P7): a connecting subscriber registers with the
:class:`~blizzard.hub.events.broker.EventBroker`, replays the buffered tail newer than
its ``Last-Event-ID`` (so a reconnect resumes without a gap), then streams every fresh
event live until it disconnects. A periodic keepalive comment keeps intermediaries from
idling the connection out. Ids are monotonic, so the replay-then-live handoff dedupes by
id — an event caught in both the replay and the live queue is emitted once.

The runner's store-and-forward fact push (``POST /events``) moved to the
runner-authenticated fleet router (:mod:`blizzard.hub.api.fleet`, issue #87) — this
stream is the board's own read, human-plane gated on ``fleet:view`` (issue #91): a
``guest`` is refused here exactly as on every other board read. Identity is resolved
*before* streaming starts (``require(FLEET_VIEW)``), then the generator stays
broker-first — no ``get_services`` inside :func:`_stream` itself. Under the default
``auth.mode = "none"`` ``require()`` never touches the store (``hub/api/auth_session.py``),
so this route stays dependency-free in effect on the store-free export/unit app, which
always builds with that default.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
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
from blizzard.hub.domain.work import EventRow, derive_event_feed
from blizzard.hub.events.broker import EventBroker
from blizzard.wire.events import EventsResponse, EventView

router = APIRouter(prefix="/api", tags=["meta"])

_RESERVED_COMMENT = ": blizzard hub event stream\n\n"
#: Keepalive cadence for an idle connection — a comment line intermediaries pass through
#: without disturbing the ``EventSource``. Shorter than typical proxy idle timeouts.
_KEEPALIVE_SECONDS = 15.0


async def _stream(
    broker: EventBroker | None,
    request: Request,
    *,
    last_event_id: int,
    shutdown: asyncio.Event | None = None,
) -> AsyncIterator[bytes]:
    """Yield the reserved comment, the buffered replay tail, then live events forever.

    ``last_event_id`` is the reconnect cursor (``Last-Event-ID`` header). Subscribing
    *before* reading the replay tail means an event published in the window between the
    two is captured by the live queue and skipped in the tail (or vice-versa) — dedup by
    monotonic id makes the seam exact. ``shutdown`` is the app-lifetime signal
    (``app.state.shutdown``, set by ``blizzard.hub.app._lifespan`` on server shutdown);
    each live-wait races it against the queue read instead of waiting on the queue alone, so
    the generator returns promptly on shutdown rather than on its next keepalive wake. A
    caller with no shutdown signal to offer (the store-free export/unit app, or a direct
    test call) gets a private ``Event`` that is never set — the race then behaves exactly
    like the old bare queue wait. The generator unsubscribes on any exit: client disconnect,
    cancellation, or this shutdown signal.
    """
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
    """The operational event feed — the ``event_log`` unified with every currently-open
    escalation (issue #125), newest-and-most-severe first, bounded.

    A human-plane board read: ``reject_runner_principal`` keeps a runner's bearer out and
    ``require(FLEET_VIEW)`` gates it exactly like ``GET /decisions``. The ``severity`` /
    ``runner_id`` / ``chunk_id`` / ``since`` filters apply to the ``event_log`` half; the
    open-escalation projection is always unioned in (a ``needs_human`` chunk is a standing
    surface, not a filterable log row). A malformed ``since`` 422s via FastAPI's own
    datetime coercion; a well-formed but tz-naive ``since`` (an offset-less ISO string) is
    coerced to UTC (``as_utc``) so the projection's aware ``recorded_at`` comparison below
    never raises against it — the store half is already protected by ``UtcDateTime``."""
    since_utc = as_utc(since) if since is not None else None
    events = services.chunks.list_events(
        severity=severity, runner_id=runner_id, chunk_id=chunk_id, since=since_utc, limit=limit
    )
    # Filter the open-escalation projection by the SAME predicates so the unified feed is
    # internally consistent: a projected escalation is always `critical`/`needs-human` and
    # names no runner, so a `severity != critical` or any `runner_id` filter excludes them
    # all; `chunk_id`/`since` narrow per row.
    escalations = services.chunks.list_open_escalations()
    if severity is not None and severity != "critical":
        escalations = []
    if runner_id is not None:
        escalations = []
    if chunk_id is not None:
        escalations = [e for e in escalations if e.chunk_id == chunk_id]
    if since_utc is not None:
        escalations = [e for e in escalations if e.recorded_at >= since_utc]
    feed = derive_event_feed(events, escalations)[:limit]
    return EventsResponse(events=[_to_event_view(row) for row in feed])


def _parse_last_event_id(request: Request) -> int:
    """The reconnect cursor from the ``Last-Event-ID`` header (or ``?last_event_id=``)."""
    raw = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0
