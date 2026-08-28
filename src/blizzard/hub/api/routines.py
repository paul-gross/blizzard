"""Routine routes — create, list, read, and edit a routine (issue #389).

The controller stays read-only over the store (``bzh:controller-read-only``), resolving
a ``routine_id`` into an object before delegating to the domain
(``bzh:domain-takes-objects``). ``reject_runner_principal`` confines a runner's bearer
token to the fleet router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import FLEET_VIEW, GRAPH_EDIT
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.routines import (
    Routine,
    RoutineGraphUnresolvedError,
    RoutineNameImmutableError,
    RoutineNameTakenError,
)
from blizzard.hub.domain.scopes import ScopeSlug, ScopeSlugError
from blizzard.wire.routine import RoutineCreateRequest, RoutineEditRequest, RoutineView

router = APIRouter(prefix="/api", tags=["routines"], dependencies=[Depends(reject_runner_principal)])


def _routine_view(routine: Routine) -> RoutineView:
    return RoutineView(
        routine_id=routine.routine_id,
        name=routine.name,
        graph_name=routine.graph_name,
        default_scope_slug=routine.default_scope_slug,
        default_model=list(routine.default_model),
        default_effort=routine.default_effort,
        created_at=iso_utc(routine.created_at),
    )


@router.post(
    "/routines",
    response_model=RoutineView,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(GRAPH_EDIT))],
)
def create_routine(
    request: RoutineCreateRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RoutineView:
    """Mint a routine; 422 on a duplicate name, a malformed default scope slug, or a
    graph name with no enabled mint."""
    try:
        slug = ScopeSlug.parse(request.default_scope_slug)
        routine = services.routine_authoring.create(
            name=request.name,
            graph_name=request.graph_name,
            default_scope_slug=slug,
            default_model=request.default_model,
            default_effort=request.default_effort,
        )
    except (ScopeSlugError, RoutineNameTakenError, RoutineGraphUnresolvedError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _routine_view(routine)


@router.get("/routines", response_model=list[RoutineView], dependencies=[Depends(require(FLEET_VIEW))])
def list_routines(services: Annotated[HubServices, Depends(get_services)]) -> list[RoutineView]:
    """Every routine, newest first."""
    return [_routine_view(r) for r in services.routines.list_all()]


@router.get("/routines/{routine_id}", response_model=RoutineView, dependencies=[Depends(require(FLEET_VIEW))])
def get_routine(routine_id: str, services: Annotated[HubServices, Depends(get_services)]) -> RoutineView:
    """One routine's whole record; 404 on an unknown id."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    return _routine_view(routine)


@router.patch("/routines/{routine_id}", response_model=RoutineView, dependencies=[Depends(require(GRAPH_EDIT))])
def edit_routine(
    routine_id: str, request: RoutineEditRequest, services: Annotated[HubServices, Depends(get_services)]
) -> RoutineView:
    """Change the graph, the default scope, and the model/effort defaults; 404 on an
    unknown id, 422 on a name change, a malformed default scope slug, or a graph name
    with no enabled mint."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    try:
        slug = ScopeSlug.parse(request.default_scope_slug)
        edited = services.routine_authoring.edit(
            routine,
            name=request.name,
            graph_name=request.graph_name,
            default_scope_slug=slug,
            default_model=request.default_model,
            default_effort=request.default_effort,
        )
    except (ScopeSlugError, RoutineNameImmutableError, RoutineGraphUnresolvedError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return _routine_view(edited)
