"""The hub live-event stream — ``GET /api/events/stream`` (SSE).

The hub re-broadcasts landed facts over Server-Sent Events; the board and each
runner subscribe outbound to keep live views streaming (tech-stack.md — live
updates are hand-rolled ``EventSource`` → RxJS → signals, *not* the generated
client, so this route is excluded from the OpenAPI schema).

Walking-skeleton form (D-067): a connecting subscriber receives the reserved comment
plus the recent ``chunk-changed`` events buffered by the
:class:`~blizzard.hub.events.broker.EventBroker`, then the stream closes — a valid,
terminating ``text/event-stream`` an ``EventSource`` reconnects to. The store-free
export app carries a broker too (empty buffer), so the stream still opens cleanly.
The live per-connection fan-out is P7 (ORCHESTRATION.md).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from blizzard.hub.events.broker import EventBroker

router = APIRouter(prefix="/api", tags=["meta"])

_RESERVED_COMMENT = ": blizzard hub event stream\n\n"


async def _stream(broker: EventBroker | None) -> AsyncIterator[bytes]:
    # A leading SSE comment (`:`-prefixed, ignored by EventSource) so a subscriber
    # connects cleanly even with an empty buffer, then the buffered events.
    yield _RESERVED_COMMENT.encode()
    if broker is not None:
        for event in broker.snapshot():
            yield event.encode()


@router.get("/events/stream", include_in_schema=False)
async def events_stream(request: Request) -> StreamingResponse:
    broker: EventBroker | None = getattr(request.app.state, "events", None)
    return StreamingResponse(_stream(broker), media_type="text/event-stream")
