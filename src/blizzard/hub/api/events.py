"""The hub live-event stream — ``GET /api/events/stream`` (SSE).

The hub re-broadcasts landed facts over Server-Sent Events; the board and each
runner subscribe outbound to keep live views streaming (tech-stack.md — live
updates are hand-rolled ``EventSource`` → RxJS → signals, *not* the generated
client, so this route is excluded from the OpenAPI schema).

Walking-skeleton form (D-067): a connecting subscriber receives the reserved comment
plus the recent ``chunk-changed`` events buffered by the
:class:`~blizzard.hub.events.broker.EventBroker`, then the stream closes — a valid,
terminating ``text/event-stream`` an ``EventSource`` reconnects to. The store-free
export app carries a broker too (empty buffer), so the stream still opens cleanly.
The live per-connection fan-out is P7 (ORCHESTRATION.md).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.events.broker import EventBroker
from blizzard.wire.facts import RunnerFactAck, RunnerFactBatch

router = APIRouter(prefix="/api", tags=["meta"])

_RESERVED_COMMENT = ": blizzard hub event stream\n\n"


@router.post("/events", response_model=RunnerFactAck)
def ingest_runner_facts(
    batch: RunnerFactBatch, services: Annotated[HubServices, Depends(get_services)]
) -> RunnerFactAck:
    """Land runner-minted facts, idempotent by per-runner seq high-water (D-069/D-044).

    The store-and-forward ingest: ``lease.minted`` (the fence input) and
    ``escalation.recorded`` ride the runner's outbound buffer here. A pushed seq at or
    below the runner's high-water mark is already-applied and re-acked; a fresh one is
    applied and advances the mark. Emits ``chunk-changed`` for each chunk a landed
    fact touched so the board refreshes.
    """
    ack = services.facts.ingest(batch)
    if ack.applied:
        from blizzard.hub.domain.work import ChunkFacts, derive_chunk_status

        touched = {p.get("chunk_id") for f in batch.facts if f.seq in ack.applied for p in [f.payload]}
        for chunk_id in touched:
            if not isinstance(chunk_id, str):
                continue
            facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
            services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    return ack


async def _stream(broker: EventBroker | None) -> AsyncIterator[bytes]:
    # A leading SSE comment (`:`-prefixed, ignored by EventSource) so a subscriber
    # connects cleanly even with an empty buffer, then the buffered events.
    yield _RESERVED_COMMENT.encode()
    if broker is not None:
        for event in broker.snapshot():
            yield event.encode()


@router.get("/events/stream", include_in_schema=False)
async def events_stream(request: Request) -> StreamingResponse:
    broker: EventBroker | None = getattr(request.app.state, "events", None)
    return StreamingResponse(_stream(broker), media_type="text/event-stream")
