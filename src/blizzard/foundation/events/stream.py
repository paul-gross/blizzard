"""The kind-agnostic SSE stream-response machinery (D1) — cursor resolution, the
replay-then-live handoff, keepalive, and disconnect/shutdown handling. A daemon's own
route binds :class:`Stream` to its own broker, reserved comment, and (in production)
keepalive cadence; a test binds a short ``keepalive_seconds`` so an emission is
observable without waiting out the real interval. Names no daemon's own vocabulary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Request

from blizzard.foundation.events.broker import EventBroker

#: Keepalive cadence for an idle connection — shorter than typical proxy idle timeouts.
DEFAULT_KEEPALIVE_SECONDS = 15.0


@dataclass(frozen=True)
class Cursor:
    """A subscriber's resume point, read tolerantly: a missing, empty, or malformed
    ``Last-Event-ID`` header (or ``?last_event_id=``) degrades to ``0`` — the whole tail."""

    last_event_id: int

    @classmethod
    def of(cls, request: Request) -> Cursor:
        raw = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
        try:
            return cls(int(raw) if raw is not None else 0)
        except ValueError:
            return cls(0)


@dataclass(frozen=True)
class Stream:
    """One subscriber's live connection — its broker, its request, its resume cursor,
    its daemon's reserved open-of-stream comment, and (optionally) the shutdown signal
    and keepalive interval it races its live wait against."""

    broker: EventBroker | None
    request: Request
    cursor: Cursor
    reserved_comment: str
    shutdown: asyncio.Event | None = None
    keepalive_seconds: float = DEFAULT_KEEPALIVE_SECONDS

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield the reserved comment, the buffered replay tail, then live events forever. Subscribing
        *before* reading the replay tail means an event published in the window between the two is caught by
        one side or the other; dedup by monotonic id makes the seam exact. Each live-wait races ``shutdown``
        against the queue read, so the generator returns promptly instead of on its next keepalive wake. It
        unsubscribes on any exit."""
        broker = self.broker
        if broker is None:
            # A store-free export/unit app carries no broker: open cleanly and idle.
            yield self.reserved_comment.encode()
            return

        shutdown = self.shutdown if self.shutdown is not None else asyncio.Event()
        sub = broker.subscribe()
        # A cursor above this broker's high-water mark is a prior process instance's id, not this one's
        # — clamp it, or every live event is silently dropped until a fresh broker's ids catch up.
        last_sent = min(self.cursor.last_event_id, broker.latest_id())
        try:
            yield self.reserved_comment.encode()
            for event in broker.replay_since(self.cursor.last_event_id):
                yield event.framed().encode()
                last_sent = event.id
            while True:
                if await self.request.is_disconnected():
                    return
                get_task = asyncio.ensure_future(sub.queue.get())
                shutdown_task = asyncio.ensure_future(shutdown.wait())
                try:
                    done, _ = await asyncio.wait(
                        {get_task, shutdown_task}, timeout=self.keepalive_seconds, return_when=asyncio.FIRST_COMPLETED
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
