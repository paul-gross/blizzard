"""The hub live-event stream — ``GET /api/events/stream`` (SSE), reserved.

The hub re-broadcasts landed facts over Server-Sent Events; the board and each
runner subscribe outbound to keep live views streaming (tech-stack.md — live
updates are hand-rolled ``EventSource`` → RxJS → signals, *not* the generated
client, so this route is excluded from the OpenAPI schema). This is the reserved
transport seam: the walking-skeleton fan-out lands in P6. Until then the stream
opens as ``text/event-stream``, emits a single reserved comment, and closes — a
valid, terminating SSE response that proves the seam without inventing events.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api", tags=["meta"])

_RESERVED_COMMENT = ": blizzard hub event stream reserved — no events until P6\n\n"


async def _reserved_stream() -> AsyncIterator[bytes]:
    # A single SSE comment line, then EOF. Comments (`:`-prefixed) are ignored by
    # EventSource, so a subscriber connects cleanly and simply sees no events yet.
    yield _RESERVED_COMMENT.encode()


@router.get("/events/stream", include_in_schema=False)
async def events_stream() -> StreamingResponse:
    return StreamingResponse(_reserved_stream(), media_type="text/event-stream")
