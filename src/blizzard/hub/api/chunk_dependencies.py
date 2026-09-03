"""Chunk-dependency routes — declare and release a dependency edge between two chunks
(issue #456), the operator's own control-plane surface. Release addresses the standing
edge by its ordered pair, never a minted edge id — deliberately no GET or listing route
here, which stays #457's to add."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import CHUNK_CONTROL
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.dependencies import (
    DependencyWouldCloseCycle,
    DependentNotEditable,
    NoStandingDependencyToRelease,
    PrerequisiteIsEphemeral,
)
from blizzard.hub.domain.errors import ChunkNotFound
from blizzard.hub.domain.work import Chunk, DependencyEdge
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


def _resolve_prerequisite(services: HubServices, chunk_id: str) -> Chunk:
    """Resolve the named prerequisite, telling an ephemeral id (grouped-away or deleted)
    from one never minted (issue #456) via the further ``is_ephemeral`` read. Raises
    :class:`PrerequisiteIsEphemeral` for the former, 404 for the latter — an early-out
    only; ``DependencyService`` re-derives the same fact, and is the sole guard, under the lock."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is not None:
        return chunk
    if services.chunks.lifecycle.is_ephemeral(chunk_id):
        raise PrerequisiteIsEphemeral(chunk_id)
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
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def declare_dependency(
    chunk_id: str,
    request: ChunkDependencyDeclareRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> object:
    """Declare that CHUNK depends on ``prerequisite_chunk_id`` (issue #456).

    Idempotent: an already-standing pair is reported back before the prerequisite is even resolved, so one since gone
    ephemeral cannot turn a refusal. 404 for an unknown dependent, or one a race deletes between resolving it and this
    write; 409 for a dependent past its window, a cycle the edge would close, or an ephemeral prerequisite."""
    dependent = _resolve_dependent(services, chunk_id)
    existing = services.chunks.dependencies.standing_edge(dependent.chunk_id, request.prerequisite_chunk_id)
    if existing is not None:
        # Outside the shared claim lock: a concurrent release can land between this read
        # and the response, so this may report an edge just released (benign staleness).
        return _edge_view(existing)
    try:
        prerequisite = _resolve_prerequisite(services, request.prerequisite_chunk_id)
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
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def release_dependency(
    chunk_id: str,
    request: ChunkDependencyReleaseRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> object:
    """Release CHUNK's standing dependency on ``prerequisite_chunk_id`` (issue
    #456) — recorded, never deleted. Admitted whenever the edge stands, whatever the
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
