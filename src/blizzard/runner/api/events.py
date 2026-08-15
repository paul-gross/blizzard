"""The runner live-event stream — ``GET /api/events/stream`` (SSE), excluded from the OpenAPI
schema (blizzard#317 Phase 2). A subscriber registers with
:class:`~blizzard.runner.events.broker.EventBroker`, replays its buffered tail, then streams
live over the hub-shared :class:`Cursor`/:class:`Stream` machinery (D1). Mounted in the
``_HUMAN`` lane, gated at router inclusion. ``app.state.events`` is ``None`` on a
stream-less composer, where :class:`Stream` degrades to an idle connection (D2)."""

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
