"""The kind-agnostic SSE stream-response machinery — cursor resolution, the
replay-then-live handoff, keepalive, and disconnect/shutdown handling. A daemon's own
route binds :class:`Stream` to its own broker and reserved comment; both production
routes idle at the one shared keepalive cadence, ``DEFAULT_KEEPALIVE_SECONDS``. A
test binds a short ``keepalive_seconds`` so an emission is observable without waiting
out the real interval. Names no daemon's own vocabulary."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request

from blizzard.foundation.events.broker import Event, EventBroker
from blizzard.foundation.roles import domain_model

#: Keepalive cadence for an idle connection — shorter than typical proxy idle timeouts.
DEFAULT_KEEPALIVE_SECONDS = 15.0


class CursorRefused(ValueError):
    """A request's join point is one no stream can honour: an unknown ``from`` value, or ``from=latest``
    alongside ``?last_event_id=``."""


@domain_model
@dataclass(frozen=True)
class Cursor:
    """A subscriber's resume point. ``Last-Event-ID`` (or ``?last_event_id=``) is read tolerantly: a missing,
    empty, or malformed one is not ``readable`` and degrades to ``0`` — the whole tail. ``?from=latest`` joins at
    the latest event instead, skipping the replay ring; only a readable ``Last-Event-ID`` at or below the join
    mark beats it, so an ``EventSource`` reconnect still resumes."""

    last_event_id: int
    from_latest: bool = False
    readable: bool = True

    @classmethod
    def of(cls, request: Request) -> Cursor:
        """Read the cursor off ``request``; raises :class:`CursorRefused` for an unknown ``from`` value, or
        for ``from=latest`` with a ``last_event_id`` query key present, even an empty one."""
        params = request.query_params
        from_latest = False
        if "from" in params:
            if params["from"] != "latest":
                raise CursorRefused(f"unknown 'from' value {params['from']!r}; the only join point is 'latest'")
            if "last_event_id" in params:
                raise CursorRefused(
                    "'from=latest' cannot be combined with 'last_event_id'; send 'Last-Event-ID' to resume"
                )
            from_latest = True
        raw = request.headers.get("last-event-id") or params.get("last_event_id")
        try:
            return cls(int(raw) if raw is not None else 0, from_latest, raw is not None)
        except ValueError:
            return cls(0, from_latest, False)

    def resume_from(self, joined_at: int) -> int:
        """The event id to replay after, and to dedup live events against, for a subscriber that joined when the
        broker's latest id was ``joined_at``. A cursor above ``joined_at`` is a prior instance's id: it degrades to
        the whole tail, or under ``from=latest`` to ``joined_at`` itself, which replays nothing."""
        if self.from_latest:
            return self.last_event_id if self.readable and self.last_event_id <= joined_at else joined_at
        return self.last_event_id if self.last_event_id <= joined_at else 0


@dataclass(frozen=True)
class Stream:
    """One subscriber's live connection — its broker, its request, its resume cursor,
    its daemon's reserved open-of-stream comment, and (optionally) the shutdown signal
    and keepalive interval it races its live wait against, the predicate selecting which
    events it is sent, and the principal it is opened for. Without ``admits`` every event is
    sent."""

    broker: EventBroker | None
    request: Request
    cursor: Cursor
    reserved_comment: str
    shutdown: asyncio.Event | None = None
    keepalive_seconds: float = DEFAULT_KEEPALIVE_SECONDS
    #: Whether an event is sent to this subscriber — awaited on the event loop, so it never reads a store
    #: there. Applied alike to the replay tail and to live events; a refused event is skipped.
    admits: Callable[[Event], Awaitable[bool]] | None = None
    #: The principal the subscription is opened for, so its broker can end that principal's streams.
    owner: str | None = None
    #: The session the subscription is opened under, so its broker can end that session's streams alone.
    session: str | None = None

    async def frames(self) -> AsyncIterator[bytes]:
        """Yield the reserved comment, the buffered replay tail, then live events until the broker closes.
        Subscribing *before* reading the replay tail means an event published in the window between the two is
        caught by one side or the other; dedup by monotonic id makes the seam exact. Each live-wait races
        ``shutdown`` and the broker's close against the queue read, so the generator returns promptly instead
        of on its next keepalive wake — after a close, once it has sent the events already queued. It
        unsubscribes on any exit."""
        broker = self.broker
        if broker is None:
            # A store-free export/unit app carries no broker: open cleanly and idle.
            yield self.reserved_comment.encode()
            return

        shutdown = self.shutdown if self.shutdown is not None else asyncio.Event()
        sub = broker.subscribe(self.owner, self.session)
        # One value, taken against the mark recorded at registration, shared by the replay and the dedup below.
        resume_from = self.cursor.resume_from(sub.joined_at)
        last_sent = resume_from
        try:
            yield self.reserved_comment.encode()
            for event in broker.replay_since(resume_from):
                if await self._admits(event):
                    yield event.framed().encode()
                last_sent = event.id
            while True:
                if await self.request.is_disconnected():
                    return
                get_task = asyncio.ensure_future(sub.queue.get())
                shutdown_task = asyncio.ensure_future(shutdown.wait())
                ended_task = asyncio.ensure_future(sub.ended.wait())
                tasks = (get_task, shutdown_task, ended_task)
                try:
                    done, _ = await asyncio.wait(
                        set(tasks), timeout=self.keepalive_seconds, return_when=asyncio.FIRST_COMPLETED
                    )
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                if shutdown_task in done:
                    return
                if not done:
                    yield b": keepalive\n\n"
                    continue
                events = [get_task.result()] if get_task in done else []
                ended = ended_task in done
                if ended:
                    events.extend(_queued(sub.queue))
                for event in events:
                    if event.id <= last_sent:
                        continue  # already emitted in the replay tail (dedup at the seam)
                    if await self._admits(event):
                        yield event.framed().encode()
                    last_sent = event.id
                if ended:
                    return
        finally:
            broker.unsubscribe(sub)

    async def _admits(self, event: Event) -> bool:
        return True if self.admits is None else await self.admits(event)


def _queued(queue: asyncio.Queue[Event]) -> list[Event]:
    """Everything ``queue`` holds now, without waiting."""
    events: list[Event] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events
