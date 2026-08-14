"""The runner live-event stream — ``GET /api/events/stream`` (SSE), excluded from the OpenAPI
schema (blizzard#317 Phase 2).

A subscriber registers with the :class:`~blizzard.runner.events.broker.EventBroker`, replays
the buffered tail newer than its ``Last-Event-ID``, then streams live until it disconnects.
The stream-response machinery itself — :class:`Cursor`, :class:`Stream` — is shared with the
hub (D1, blizzard#317); only the reserved open-of-stream comment below names this daemon.
Mounted in the ``_HUMAN`` lane (``runner/app.py``), so ``require_human_api`` gates it at
router inclusion — no route-level ``Depends`` of its own, unlike the hub's per-route
permission. ``app.state.events`` is ``None`` on every composer that has no stream to feed
(``blizzard runner tick``, the store-free/export app); :class:`Stream` degrades to an
idle, cleanly-opened connection in that case (D2)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from blizzard.foundation.events.broker import EventBroker
from blizzard.foundation.events.stream import Cursor, Stream

router = APIRouter(prefix="/api", tags=["runner"])

_RESERVED_COMMENT = ": blizzard runner event stream\n\n"


@router.get("/events/stream", include_in_schema=False)
async def events_stream(request: Request) -> StreamingResponse:
    """Subscribe to the live event stream, resuming from ``Last-Event-ID`` if present."""
    broker: EventBroker | None = getattr(request.app.state, "events", None)
    shutdown: asyncio.Event | None = getattr(request.app.state, "shutdown", None)
    stream = Stream(broker, request, Cursor.of(request), _RESERVED_COMMENT, shutdown)
    return StreamingResponse(stream.frames(), media_type="text/event-stream")
