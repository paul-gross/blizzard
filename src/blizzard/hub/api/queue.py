"""Queue routes — read, replace, and group — the anonymous **operator**
surface (issues #87, #104).

``GET /api/queue`` is the read-only ready-queue view the board's queue panel polls;
ready is a **derived** status (a minted chunk with no live route) and the queue's order
is an explicit hub-side property. A runner's FILL step peeks the same ready queue
through its own fleet-side counterpart (``GET /api/fleet/queue/peek``,
:mod:`blizzard.hub.api.fleet`) rather than this route — both share :func:`_entries`.
``PUT /api/queue`` is the explicit whole-order operator verb behind ``blizzard hub queue
set`` — no longer any client's reorder path, now that ``POST /api/queue/position``
(issue #137) is the single-chunk fractional write the board's drag-and-drop and
``blizzard hub queue move`` both drive. ``POST /chunks/{id}/group`` is the Group
control. Controllers stay read-only over the store and delegate the writes to the
queue-shaping domain services (``bzh:controller-read-only``).

``dependencies=[Depends(reject_runner_principal)]`` rejects a runner's bearer token
here rather than treating it as anonymous-plus-credential.
"""

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
    """Idempotent whole-order replacement of the ready queue — the explicit operator
    verb behind ``blizzard hub queue set``, no longer any client's reorder path
    (``POST /api/queue/position`` is that path now).

    Resolves every named id against the current ready set here (the edge concern,
    ``bzh:domain-takes-objects``): ``409`` names the first id that is not a ready
    chunk, ``422`` rejects a duplicate id. A ready chunk not named keeps its current
    relative order, appended after the named ones, so the replacement is total and
    idempotent."""
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
    """Single-chunk fractional reorder — the board's drag-and-drop and
    ``blizzard hub queue move`` (issue #137).

    Resolves both ``chunk_id`` and ``after_chunk_id`` (if not null) against the current
    ready set here (the edge concern, ``bzh:domain-takes-objects``): ``409`` names
    either one if it is not a ready chunk, ``422`` rejects ``after_chunk_id`` naming
    ``chunk_id`` itself (self-anchor is nonsensical). ``after_chunk_id=null`` moves the
    chunk to the very top of the queue.
    """
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
    """Merge unacquired chunks into ``chunk_id`` — the board's Group control.

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
    # Each merged-away chunk vanished from the listings, and the survivor's pointers grew:
    # refresh the queue and the survivor's row on the board. The survivor's status comes
    # from the group result rather than a ``"ready"`` constant — grouping backlog chunks
    # leaves a backlog survivor, and a frame claiming otherwise would flip the board's row
    # to a status the store never derives (issue #141).
    survivor = result.survivor
    services.events.publish_queue_changed()
    chunk_events.publish_chunk_changed(
        services, survivor.chunk_id, cause="grouped", prev_status=prev_status, status=result.status.value
    )
    return ChunkGroupResponse(
        chunk_id=survivor.chunk_id,
        work_refs=[WorkRefModel(source=p.source, ref=p.ref) for p in survivor.work_refs],
        merged_chunk_ids=[m for m in request.merge_chunk_ids if m != survivor.chunk_id],
    )
