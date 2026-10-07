"""The runner live-event stream — ``GET /api/events/stream`` (SSE), excluded from the OpenAPI
schema. Resumes from ``Last-Event-ID`` through :class:`Cursor`/:class:`Stream`; the broker and
shutdown event are resolved through :class:`RunnerWiring` and may be ``None``."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from blizzard.foundation.events.stream import Cursor, Stream
from blizzard.runner.api.wiring import RunnerWiring

router = APIRouter(prefix="/api", tags=["runner"])

_RESERVED_COMMENT = ": blizzard runner event stream\n\n"


@router.get("/events/stream", include_in_schema=False)
async def events_stream(request: Request) -> StreamingResponse:
    """Subscribe to the live event stream, resuming from ``Last-Event-ID`` if present."""
    wiring = RunnerWiring.of(request)
    stream = Stream(
        wiring.maybe_event_broker(), request, Cursor.of(request), _RESERVED_COMMENT, wiring.maybe_shutdown()
    )
    return StreamingResponse(stream.frames(), media_type="text/event-stream")
