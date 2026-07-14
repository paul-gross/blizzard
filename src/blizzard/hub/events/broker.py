"""The hub event broker (D-067) — the live SSE re-broadcast seam.

Fact names double as event names (``events.md``): the hub re-broadcasts landed facts
over ``GET /api/events/stream`` so the board and runners keep live views current. This
is the **real** in-process fan-out (P7, ORCHESTRATION.md — no cross-process bus): each
mutating route publishes a typed event here, every event carries a **monotonic id**,
and every open SSE connection receives it live. A reconnecting client replays the
buffered tail from its ``Last-Event-ID`` and re-GETs the REST resources to reconcile
anything that aged out of the bounded ring (``history``).

Threading: the sync FastAPI route handlers publish from an anyio worker thread, while
each SSE stream awaits its queue on the event loop. A subscriber captures its running
loop at :meth:`subscribe`, and :meth:`publish` hands each event across with
``loop.call_soon_threadsafe`` — the one safe bridge from a worker thread into an
event-loop-bound :class:`asyncio.Queue`. History mutation and the subscriber set are
guarded by a lock; ids are minted under it, so they are strictly monotonic across
concurrent publishers.

The event **type** names are the board's live vocabulary (the prompt's ``chunk-changed``,
``question-asked``/``-answered``, ``decision-opened``/``-resolved``, ``queue-changed``,
plus ``runner-changed`` for the fleet's liveness column); each maps to the hub facts it
is emitted on (see the call sites in ``blizzard.hub.api``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections import deque
from dataclasses import dataclass

# SSE event-type names — the board's live vocabulary (design/hub/web-app.md).
CHUNK_CHANGED = "chunk-changed"
QUESTION_ASKED = "question-asked"
QUESTION_ANSWERED = "question-answered"
DECISION_OPENED = "decision-opened"
DECISION_RESOLVED = "decision-resolved"
QUEUE_CHANGED = "queue-changed"
RUNNER_CHANGED = "runner-changed"


@dataclass(frozen=True)
class Event:
    """One broadcast event: its monotonic id, its type, and its JSON payload."""

    id: int
    type: str
    data: str  # JSON-encoded payload

    def framed(self) -> str:
        """The ``text/event-stream`` frame — ``id`` first so a reconnect resumes (D-067)."""
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
        snapshotting the subscriber set happen under the lock; the cross-thread handoff
        to each subscriber's loop happens outside it.
        """
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

    def publish_chunk_changed(self, chunk_id: str, status: str) -> int:
        """A chunk's derived status changed (D-004) — the board refreshes that row."""
        return self.publish(CHUNK_CHANGED, {"chunk_id": chunk_id, "status": status})

    def publish_question_asked(self, chunk_id: str, question_id: str) -> int:
        """A ``question.asked`` landed — the chunk parks ``waiting_on_human``."""
        return self.publish(QUESTION_ASKED, {"chunk_id": chunk_id, "question_id": question_id})

    def publish_question_answered(self, chunk_id: str, question_id: str) -> int:
        """A ``question.answered`` landed — the chunk leaves ``waiting_on_human``."""
        return self.publish(QUESTION_ANSWERED, {"chunk_id": chunk_id, "question_id": question_id})

    def publish_decision_opened(self, chunk_id: str, decision_id: str) -> int:
        """A gate ``decision.submitted`` opened — a human choice is awaited (D-045)."""
        return self.publish(DECISION_OPENED, {"chunk_id": chunk_id, "decision_id": decision_id})

    def publish_decision_resolved(self, chunk_id: str, decision_id: str) -> int:
        """A ``decision.resolved`` landed — the holding runner will advance the chunk."""
        return self.publish(DECISION_RESOLVED, {"chunk_id": chunk_id, "decision_id": decision_id})

    def publish_queue_changed(self) -> int:
        """The ready queue's membership or order changed — the board re-peeks (D-048)."""
        return self.publish(QUEUE_CHANGED, {})

    def publish_runner_changed(self, runner_id: str) -> int:
        """A runner's registry state changed (registered / liveness / paused, D-070)."""
        return self.publish(RUNNER_CHANGED, {"runner_id": runner_id})

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
