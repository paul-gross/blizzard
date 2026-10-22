"""Permit routes — request a project's permit, move it through its verbs, and read it.

Every route acts in the request's tenant; reading takes ``FLEET_VIEW`` there and every verb ``CONFIG_EDIT``."""

# exemplar: bzh:domain-orchestration-split, bzh:controller-read-only — resolve, call the service, map each refusal.

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.project_label_fields import ProjectLabelFields
from blizzard.hub.api.project_labels import ProjectLabels
from blizzard.hub.api.project_params import QueryProject, body_project, project_of_call
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.domain.kernel.pagination import DEFAULT_LIMIT, MAX_LIMIT, MalformedCursor
from exemplar.hub.api.deps import Services
from exemplar.hub.api.permit_params import PathPermit
from exemplar.hub.api.refusals import conflict, unprocessable
from exemplar.hub.composition import TenantServices
from exemplar.hub.domain.permits.model import (
    Permit,
    PermitAlreadyRequested,
    PermitFactKind,
    PermitVerbRefused,
    RevocationNoteRequired,
)
from exemplar.wire.permit import PermitCreateRequest, PermitListResponse, PermitRevokeRequest, PermitView

router = APIRouter(
    prefix="/api", tags=["permits"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)

Editor = Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))]


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


def _labelled(services: TenantServices, permit: Permit) -> PermitView:
    return permit_view(permit, ProjectLabels.read(services, [permit.project_id]).fields(permit.project_id))


@router.post("/permits", response_model=PermitView, status_code=status.HTTP_201_CREATED)
def request_permit(request: PermitCreateRequest, services: Services, identity: Editor) -> PermitView:
    """Request a permit for ``holder`` in the project named, or the tenant's only one: 201, 409 when the project already
    holds one for the holder and capability, 422 on a project the tenant does not hold or one left open
    (``project_required``)."""
    project = body_project(request.project, services) if request.project is not None else project_of_call(services)
    try:
        permit = services.permit_service.request(
            project=project, holder=request.holder, capability=request.capability, by=identity.user_id
        )
    except PermitAlreadyRequested as exc:
        raise conflict(str(exc)) from exc
    return permit_view(permit, ProjectLabels.of_project(project).fields(permit.project_id))


@router.get("/permits", response_model=PermitListResponse, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def list_permits(
    services: Services,
    project: QueryProject,
    holder: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> PermitListResponse:
    """One page of permits, newest requested first, narrowed by ``?project=`` and ``?holder=``: 200, 422 on a project
    the tenant does not hold or a malformed cursor."""
    try:
        page = services.permits.list_page(project=project, holder=holder, limit=limit, cursor=cursor)
    except MalformedCursor as exc:
        raise unprocessable("malformed cursor") from exc
    labels = ProjectLabels.read(services, (p.project_id for p in page.permits))
    return PermitListResponse(
        permits=[permit_view(p, labels.fields(p.project_id)) for p in page.permits], next_cursor=page.next_cursor
    )


@router.get("/permits/{permit_id}", response_model=PermitView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def get_permit(permit: PathPermit, services: Services) -> PermitView:
    """One permit: 200, or 404 for one the tenant does not hold."""
    return _labelled(services, permit)


@router.post("/permits/{permit_id}/grant", response_model=PermitView)
def grant_permit(permit: PathPermit, services: Services, identity: Editor) -> PermitView:
    """Grant a requested permit: 200, 404 for one the tenant does not hold, 409 for one not requested."""
    try:
        return _labelled(services, services.permit_service.grant(permit, by=identity.user_id))
    except PermitVerbRefused as exc:
        raise conflict(str(exc)) from exc


@router.post("/permits/{permit_id}/suspend", response_model=PermitView)
def suspend_permit(permit: PathPermit, services: Services, identity: Editor) -> PermitView:
    """Suspend a granted permit: 200, 404 for one the tenant does not hold, 409 for one not granted."""
    try:
        return _labelled(services, services.permit_service.suspend(permit, by=identity.user_id))
    except PermitVerbRefused as exc:
        raise conflict(str(exc)) from exc


@router.post("/permits/{permit_id}/resume", response_model=PermitView)
def resume_permit(permit: PathPermit, services: Services, identity: Editor) -> PermitView:
    """Resume a suspended permit: 200, 404 for one the tenant does not hold, 409 for one not suspended."""
    try:
        return _labelled(services, services.permit_service.resume(permit, by=identity.user_id))
    except PermitVerbRefused as exc:
        raise conflict(str(exc)) from exc


@router.post("/permits/{permit_id}/revoke", response_model=PermitView)
def revoke_permit(permit: PathPermit, request: PermitRevokeRequest, services: Services, identity: Editor) -> PermitView:
    """Revoke a live permit: 200, 404 for one the tenant does not hold, 409 for one already revoked, 422 for a blank
    note."""
    try:
        return _labelled(services, services.permit_service.revoke(permit, note=request.note, by=identity.user_id))
    except PermitVerbRefused as exc:
        raise conflict(str(exc)) from exc
    except RevocationNoteRequired as exc:
        raise unprocessable(str(exc)) from exc
