"""Queue and backlog routes — read, replace, and group (issues #87, #104).

The ``ready`` queue and ``not_ready`` list each rank independently
(``bzh:ranking-is-per-list``); controllers stay read-only and delegate writes to the
queue-shaping domain (``bzh:controller-read-only``). Backlog routes require
``QUEUE_REORDER`` even to read — an operator triage surface, not fleet-wide visibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import FLEET_VIEW, QUEUE_REORDER
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.chunk_views import blocked_view
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.dependencies import derive_blocked_markings
from blizzard.hub.domain.queue import ChunkNotFound, ChunkNotGroupable, QueueList
from blizzard.hub.domain.work import Chunk
from blizzard.wire.chunk import WorkRefModel
from blizzard.wire.queue import (
    BacklogPeekEntry,
    BacklogPeekResponse,
    BacklogPositionRequest,
    BacklogReplaceRequest,
    ChunkGroupRequest,
    ChunkGroupResponse,
    QueuePeekEntry,
    QueuePeekResponse,
    QueuePositionRequest,
    QueueReplaceRequest,
)

router = APIRouter(prefix="/api", tags=["queue"], dependencies=[Depends(reject_runner_principal)])


def _refusal_detail(chunk_id: str, *, expected: QueueList, other_ids: set[str]) -> str:
    """The 409 detail for a chunk resolved against the wrong list — names both lists
    (``bzh:ranking-is-per-list``) rather than assuming the reader knows which one this
    route serves."""
    other = QueueList.NOT_READY if expected is QueueList.READY else QueueList.READY
    if chunk_id in other_ids:
        return f"chunk {chunk_id} is not in the {expected.value} list (it is {other.value})"
    return f"chunk {chunk_id} is not in the {expected.value} list"


def _other_list(list_: QueueList) -> QueueList:
    return QueueList.NOT_READY if list_ is QueueList.READY else QueueList.READY


def _blocked_markings(services: HubServices) -> dict[str, str]:
    """Every currently-blocked dependent's marking, from one bulk facts read and one bulk
    standing-edges read, joined here rather than inside a store (``bzh:dependency-inversion``).
    A second bulk facts pass beside the one the ordering read already drives internally
    (``ChunkRecordStore._listed_with_status``) — the reviewed plan's own D2 directs
    ``ReadyQueue.view``/``Backlog.view`` to "gain the two bulk reads they need to populate
    it", both still flat in fleet size regardless."""
    facts = services.chunks.facts.load_all_facts()
    statuses = {chunk_id: chunk_facts.status() for chunk_id, chunk_facts in facts.items()}
    return derive_blocked_markings(services.chunks.dependencies.list_standing_edges(), statuses)


def _refuse(chunk_id: str, *, expected: QueueList, services: HubServices) -> HTTPException:
    other_ids = {c.chunk_id for c in services.queue.ordered(_other_list(expected))}
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail=_refusal_detail(chunk_id, expected=expected, other_ids=other_ids)
    )


def _replace(list_: QueueList, chunk_ids: list[str], services: HubServices) -> list[Chunk]:
    """Resolve ``chunk_ids`` against ``list_``'s current order and replace it — the body
    ``PUT /api/queue`` and ``PUT /api/backlog`` share, differing only in which list they
    rank (``bzh:ranking-is-per-list``)."""
    if len(set(chunk_ids)) != len(chunk_ids):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="chunk_ids must not repeat")
    current = services.queue.ordered(list_)
    by_id = {chunk.chunk_id: chunk for chunk in current}
    for chunk_id in chunk_ids:
        if chunk_id not in by_id:
            raise _refuse(chunk_id, expected=list_, services=services)
    named_ids = set(chunk_ids)
    ordered = [by_id[chunk_id] for chunk_id in chunk_ids]
    ordered.extend(chunk for chunk in current if chunk.chunk_id not in named_ids)
    services.queue.replace_order(list_, ordered)
    services.events.publish_queue_changed()
    return ordered


def _reposition(list_: QueueList, chunk_id: str, after_chunk_id: str | None, services: HubServices) -> None:
    """Resolve ``chunk_id``/``after_chunk_id`` against ``list_``'s current order and
    reposition — the body ``POST /api/queue/position`` and ``POST /api/backlog/position``
    share, differing only in which list they rank (``bzh:ranking-is-per-list``)."""
    if after_chunk_id == chunk_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="after_chunk_id must not equal chunk_id"
        )
    by_id = {chunk.chunk_id: chunk for chunk in services.queue.ordered(list_)}
    chunk = by_id.get(chunk_id)
    if chunk is None:
        raise _refuse(chunk_id, expected=list_, services=services)
    after: Chunk | None = None
    if after_chunk_id is not None:
        after = by_id.get(after_chunk_id)
        if after is None:
            raise _refuse(after_chunk_id, expected=list_, services=services)
    services.queue.reposition(list_, chunk, after)
    services.events.publish_queue_changed()


@dataclass(frozen=True)
class ReadyQueue:
    """The hub-ordered ready queue as every peek renders it — position is the order itself.
    ``markings`` is resolved once in :meth:`of`, alongside ``chunks``, so :attr:`view` stays
    a pure projection over already-fetched state rather than an I/O-performing property."""

    chunks: list[Chunk]
    markings: dict[str, str]

    @classmethod
    def of(cls, services: HubServices) -> ReadyQueue:
        return cls(services.queue.ordered_ready(), _blocked_markings(services))

    @property
    def view(self) -> QueuePeekResponse:
        return QueuePeekResponse(
            entries=[
                QueuePeekEntry(
                    chunk_id=chunk.chunk_id,
                    graph_id=chunk.graph_id,
                    position=position,
                    work_refs=[WorkRefModel(source=p.source, ref=p.ref) for p in chunk.work_refs],
                    blocked=blocked_view(self.markings.get(chunk.chunk_id)),
                )
                for position, chunk in enumerate(self.chunks)
            ]
        )


@router.get("/queue", response_model=QueuePeekResponse, dependencies=[Depends(require(FLEET_VIEW))])
def get_queue(services: Annotated[HubServices, Depends(get_services)]) -> QueuePeekResponse:
    """The hub-ordered ready queue, read-only — honours reorder/replace + grouping."""
    return ReadyQueue.of(services).view


@router.put("/queue", response_model=QueuePeekResponse, dependencies=[Depends(require(QUEUE_REORDER))])
def replace_queue(
    request: QueueReplaceRequest, services: Annotated[HubServices, Depends(get_services)]
) -> QueuePeekResponse:
    """Idempotent whole-order replacement of the ready queue.

    Resolves every named id against the current ready set: ``409`` names the first id
    that is not ready, ``422`` a duplicate id. An unnamed ready chunk keeps its relative
    order, appended after the named ones."""
    _replace(QueueList.READY, request.chunk_ids, services)
    return ReadyQueue.of(services).view


@router.post("/queue/position", response_model=QueuePeekResponse, dependencies=[Depends(require(QUEUE_REORDER))])
def reposition_queue(
    request: QueuePositionRequest, services: Annotated[HubServices, Depends(get_services)]
) -> QueuePeekResponse:
    """Single-chunk fractional reorder (issue #137).

    Resolves both ids against the current ready set: ``409`` names either one if it is
    not ready, ``422`` rejects a self-anchor. ``after_chunk_id=null`` moves the chunk to
    the top of the queue."""
    _reposition(QueueList.READY, request.chunk_id, request.after_chunk_id, services)
    return ReadyQueue.of(services).view


@dataclass(frozen=True)
class Backlog:
    """The hub-ordered ``not_ready`` list as every peek renders it — position is the
    order itself. ``markings`` is resolved once in :meth:`of`, alongside ``chunks``, so
    :attr:`view` stays a pure projection over already-fetched state rather than an
    I/O-performing property."""

    chunks: list[Chunk]
    markings: dict[str, str]

    @classmethod
    def of(cls, services: HubServices) -> Backlog:
        return cls(services.queue.ordered_not_ready(), _blocked_markings(services))

    @property
    def view(self) -> BacklogPeekResponse:
        return BacklogPeekResponse(
            entries=[
                BacklogPeekEntry(
                    chunk_id=chunk.chunk_id,
                    graph_id=chunk.graph_id,
                    position=position,
                    work_refs=[WorkRefModel(source=p.source, ref=p.ref) for p in chunk.work_refs],
                    blocked=blocked_view(self.markings.get(chunk.chunk_id)),
                )
                for position, chunk in enumerate(self.chunks)
            ]
        )


@router.get("/backlog", response_model=BacklogPeekResponse, dependencies=[Depends(require(QUEUE_REORDER))])
def get_backlog(services: Annotated[HubServices, Depends(get_services)]) -> BacklogPeekResponse:
    """The hub-ordered ``not_ready`` list, read-only — an operator triage surface, so it
    requires ``QUEUE_REORDER`` rather than the ready queue's ``FLEET_VIEW``."""
    return Backlog.of(services).view


@router.put("/backlog", response_model=BacklogPeekResponse, dependencies=[Depends(require(QUEUE_REORDER))])
def replace_backlog(
    request: BacklogReplaceRequest, services: Annotated[HubServices, Depends(get_services)]
) -> BacklogPeekResponse:
    """Idempotent whole-order replacement of the backlog.

    Resolves every named id against the current ``not_ready`` set: ``409`` names the
    first id that is not ``not_ready``, ``422`` a duplicate id. An unnamed chunk keeps
    its relative order, appended after the named ones."""
    _replace(QueueList.NOT_READY, request.chunk_ids, services)
    return Backlog.of(services).view


@router.post("/backlog/position", response_model=BacklogPeekResponse, dependencies=[Depends(require(QUEUE_REORDER))])
def reposition_backlog(
    request: BacklogPositionRequest, services: Annotated[HubServices, Depends(get_services)]
) -> BacklogPeekResponse:
    """Single-chunk fractional reorder within the backlog.

    Resolves both ids against the current ``not_ready`` set: ``409`` names either one if
    it is not ``not_ready``, ``422`` rejects a self-anchor. ``after_chunk_id=null`` moves
    the chunk to the top of the backlog."""
    _reposition(QueueList.NOT_READY, request.chunk_id, request.after_chunk_id, services)
    return Backlog.of(services).view


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
    before = chunk_events.ChunkChanged.before(services, chunk_id)
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
    chunk_events.ChunkChanged.of(services, survivor.chunk_id, prev_status=before.prev_status).publish(
        cause="grouped", status=result.status.value, key=key
    )
    return ChunkGroupResponse(
        chunk_id=survivor.chunk_id,
        work_refs=[WorkRefModel(source=p.source, ref=p.ref) for p in survivor.work_refs],
        merged_chunk_ids=[m for m in request.merge_chunk_ids if m != survivor.chunk_id],
    )
