"""Chunk-dependency routes — declare and release a dependency edge between two chunks,
the operator's own control-plane surface. Release addresses the standing
edge by its ordered pair, never a minted edge id — deliberately no GET or listing route
here, which stays #457's to add."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.chunk.dependencies import (
    DependencyWouldCloseCycle,
    DependentNotEditable,
    NoStandingDependencyToRelease,
    PrerequisiteIsEphemeral,
)
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, DependencyEdge
from blizzard.wire.chunk import (
    ChunkDependencyDeclareRequest,
    ChunkDependencyEdgeView,
    ChunkDependencyReleaseRequest,
    DependencyWouldCloseCycleView,
    DependentNotEditableView,
    NoStandingDependencyView,
    PrerequisiteIsEphemeralView,
)

router = APIRouter(prefix="/api", tags=["chunk-dependencies"], dependencies=[Depends(reject_runner_principal)])


def _resolve_dependent(services: HubServices, chunk_id: str) -> Chunk:
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    return chunk


def _resolve_prerequisite(services: HubServices, chunk_id: str) -> Chunk | str:
    """Resolve the named prerequisite to its chunk, or — for an id minted but since gone ephemeral
    (grouped-away or deleted), which has no chunk to load — to the id itself; 404 for an id never
    minted. Whether an ephemeral prerequisite is refused is ``DependencyService``'s to decide, under
    the lock."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is not None:
        return chunk
    if services.chunks.lifecycle.is_ephemeral(chunk_id):
        return chunk_id
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")


def _resolve_standing_edge(services: HubServices, dependent: Chunk, prerequisite_chunk_id: str) -> DependencyEdge:
    """Resolve the ordered pair to the standing edge a release operates on
    (``bzh:domain-takes-objects``). Nothing about the prerequisite is read: an edge whose
    prerequisite was since deleted still releases, which is the lever that keeps blocked a
    held state rather than a dead end."""
    edge = services.chunks.dependencies.standing_edge(dependent.chunk_id, prerequisite_chunk_id)
    if edge is None:
        raise NoStandingDependencyToRelease(dependent.chunk_id, prerequisite_chunk_id)
    return edge


def _edge_view(edge: DependencyEdge) -> ChunkDependencyEdgeView:
    return ChunkDependencyEdgeView(
        dependency_id=edge.dependency_id,
        dependent_chunk_id=edge.dependent_chunk_id,
        prerequisite_chunk_id=edge.prerequisite_chunk_id,
        declared_at=iso_utc(edge.declared_at),
        declared_by=edge.declared_by,
        released_at=iso_utc(edge.released_at) if edge.released_at is not None else None,
        released_by=edge.released_by,
    )


@router.post(
    "/chunks/{chunk_id}/dependencies",
    response_model=ChunkDependencyEdgeView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def declare_dependency(
    chunk_id: str,
    request: ChunkDependencyDeclareRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> object:
    """Declare that CHUNK depends on ``prerequisite_chunk_id``.

    Idempotent: an already-standing pair is reported back, even past the dependent's window or once the
    prerequisite goes ephemeral. 404 for an unknown or concurrently deleted dependent or prerequisite;
    409 for a dependent past its window, a cycle the edge would close, or an ephemeral prerequisite."""
    dependent = _resolve_dependent(services, chunk_id)
    prerequisite = _resolve_prerequisite(services, request.prerequisite_chunk_id)
    try:
        edge = services.dependencies.declare(dependent, prerequisite, by=request.by)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except DependentNotEditable as exc:
        view = DependentNotEditableView(chunk_id=exc.chunk_id, status=exc.status)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=view.model_dump())
    except DependencyWouldCloseCycle as exc:
        view = DependencyWouldCloseCycleView(
            dependent_chunk_id=exc.dependent_chunk_id, prerequisite_chunk_id=exc.prerequisite_chunk_id
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=view.model_dump())
    except PrerequisiteIsEphemeral as exc:
        view = PrerequisiteIsEphemeralView(chunk_id=exc.chunk_id)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=view.model_dump())
    return _edge_view(edge)


@router.post(
    "/chunks/{chunk_id}/dependencies/release",
    response_model=ChunkDependencyEdgeView,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def release_dependency(
    chunk_id: str,
    request: ChunkDependencyReleaseRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> object:
    """Release CHUNK's standing dependency on ``prerequisite_chunk_id``
    — recorded, never deleted. Admitted whenever the edge stands, whatever the
    prerequisite's own state. 404 for an unknown dependent; 409 when no edge stands."""
    dependent = _resolve_dependent(services, chunk_id)
    try:
        edge = _resolve_standing_edge(services, dependent, request.prerequisite_chunk_id)
        released = services.dependencies.release(edge, by=request.by)
    except NoStandingDependencyToRelease as exc:
        view = NoStandingDependencyView(
            dependent_chunk_id=exc.dependent_chunk_id, prerequisite_chunk_id=exc.prerequisite_chunk_id
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=view.model_dump())
    return _edge_view(released)
