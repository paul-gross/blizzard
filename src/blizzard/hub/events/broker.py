"""The hub event broker — the live SSE re-broadcast seam.

Fact names double as event names: the hub re-broadcasts landed facts
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
is emitted on (see the call sites in ``blizzard.hub.api``). A frame's payload carries only
what identifies the change — a consumer re-GETs the REST resource for the rest — except
where a frame is itself the news: ``runner-changed`` names its :data:`RunnerChangeKind`,
since the runner-registry read it stales cannot say which change fired it, and
``chunk-changed`` names its :data:`ChunkChangeCause` alongside the prev/current node,
prev status, runner id, and graph id (issue #212) — the Event log renders these directly
rather than re-deriving them from a re-GET.
"""

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

#: Every event-type name the broker can publish (issue #235) — the single enumerable
#: source the SSE contract test's corpus-closure check reads, mirroring the board's own
#: ``HUB_EVENT_TYPES`` (``fleet-live.ts``). A ninth constant added above without a slot
#: here would still be invisible to that check, exactly as adding one to
#: ``HUB_EVENT_TYPES`` on the TS side is what makes a new kind visible there — this
#: tuple, not the bare constants, is the broker's declared vocabulary.
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
        """A chunk's derived status changed — the board refreshes that row.

        The optionals are present-when-meaningful, the same shape :meth:`publish_runner_changed`
        established for issue #151: each is added to the payload only when supplied, never
        serialized as ``null``, so a chunk with no runner or no prior transition renders
        without placeholder junk (issue #212). ``key`` (issue #213) names the identity of the
        durable fact this frame describes — e.g. ``"transitions:tr_01J..."`` — the same
        table-qualified natural key :class:`~blizzard.hub.domain.work.ActivityRow` carries, so
        a page-load backfill row and this live frame can be recognized as the same fact by
        exact string equality. Absent (never a placeholder) for a cause with no fact table
        (``edited``) or when this call recorded nothing new (an idempotent no-op)."""
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

        Every kind still stales the board's fleet registry the same way, so the liveness
        column keeps refreshing on the ``heartbeat`` flood; the kind is what lets the Event
        log show the operator only the pause family. ``by`` rides the four pause/resume
        kinds (who set or cleared the brake) and ``reason`` the runner-local pair, which
        carries the free-text note off the ``runner.locally-paused``/``-resumed`` fact.
        ``key`` (issue #213) names the pause-family fact's identity
        (``runner_pause_facts``/``runner_local_pause_facts``); deliberately absent on
        ``registered``/``heartbeat``, which have no fact table and are muted client-side.
        """
        payload = RunnerChangedPayload(runner_id=runner_id, kind=kind, by=by, reason=reason, key=key).to_payload()
        return self.publish(RUNNER_CHANGED, payload)

    def publish_event_logged(
        self, *, severity: str, kind: str, chunk_id: str | None, runner_id: str, key: str | None = None
    ) -> int:
        """An operational event landed in the event log (issue #125) — the board's Events
        tab refreshes, and a chunk-named event also refreshes that chunk's card. The frame
        carries only the identifying fields the board's invalidation registry keys on; the
        row itself is read back off ``GET /api/events``. ``key`` (issue #213) names the
        ``event_log`` row's own id, matching :func:`~blizzard.hub.domain.work._event_row_to_activity`'s
        backfill key exactly."""
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
