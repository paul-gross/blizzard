"""The hub event broker (D-067) — the SSE re-broadcast seam, walking-skeleton form.

Fact names double as event names (``events.md``): the hub re-broadcasts landed facts
over ``GET /api/events/stream`` so the board and runners keep live views current. In
the P6 walking skeleton this is a **terminating replay-buffer stub** (ORCHESTRATION.md
"SSE beyond a stub" is P7): mutating routes publish a ``chunk-changed`` event here, and
a connecting subscriber receives the recent buffer and the stream closes — a valid,
terminating ``text/event-stream`` response an ``EventSource`` reconnects to. The live
per-subscriber fan-out (an ``asyncio`` queue per connection, ``Last-Event-ID`` replay)
bolts onto this same publish surface without reshaping the routes.

``deque.append`` is atomic in CPython, so the sync route handlers publish without a
lock or an event-loop hop.
"""

from __future__ import annotations

import json
from collections import deque

CHUNK_CHANGED = "chunk-changed"


class EventBroker:
    """A bounded ring of recent SSE-framed events, published by the routes."""

    def __init__(self, *, history: int = 128) -> None:
        self._recent: deque[str] = deque(maxlen=history)

    def publish_chunk_changed(self, chunk_id: str, status: str) -> None:
        """Record a ``chunk-changed`` event for ``chunk_id`` and its derived status."""
        payload = json.dumps({"chunk_id": chunk_id, "status": status})
        self._recent.append(f"event: {CHUNK_CHANGED}\ndata: {payload}\n\n")

    def snapshot(self) -> list[str]:
        """The recent SSE-framed events, oldest first — the connect-time replay."""
        return list(self._recent)
