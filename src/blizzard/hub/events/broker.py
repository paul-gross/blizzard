"""The hub event broker — the live SSE re-broadcast seam.

Every published event carries a **monotonic id** and reaches every open connection live,
with a bounded tail (``history``) a reconnect replays from its ``Last-Event-ID``. Publishers
run on worker threads and subscribers on the event loop, so a subscriber captures its loop at
:meth:`subscribe` and :meth:`publish` crosses over with ``loop.call_soon_threadsafe``."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
from collections import deque
from dataclasses import dataclass

from blizzard.wire.sse import (
    ChunkChangeCause,
    ChunkChangedPayload,
    DecisionOpenedPayload,
    DecisionResolvedPayload,
    EventLoggedPayload,
    QuestionAnsweredPayload,
    QuestionAskedPayload,
    QueueChangedPayload,
    RunnerChangedPayload,
    RunnerChangeKind,
)

# SSE event-type names — the board's live vocabulary.
CHUNK_CHANGED = "chunk-changed"
QUESTION_ASKED = "question-asked"
QUESTION_ANSWERED = "question-answered"
DECISION_OPENED = "decision-opened"
DECISION_RESOLVED = "decision-resolved"
QUEUE_CHANGED = "queue-changed"
RUNNER_CHANGED = "runner-changed"
EVENT_LOGGED = "event-logged"

#: Every event-type name the broker can publish (issue #235). This tuple, not the bare
#: constants above, is the broker's declared vocabulary.
EVENT_TYPES: tuple[str, ...] = (
    CHUNK_CHANGED,
    QUESTION_ASKED,
    QUESTION_ANSWERED,
    DECISION_OPENED,
    DECISION_RESOLVED,
    QUEUE_CHANGED,
    RUNNER_CHANGED,
    EVENT_LOGGED,
)


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

    def publish_chunk_changed(
        self,
        chunk_id: str,
        status: str,
        *,
        prev_status: str | None = None,
        prev_node: str | None = None,
        node: str | None = None,
        runner_id: str | None = None,
        cause: ChunkChangeCause | None = None,
        graph_id: str | None = None,
        key: str | None = None,
    ) -> int:
        """A chunk's derived status changed.

        Optionals are added to the payload only when supplied, never serialized as
        ``null`` (issue #212). ``key`` (issue #213) is the table-qualified natural key of
        the fact this frame describes, absent when there is no such fact."""
        payload = ChunkChangedPayload(
            chunk_id=chunk_id,
            status=status,
            prev_status=prev_status,
            prev_node=prev_node,
            node=node,
            runner_id=runner_id,
            cause=cause,
            graph_id=graph_id,
            key=key,
        ).to_payload()
        return self.publish(CHUNK_CHANGED, payload)

    def publish_question_asked(self, chunk_id: str, question_id: str, *, key: str | None = None) -> int:
        """A ``question.asked`` landed — the chunk parks ``waiting_on_human``."""
        payload = QuestionAskedPayload(chunk_id=chunk_id, question_id=question_id, key=key).to_payload()
        return self.publish(QUESTION_ASKED, payload)

    def publish_question_answered(self, chunk_id: str, question_id: str, *, key: str | None = None) -> int:
        """A ``question.answered`` landed — the chunk leaves ``waiting_on_human``."""
        payload = QuestionAnsweredPayload(chunk_id=chunk_id, question_id=question_id, key=key).to_payload()
        return self.publish(QUESTION_ANSWERED, payload)

    def publish_decision_opened(self, chunk_id: str, decision_id: str, *, key: str | None = None) -> int:
        """A gate ``decision.submitted`` opened — a human choice is awaited."""
        payload = DecisionOpenedPayload(chunk_id=chunk_id, decision_id=decision_id, key=key).to_payload()
        return self.publish(DECISION_OPENED, payload)

    def publish_decision_resolved(self, chunk_id: str, decision_id: str, *, key: str | None = None) -> int:
        """A ``decision.resolved`` landed — the holding runner will advance the chunk."""
        payload = DecisionResolvedPayload(chunk_id=chunk_id, decision_id=decision_id, key=key).to_payload()
        return self.publish(DECISION_RESOLVED, payload)

    def publish_queue_changed(self) -> int:
        """The ready queue's membership or order changed — the board re-peeks.

        Carries no ``key`` (issue #213): a reorder writes N rows with no per-row news, so
        there is no single durable fact this frame could name."""
        return self.publish(QUEUE_CHANGED, QueueChangedPayload().to_payload())

    def publish_runner_changed(
        self,
        runner_id: str,
        *,
        kind: RunnerChangeKind,
        by: str | None = None,
        reason: str | None = None,
        key: str | None = None,
    ) -> int:
        """A runner's registry state changed — ``kind`` names which change (issue #151).

        ``by`` rides the four pause/resume kinds and ``reason`` the runner-local pair.
        ``key`` (issue #213) names the pause-family fact's identity, absent on
        ``registered``/``heartbeat``, which have no fact table."""
        payload = RunnerChangedPayload(runner_id=runner_id, kind=kind, by=by, reason=reason, key=key).to_payload()
        return self.publish(RUNNER_CHANGED, payload)

    def publish_event_logged(
        self, *, severity: str, kind: str, chunk_id: str | None, runner_id: str, key: str | None = None
    ) -> int:
        """An operational event landed in the event log (issue #125). The frame carries
        only identifying fields; the row itself is read back off ``GET /api/events``.
        ``key`` (issue #213) names the ``event_log`` row's own id."""
        payload = EventLoggedPayload(
            severity=severity, kind=kind, chunk_id=chunk_id, runner_id=runner_id, key=key
        ).to_payload()
        return self.publish(EVENT_LOGGED, payload)

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
