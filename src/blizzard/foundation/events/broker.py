"""The kind-agnostic SSE event broker.
Every published event carries a **monotonic id** and reaches every open connection live,
with a bounded tail (``history``) a reconnect replays from its ``Last-Event-ID``.
Publishers run on worker threads, subscribers on the event loop, so :meth:`publish`
crosses over with ``loop.call_soon_threadsafe``. A closed broker ends every connection it
holds. Nothing here knows an event's kind."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections import deque
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class Event:
    """One broadcast event: its monotonic id, its type, and its JSON payload."""

    id: int
    type: str
    data: str  # JSON-encoded payload

    def framed(self) -> str:
        """The ``text/event-stream`` frame — ``id`` first so a reconnect resumes."""
        return f"id: {self.id}\nevent: {self.type}\ndata: {self.data}\n\n"


class Subscriber:
    """One live SSE connection: its event queue, the loop that drains it, the principal it was opened
    for (``None`` when its daemon names none), and the signal that it has ended — its broker closed, or
    its owner was ended — set after every event published before the end is queued. ``joined_at`` is the
    broker's latest event id when it registered: an event above it reaches the queue, one at or below never does."""

    __slots__ = ("ended", "joined_at", "loop", "owner", "queue")

    def __init__(
        self,
        queue: asyncio.Queue[Event],
        loop: asyncio.AbstractEventLoop,
        owner: str | None = None,
        joined_at: int = 0,
    ) -> None:
        self.queue = queue
        self.loop = loop
        self.owner = owner
        self.joined_at = joined_at
        self.ended = asyncio.Event()


class EventBroker:
    """An id-stamped, bounded event ring with live per-connection fan-out."""

    def __init__(self, *, history: int = 256) -> None:
        self._history: deque[Event] = deque(maxlen=history)
        self._subscribers: set[Subscriber] = set()
        self._lock = threading.Lock()
        self._next_id = 0
        self._closed = False

    # --- publish -------------------------------------------------------------

    def publish(self, event_type: str, payload: dict[str, object]) -> int:
        """Record a typed event and fan it out live to every open connection.

        Returns the event's monotonic id. Minting the id, appending to the ring, and
        snapshotting the subscriber set happen under the lock; the handoff does not."""
        data = json.dumps(payload)
        with self._lock:
            self._next_id += 1
            event = Event(id=self._next_id, type=event_type, data=data)
            self._history.append(event)
            subscribers = list(self._subscribers)
        for sub in subscribers:
            # RuntimeError = the subscriber's loop has closed; its stream generator will
            # unsubscribe on its own exit, so dropping the handoff here is safe.
            with contextlib.suppress(RuntimeError):
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, event)
        return event.id

    # --- subscription --------------------------------------------------------

    def subscribe(self, owner: str | None = None) -> Subscriber:
        """Register a live connection opened for ``owner``, capturing the running loop for the handoff. A
        connection to a closed broker is ended from the start. The latest event id is recorded in the same
        lock acquisition that registers the connection, so it splits events exactly into those the
        connection was queued and those it was not."""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        with self._lock:
            sub = Subscriber(queue, loop, owner, self._next_id)
            if self._closed:
                sub.ended.set()
            else:
                self._subscribers.add(sub)
        return sub

    def close(self) -> None:
        """End every open connection — each stream sends what it already holds, then returns — and every
        later one. Idempotent; the broker's ring stays readable."""
        with self._lock:
            self._closed = True
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for sub in subscribers:
            # Queued behind every event handed over before the close, so none of them is lost.
            with contextlib.suppress(RuntimeError):
                sub.loop.call_soon_threadsafe(sub.ended.set)

    def end_subscriber(self, owner: str) -> None:
        """End every open connection of ``owner`` as :meth:`close` ends them all — each sends what it already
        holds, then returns — leaving every other connection and the broker open."""
        with self._lock:
            ended = [sub for sub in self._subscribers if sub.owner == owner]
            self._subscribers.difference_update(ended)
        for sub in ended:
            with contextlib.suppress(RuntimeError):
                sub.loop.call_soon_threadsafe(sub.ended.set)

    def unsubscribe(self, sub: Subscriber) -> None:
        """Drop a connection when its stream generator exits (disconnect / shutdown)."""
        with self._lock:
            self._subscribers.discard(sub)

    def subscriber_count(self) -> int:
        """The number of live connections — the no-leak invariant a shutdown test asserts."""
        with self._lock:
            return len(self._subscribers)

    def replay_since(self, last_event_id: int) -> list[Event]:
        """The buffered events newer than ``last_event_id`` — the reconnect replay tail."""
        with self._lock:
            return [e for e in self._history if e.id > last_event_id]

    def latest_id(self) -> int:
        """The id of the most recently published event (0 before any publish)."""
        with self._lock:
            return self._next_id

    def snapshot(self) -> list[Event]:
        """The full buffered ring, oldest first — the connect-time replay if no cursor."""
        with self._lock:
            return list(self._history)
