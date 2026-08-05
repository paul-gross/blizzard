"""Queue routes — read, replace, and group — the operator surface (issues #87, #104).

Ready is a **derived** status (a minted chunk with no live route) and the queue's order
is an explicit hub-side property. Controllers stay read-only over the store and delegate
the writes to the queue-shaping domain services (``bzh:controller-read-only``); a
runner's bearer token is rejected rather than treated as anonymous-plus-credential."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import FLEET_VIEW, QUEUE_REORDER
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.queue import ChunkNotFound, ChunkNotGroupable
from blizzard.hub.domain.work import Chunk
from blizzard.wire.chunk import WorkRefModel
from blizzard.wire.queue import (
    ChunkGroupRequest,
    ChunkGroupResponse,
    QueuePeekEntry,
    QueuePeekResponse,
    QueuePositionRequest,
    QueueReplaceRequest,
)

router = APIRouter(prefix="/api", tags=["queue"], dependencies=[Depends(reject_runner_principal)])


def _entries(ready: list[Chunk]) -> list[QueuePeekEntry]:
    return [
        QueuePeekEntry(
            chunk_id=chunk.chunk_id,
            graph_id=chunk.graph_id,
            position=position,
            work_refs=[WorkRefModel(source=p.source, ref=p.ref) for p in chunk.work_refs],
        )
        for position, chunk in enumerate(ready)
    ]


@router.get("/queue", response_model=QueuePeekResponse, dependencies=[Depends(require(FLEET_VIEW))])
def get_queue(services: Annotated[HubServices, Depends(get_services)]) -> QueuePeekResponse:
    """The hub-ordered ready queue, read-only — honours reorder/replace + grouping."""
    return QueuePeekResponse(entries=_entries(services.queue.ordered_ready()))


@router.put("/queue", response_model=QueuePeekResponse, dependencies=[Depends(require(QUEUE_REORDER))])
def replace_queue(
    request: QueueReplaceRequest, services: Annotated[HubServices, Depends(get_services)]
) -> QueuePeekResponse:
    """Idempotent whole-order replacement of the ready queue.

    Resolves every named id against the current ready set (``bzh:domain-takes-objects``):
    ``409`` names the first id that is not ready, ``422`` a duplicate id. An unnamed
    ready chunk keeps its relative order, appended after the named ones."""
    if len(set(request.chunk_ids)) != len(request.chunk_ids):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="chunk_ids must not repeat")
    ready = services.queue.ordered_ready()
    ready_by_id = {chunk.chunk_id: chunk for chunk in ready}
    for chunk_id in request.chunk_ids:
        if chunk_id not in ready_by_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"chunk {chunk_id} is not a ready chunk")
    named_ids = set(request.chunk_ids)
    ordered = [ready_by_id[chunk_id] for chunk_id in request.chunk_ids]
    ordered.extend(chunk for chunk in ready if chunk.chunk_id not in named_ids)
    services.queue.replace_order(ordered)
    services.events.publish_queue_changed()
    return QueuePeekResponse(entries=_entries(services.queue.ordered_ready()))


@router.post("/queue/position", response_model=QueuePeekResponse, dependencies=[Depends(require(QUEUE_REORDER))])
def reposition_queue(
    request: QueuePositionRequest, services: Annotated[HubServices, Depends(get_services)]
) -> QueuePeekResponse:
    """Single-chunk fractional reorder (issue #137).

    Resolves both ids against the current ready set (``bzh:domain-takes-objects``):
    ``409`` names either one if it is not ready, ``422`` rejects a self-anchor.
    ``after_chunk_id=null`` moves the chunk to the top of the queue."""
    if request.after_chunk_id == request.chunk_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="after_chunk_id must not equal chunk_id"
        )
    ready_by_id = {chunk.chunk_id: chunk for chunk in services.queue.ordered_ready()}
    chunk = ready_by_id.get(request.chunk_id)
    if chunk is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=f"chunk {request.chunk_id} is not a ready chunk"
        )
    after: Chunk | None = None
    if request.after_chunk_id is not None:
        after = ready_by_id.get(request.after_chunk_id)
        if after is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f"chunk {request.after_chunk_id} is not a ready chunk"
            )
    services.queue.reposition(chunk, after)
    services.events.publish_queue_changed()
    return QueuePeekResponse(entries=_entries(services.queue.ordered_ready()))


@router.post(
    "/chunks/{chunk_id}/group", response_model=ChunkGroupResponse, dependencies=[Depends(require(QUEUE_REORDER))]
)
def group_chunks(
    chunk_id: str,
    request: ChunkGroupRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> object:
    """Merge unacquired chunks into ``chunk_id``.

    Accepts ``not_ready`` and ``ready`` participants alike (issue #141); 409 names the
    first chunk a runner holds, or one already finished.
    """
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)
    try:
        result = services.group.group(chunk_id, request.merge_chunk_ids)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChunkNotGroupable as exc:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})
    # The survivor's status comes from the group result, never a ``"ready"`` constant:
    # grouping backlog chunks leaves a backlog survivor (issue #141).
    survivor = result.survivor
    services.events.publish_queue_changed()
    key = f"chunk_grouped:{result.grouped_id}" if result.grouped_id is not None else None
    chunk_events.publish_chunk_changed(
        services, survivor.chunk_id, cause="grouped", prev_status=prev_status, status=result.status.value, key=key
    )
    return ChunkGroupResponse(
        chunk_id=survivor.chunk_id,
        work_refs=[WorkRefModel(source=p.source, ref=p.ref) for p in survivor.work_refs],
        merged_chunk_ids=[m for m in request.merge_chunk_ids if m != survivor.chunk_id],
    )
