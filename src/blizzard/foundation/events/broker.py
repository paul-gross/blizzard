"""The kind-agnostic SSE event broker (D1) — the live re-broadcast seam both daemons bind.

Every published event carries a **monotonic id** and reaches every open connection live,
with a bounded tail (``history``) a reconnect replays from its ``Last-Event-ID``. Publishers
run on worker threads and subscribers on the event loop, so a subscriber captures its loop at
:meth:`subscribe` and :meth:`publish` crosses over with ``loop.call_soon_threadsafe``. A
daemon's own broker wraps this one with its own domain-named ``publish_*`` helpers and event
vocabulary; nothing here knows an event's kind beyond its bare type name."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections import deque
from dataclasses import dataclass


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
    """One live SSE connection: its event queue and the loop that drains it."""

    __slots__ = ("loop", "queue")

    def __init__(self, queue: asyncio.Queue[Event], loop: asyncio.AbstractEventLoop) -> None:
        self.queue = queue
        self.loop = loop


class EventBroker:
    """An id-stamped, bounded event ring with live per-connection fan-out."""

    def __init__(self, *, history: int = 256) -> None:
        self._history: deque[Event] = deque(maxlen=history)
        self._subscribers: set[Subscriber] = set()
        self._lock = threading.Lock()
        self._next_id = 0

    # --- publish (called from the sync route handlers) ----------------------

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

    # --- subscription (called from the async SSE handler) -------------------

    def subscribe(self) -> Subscriber:
        """Register a live connection, capturing the running loop for the handoff."""
        sub = Subscriber(asyncio.Queue(), asyncio.get_running_loop())
        with self._lock:
            self._subscribers.add(sub)
        return sub

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
