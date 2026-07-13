"""Queue routes — ``GET /api/queue/peek`` (D-080).

The read-only ready-queue peek a runner's FILL step does before claiming. Ready is a
**derived** status — a minted chunk with no live route (D-004) — so the peek lists
:meth:`~blizzard.hub.store.internal.chunk_store.ChunkStore.list_ready`, ordered FIFO
by mint time in the walking skeleton (queue shaping is P7, ORCHESTRATION.md).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.wire.chunk import PmPointerModel
from blizzard.wire.queue import QueuePeekEntry, QueuePeekResponse

router = APIRouter(prefix="/api", tags=["queue"])


@router.get("/queue/peek", response_model=QueuePeekResponse)
def peek_queue(services: Annotated[HubServices, Depends(get_services)]) -> QueuePeekResponse:
    """The hub-ordered ready queue, read-only (D-080)."""
    ready = sorted(services.chunks.list_ready(), key=lambda c: c.minted_at)
    entries = [
        QueuePeekEntry(
            chunk_id=chunk.chunk_id,
            graph_id=chunk.graph_id,
            position=position,
            pm_pointers=[PmPointerModel(provider=p.provider, url=p.url) for p in chunk.pm_pointers],
        )
        for position, chunk in enumerate(ready)
    ]
    return QueuePeekResponse(entries=entries)
