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
stream is the board's own anonymous read, and stays deliberately dependency-free (no
``get_services``) so it opens cleanly even on the store-free export/unit app.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from blizzard.hub.events.broker import EventBroker

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
async def events_stream(request: Request) -> StreamingResponse:
    """Subscribe to the live event stream, resuming from ``Last-Event-ID`` if present."""
    broker: EventBroker | None = getattr(request.app.state, "events", None)
    shutdown: asyncio.Event | None = getattr(request.app.state, "shutdown", None)
    last_event_id = _parse_last_event_id(request)
    return StreamingResponse(
        _stream(broker, request, last_event_id=last_event_id, shutdown=shutdown), media_type="text/event-stream"
    )


def _parse_last_event_id(request: Request) -> int:
    """The reconnect cursor from the ``Last-Event-ID`` header (or ``?last_event_id=``)."""
    raw = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0
