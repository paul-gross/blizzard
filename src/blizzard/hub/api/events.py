"""The hub live-event stream — ``GET /api/events/stream`` (SSE), and ``POST /api/events``.

The hub re-broadcasts landed facts over Server-Sent Events; the board and each runner
subscribe outbound to keep live views streaming (live updates are hand-rolled
``EventSource`` → RxJS → signals, *not* the generated client, so the stream
route is excluded from the OpenAPI schema).

Live fan-out (P7): a connecting subscriber registers with the
:class:`~blizzard.hub.events.broker.EventBroker`, replays the buffered tail newer than
its ``Last-Event-ID`` (so a reconnect resumes without a gap), then streams every fresh
event live until it disconnects. A periodic keepalive comment keeps intermediaries from
idling the connection out. Ids are monotonic, so the replay-then-live handoff dedupes by
id — an event caught in both the replay and the live queue is emitted once.

``POST /api/events`` is the runner's store-and-forward fact push; every
landed fact re-broadcasts on the stream under its board vocabulary so the board refreshes
without polling.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.events.broker import EventBroker
from blizzard.wire.facts import (
    QUESTION_ASKED,
    RUNNER_LOCALLY_PAUSED,
    RUNNER_LOCALLY_RESUMED,
    RunnerFactAck,
    RunnerFactBatch,
)

router = APIRouter(prefix="/api", tags=["meta"])

_RESERVED_COMMENT = ": blizzard hub event stream\n\n"
#: Keepalive cadence for an idle connection — a comment line intermediaries pass through
#: without disturbing the ``EventSource``. Shorter than typical proxy idle timeouts.
_KEEPALIVE_SECONDS = 15.0


@router.post("/events", response_model=RunnerFactAck)
def ingest_runner_facts(
    batch: RunnerFactBatch, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerFactAck:
    """Land runner-minted facts, idempotent by per-runner seq high-water.

    The store-and-forward ingest: ``lease.minted`` (the fence input), ``escalation.recorded``,
    ``question.asked``, and ``answer.delivered`` ride the runner's outbound buffer here. A
    pushed seq at or below the runner's high-water mark is already-applied and re-acked; a
    fresh one is applied and advances the mark. Each freshly-applied fact re-broadcasts on
    the SSE stream so the board refreshes — ``chunk-changed`` for every touched chunk, and
    ``question-asked`` for a forwarded ask.
    """
    ack = services.facts.ingest(batch)
    if ack.applied:
        from blizzard.hub.domain.work import ChunkFacts, derive_chunk_status

        applied = set(ack.applied)
        for fact in batch.facts:
            if fact.seq not in applied:
                continue
            # Runner-scoped facts (issue #43) carry no chunk_id: they are about the runner,
            # so they refresh the fleet column, not a card. Handled before the chunk branch
            # below, which would otherwise skip them and land them invisibly — applied to
            # the store but never pushed, so the board would keep showing a runner as
            # claiming until something unrelated forced a refetch.
            if fact.kind in (RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED):
                services.events.publish_runner_changed(batch.runner_id)
                continue
            chunk_id = fact.payload.get("chunk_id")
            if not isinstance(chunk_id, str):
                continue
            if fact.kind == QUESTION_ASKED:
                question_id = fact.payload.get("question_id")
                if isinstance(question_id, str):
                    services.events.publish_question_asked(chunk_id, question_id)
            facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
            services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    return ack


async def _stream(broker: EventBroker | None, request: Request, *, last_event_id: int) -> AsyncIterator[bytes]:
    """Yield the reserved comment, the buffered replay tail, then live events forever.

    ``last_event_id`` is the reconnect cursor (``Last-Event-ID`` header). Subscribing
    *before* reading the replay tail means an event published in the window between the
    two is captured by the live queue and skipped in the tail (or vice-versa) — dedup by
    monotonic id makes the seam exact. The generator unsubscribes on any exit (client
    disconnect, cancellation, shutdown).
    """
    if broker is None:
        # The store-free export/unit app carries no broker: open cleanly and idle.
        yield _RESERVED_COMMENT.encode()
        return

    sub = broker.subscribe()
    last_sent = last_event_id
    try:
        yield _RESERVED_COMMENT.encode()
        for event in broker.replay_since(last_event_id):
            yield event.framed().encode()
            last_sent = event.id
        while True:
            if await request.is_disconnected():
                return
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=_KEEPALIVE_SECONDS)
            except TimeoutError:
                yield b": keepalive\n\n"
                continue
            if event.id <= last_sent:
                continue  # already emitted in the replay tail (dedup at the seam)
            yield event.framed().encode()
            last_sent = event.id
    finally:
        broker.unsubscribe(sub)


@router.get("/events/stream", include_in_schema=False)
async def events_stream(request: Request) -> StreamingResponse:
    """Subscribe to the live event stream, resuming from ``Last-Event-ID`` if present."""
    broker: EventBroker | None = getattr(request.app.state, "events", None)
    last_event_id = _parse_last_event_id(request)
    return StreamingResponse(_stream(broker, request, last_event_id=last_event_id), media_type="text/event-stream")


def _parse_last_event_id(request: Request) -> int:
    """The reconnect cursor from the ``Last-Event-ID`` header (or ``?last_event_id=``)."""
    raw = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    try:
        return int(raw) if raw is not None else 0
    except ValueError:
        return 0
