"""Permit routes — request a project's permit, move it through its verbs, and read it.

Every route acts in the request's tenant; reading takes ``FLEET_VIEW`` there and every verb ``CONFIG_EDIT``."""

# exemplar: bzh:domain-orchestration-split, bzh:controller-read-only — resolve, call the service, map each refusal.
# exemplar: bzh:dependency-injection — a route names each service it calls through ``FromDishka`` and builds none.

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import JSONResponse

from blizzard.foundation.instants import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.project_label_fields import ProjectLabelFields
from blizzard.hub.api.project_labels import ProjectLabels
from blizzard.hub.api.project_params import QueryProject, body_project, project_of_call
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.domain.kernel.pagination import DEFAULT_LIMIT, MAX_LIMIT, MalformedCursor
from blizzard.hub.domain.projects.model import IReadProjectRepository
from exemplar.hub.api.permit_params import PathPermit
from exemplar.hub.api.refusals import already_requested, conflict, unprocessable
from exemplar.hub.domain.permits.holders import PermitHolderReader
from exemplar.hub.domain.permits.model import (
    IReadPermitRepository,
    Permit,
    PermitAlreadyRequested,
    PermitChanged,
    PermitFactKind,
    PermitVerbRefused,
    RevocationNoteRequired,
)
from exemplar.hub.domain.permits.service import PermitService
from exemplar.wire.permit import (
    PermitAlreadyRequestedProblem,
    PermitCreateRequest,
    PermitListResponse,
    PermitRevokeRequest,
    PermitView,
)

router = APIRouter(
    prefix="/api", tags=["permits"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)

Editor = Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))]
#: The tenant's permit reads, verbs, holder reads and project reads, each resolved through the request's scope.
Permits = FromDishka[IReadPermitRepository]
Verbs = FromDishka[PermitService]
Holders = FromDishka[PermitHolderReader]
Projects = FromDishka[IReadProjectRepository]


def permit_view(permit: Permit, labels: ProjectLabelFields) -> PermitView:
    latest = permit.latest
    return PermitView(
        **labels,
        permit_id=permit.permit_id,
        holder=permit.holder,
        capability=permit.capability,
        state=permit.state(),
        verbs=[*permit.legal_verbs()],
        requested_at=iso_utc(permit.requested_at),
        requested_by=permit.requested_by,
        changed_at=iso_utc(latest.at) if latest is not None else None,
        changed_by=latest.by if latest is not None else None,
        revocation_note=latest.note if latest is not None and latest.kind is PermitFactKind.REVOKED else None,
    )


def _labelled(projects: IReadProjectRepository, permit: Permit) -> PermitView:
    return permit_view(permit, ProjectLabels.read(projects, [permit.project_id]).fields(permit.project_id))


def _listed(projects: IReadProjectRepository, permits: Sequence[Permit], next_cursor: str | None) -> PermitListResponse:
    labels = ProjectLabels.read(projects, (p.project_id for p in permits))
    return PermitListResponse(
        permits=[permit_view(p, labels.fields(p.project_id)) for p in permits], next_cursor=next_cursor
    )


@router.post(
    "/permits",
    response_model=PermitView,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {"model": PermitAlreadyRequestedProblem, "content": {"application/problem+json": {}}}
    },
)
@inject_sync
def request_permit(
    request: PermitCreateRequest, verbs: Verbs, projects: Projects, identity: Editor, http: Request
) -> PermitView | JSONResponse:
    """Request a permit for ``holder`` in the project named, or the tenant's only one: 201, 409 when the project already
    holds one for the holder and capability, 422 on a project the tenant does not hold or one left open
    (``project_required``)."""
    project = body_project(request.project, projects) if request.project is not None else project_of_call(projects)
    try:
        permit = verbs.request(
            project=project, holder=request.holder, capability=request.capability, by=identity.user_id
        )
    except PermitAlreadyRequested as exc:
        return already_requested(exc, http)
    return permit_view(permit, ProjectLabels.of_project(project).fields(permit.project_id))


@router.get("/permits", response_model=PermitListResponse, dependencies=[Depends(require(Permission.FLEET_VIEW))])
@inject_sync
def list_permits(
    permits: Permits,
    projects: Projects,
    project: QueryProject,
    holder: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> PermitListResponse:
    """One page of permits, newest requested first, narrowed by ``?project=`` and ``?holder=``: 200, 422 on a project
    the tenant does not hold or a malformed cursor."""
    try:
        page = permits.list_page(project=project, holder=holder, limit=limit, cursor=cursor)
    except MalformedCursor as exc:
        raise unprocessable("malformed cursor") from exc
    return _listed(projects, page.permits, page.next_cursor)


@router.get(
    "/holders/{holder}/permits",
    response_model=PermitListResponse,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
@inject_sync
def list_live_permits(holder: str, holders: Holders, projects: Projects) -> PermitListResponse:
    """Every permit ``holder`` holds that is not yet revoked, newest requested first, on one page: 200."""
    return _listed(projects, holders.live(holder), None)


@router.get("/permits/{permit_id}", response_model=PermitView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
@inject_sync
def get_permit(permit: PathPermit, projects: Projects) -> PermitView:
    """One permit: 200, or 404 for one the tenant does not hold."""
    return _labelled(projects, permit)


@router.post("/permits/{permit_id}/grant", response_model=PermitView)
@inject_sync
def grant_permit(permit: PathPermit, verbs: Verbs, projects: Projects, identity: Editor) -> PermitView:
    """Grant a requested permit: 200, 404 for one the tenant does not hold, 409 for one not requested or changed since
    it was read."""
    try:
        return _labelled(projects, verbs.grant(permit, by=identity.user_id))
    except (PermitVerbRefused, PermitChanged) as exc:
        raise conflict(str(exc)) from exc


@router.post("/permits/{permit_id}/suspend", response_model=PermitView)
@inject_sync
def suspend_permit(permit: PathPermit, verbs: Verbs, projects: Projects, identity: Editor) -> PermitView:
    """Suspend a granted permit: 200, 404 for one the tenant does not hold, 409 for one not granted or changed since
    it was read."""
    try:
        return _labelled(projects, verbs.suspend(permit, by=identity.user_id))
    except (PermitVerbRefused, PermitChanged) as exc:
        raise conflict(str(exc)) from exc


@router.post("/permits/{permit_id}/resume", response_model=PermitView)
@inject_sync
def resume_permit(permit: PathPermit, verbs: Verbs, projects: Projects, identity: Editor) -> PermitView:
    """Resume a suspended permit: 200, 404 for one the tenant does not hold, 409 for one not suspended or changed since
    it was read."""
    try:
        return _labelled(projects, verbs.resume(permit, by=identity.user_id))
    except (PermitVerbRefused, PermitChanged) as exc:
        raise conflict(str(exc)) from exc


@router.post("/permits/{permit_id}/revoke", response_model=PermitView)
@inject_sync
def revoke_permit(
    permit: PathPermit, request: PermitRevokeRequest, verbs: Verbs, projects: Projects, identity: Editor
) -> PermitView:
    """Revoke a live permit: 200, 404 for one the tenant does not hold, 409 for one already revoked or changed since it
    was read, 422 for a blank note."""
    try:
        return _labelled(projects, verbs.revoke(permit, note=request.note, by=identity.user_id))
    except (PermitVerbRefused, PermitChanged) as exc:
        raise conflict(str(exc)) from exc
    except RevocationNoteRequired as exc:
        raise unprocessable(str(exc)) from exc
