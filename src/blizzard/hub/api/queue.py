"""Queue routes — peek, reorder, and group (D-080/D-048).

``GET /queue/peek`` is the read-only ready-queue peek a runner's FILL step does before
claiming; ready is a **derived** status (a minted chunk with no live route, D-004) and
the queue's order is an explicit hub-side property (D-048). ``POST /queue/reorder`` is
the board's Prioritize control, and ``POST /chunks/{id}/group`` is the Group control —
both shape the ready queue without executing work. Controllers stay read-only over the
store and delegate the writes to the queue-shaping domain services
(``bzh:controller-read-only``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.queue import ChunkNotFound, ChunkNotReady
from blizzard.hub.domain.work import Chunk
from blizzard.wire.chunk import PmPointerModel
from blizzard.wire.queue import (
    ChunkGroupRequest,
    ChunkGroupResponse,
    QueuePeekEntry,
    QueuePeekResponse,
    QueueReorderRequest,
    QueueReorderResponse,
)

router = APIRouter(prefix="/api", tags=["queue"])


def _entries(ready: list[Chunk]) -> list[QueuePeekEntry]:
    return [
        QueuePeekEntry(
            chunk_id=chunk.chunk_id,
            graph_id=chunk.graph_id,
            position=position,
            pm_pointers=[PmPointerModel(source=p.source, ref=p.ref) for p in chunk.pm_pointers],
        )
        for position, chunk in enumerate(ready)
    ]


@router.get("/queue/peek", response_model=QueuePeekResponse)
def peek_queue(services: Annotated[HubServices, Depends(get_services)]) -> QueuePeekResponse:
    """The hub-ordered ready queue, read-only (D-080) — honours reorder + grouping (D-048)."""
    return QueuePeekResponse(entries=_entries(services.queue.ordered_ready()))


@router.post("/queue/reorder", response_model=QueueReorderResponse)
def reorder_queue(
    request: QueueReorderRequest, services: Annotated[HubServices, Depends(get_services)]
) -> QueueReorderResponse:
    """Move a ready chunk to a queue position — the board's Prioritize control (D-048)."""
    try:
        services.queue.reorder(request.chunk_id, to_index=request.position)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChunkNotReady as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    services.events.publish_queue_changed()
    return QueueReorderResponse(entries=_entries(services.queue.ordered_ready()))


@router.post("/chunks/{chunk_id}/group", response_model=ChunkGroupResponse)
def group_chunks(
    chunk_id: str,
    request: ChunkGroupRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> object:
    """Merge unacquired chunks into ``chunk_id`` — the board's Group control (D-048/D-076)."""
    try:
        survivor = services.group.group(chunk_id, request.merge_chunk_ids)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChunkNotReady as exc:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})
    # Each merged-away chunk vanished from the listings, and the survivor's pointers grew:
    # refresh the queue and the survivor's row on the board.
    services.events.publish_queue_changed()
    services.events.publish_chunk_changed(survivor.chunk_id, "ready")
    return ChunkGroupResponse(
        chunk_id=survivor.chunk_id,
        pm_pointers=[PmPointerModel(source=p.source, ref=p.ref) for p in survivor.pm_pointers],
        merged_chunk_ids=[m for m in request.merge_chunk_ids if m != survivor.chunk_id],
    )
