"""Routine routes — create, list, read, edit, and run a routine (issue #389, blizzard#392).

The controller stays read-only over the store (``bzh:controller-read-only``), resolving
a ``routine_id`` into an object before delegating to the domain
(``bzh:domain-takes-objects``). ``reject_runner_principal`` confines a runner's bearer
token to the fleet router."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import CHUNK_CONTROL, FLEET_VIEW, GRAPH_EDIT
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.ingest import IngestConflict
from blizzard.hub.domain.routine_run import RoutineNotFoundError, RunResult, ScopeRetiredError
from blizzard.hub.domain.routines import (
    Routine,
    RoutineGraphUnresolvedError,
    RoutineNameImmutableError,
    RoutineNameTakenError,
    RunMode,
)
from blizzard.hub.domain.scopes import ScopeSlug, ScopeSlugError
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.wire.chunk import ChunkIngestConflict
from blizzard.wire.routine import (
    RoutineCreateRequest,
    RoutineEditRequest,
    RoutineRunRequest,
    RoutineRunResponse,
    RoutineView,
)

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


def _run_response(result: RunResult) -> RoutineRunResponse:
    baseline = result.baseline
    return RoutineRunResponse(
        chunk_id=result.chunk_id,
        source=result.item.source,
        ref=result.item.ref,
        title=result.item.title,
        body=result.item.body,
        routine_name=result.item.routine_name or "",
        scope_slug=result.item.scope_slug or "",
        effective_mode=result.effective_mode.value,
        downgraded=result.downgraded,
        baseline_finding_set_id=baseline.finding_set_id if baseline is not None else None,
        baseline_revisions=dict(baseline.revisions) if baseline is not None else None,
        created_at=iso_utc(result.item.created_at),
    )


@router.post(
    "/routines/{routine_id}/run",
    response_model=RoutineRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def run_routine(
    routine_id: str,
    request: RoutineRunRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> object:
    """Mint, ingest, and promote a hub work item from the routine, in one act
    (blizzard#392). 404 on an unknown id; 422 on a malformed ``scope_slug``, an unknown
    ``mode``, or a graph name with no enabled mint; 409 on a retired effective scope or
    an out-of-band ingest already holding the allocated ref's pointer."""
    routine = services.routines.get(routine_id)
    if routine is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown routine {routine_id}")
    try:
        mode = RunMode(request.mode)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"unknown mode {request.mode!r}"
        ) from exc
    try:
        slug = ScopeSlug.parse(request.scope_slug) if request.scope_slug is not None else None
        result = services.routine_run.run(
            routine.name,
            scope_slug=slug,
            mode=mode,
            note=request.note,
            author=WorkItemAuthor.user(identity.user_id),
        )
    except (ScopeSlugError, RoutineGraphUnresolvedError) as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RoutineNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ScopeRetiredError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except IngestConflict as exc:
        conflict = ChunkIngestConflict(
            existing_chunk_id=exc.existing_chunk_id, source=exc.pointer.source, ref=exc.pointer.ref
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())
    # A freshly minted chunk is promoted in the same transaction, so its post-write
    # status already reads `ready` — one frame, not a mint then a separate promote.
    chunk_events.ChunkChanged.of(services, result.chunk_id, prev_status=None).publish(
        cause="minted", key=f"chunks:{result.chunk_id}"
    )
    services.events.publish_queue_changed()  # a promoted chunk enters the ready queue
    return _run_response(result)
