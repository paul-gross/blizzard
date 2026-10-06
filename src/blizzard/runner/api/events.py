"""The runner live-event stream — ``GET /api/events/stream`` (SSE), excluded from the OpenAPI
schema. Resumes from ``Last-Event-ID`` through :class:`Cursor`/:class:`Stream`; the broker and
shutdown event are read from ``app.state`` and may be ``None``."""

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
