"""Tenant administration routes — hub-level, under ``TENANT_ADMIN``: create, list, rename and delete the hub's tenants,
and reconcile the packaged graphs into every open tenant. A runner principal is refused; no route resolves a request
tenant, since each names the tenants it acts on itself."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.api.door import RequestDoor
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.domain.tenancy.administration import Tenant, TenantAdminUnknown, TenantNameRefused
from blizzard.hub.domain.tenancy.teardown import DeletionState, LastTenant, TenantAlreadyDeleted
from blizzard.hub.graph_sync import GraphReconciliation, GraphSyncStatus, reconcile_open_tenants
from blizzard.hub.tenancy import StoreScope, TenantId, UnknownTenant
from blizzard.wire.graph import GraphSyncEntry
from blizzard.wire.tenant import (
    AdminGraphSyncResponse,
    TenantCreateRequest,
    TenantDeletionResponse,
    TenantGraphSync,
    TenantRenameRequest,
    TenantView,
)

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(reject_runner_principal)])

TenantAdmin = Annotated[ResolvedIdentity, Depends(require_hub(Permission.TENANT_ADMIN))]


def _view(tenant: Tenant) -> TenantView:
    return TenantView(
        tenant_id=tenant.tenant_id,
        name=tenant.name,
        created_at=iso_utc(tenant.created_at),
        created_by=tenant.created_by,
        state=tenant.state.value,
        closed_at=iso_utc(tenant.closed_at) if tenant.closed_at is not None else None,
    )


@router.post("/tenants", response_model=TenantView, status_code=status.HTTP_201_CREATED)
def create_tenant(body: TenantCreateRequest, actor: TenantAdmin, door: RequestDoor, services: Hub) -> TenantView:
    """Create a tenant in one transaction, its packaged graphs minted, its default project created through the
    request's door and, when named, its first admin granted; it is usable the moment this answers. ``404`` when
    ``admin_user_id`` names no user, ``422`` when the name is not a tenant name's shape — nothing is created."""
    try:
        tenant = services.tenant_admin.create(body.name, admin_user_id=body.admin_user_id, by=actor.user_id, door=door)
    except TenantNameRefused as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except TenantAdminUnknown as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such user") from exc
    return _view(tenant)


@router.get("/tenants", response_model=list[TenantView], dependencies=[Depends(require_hub(Permission.TENANT_ADMIN))])
def list_tenants(services: Hub) -> list[TenantView]:
    """Every tenant the hub holds, open or with its deletion underway, by id — creation order to the millisecond."""
    return [_view(tenant) for tenant in services.tenant_admin.list_tenants()]


@router.patch(
    "/tenants/{tenant_id}",
    response_model=TenantView,
    dependencies=[Depends(require_hub(Permission.TENANT_ADMIN))],
)
def rename_tenant(tenant_id: str, body: TenantRenameRequest, services: Hub) -> TenantView:
    """Rename an open tenant; ``404`` when no open tenant has the id, ``422`` when the name is not a tenant name's
    shape."""
    try:
        return _view(services.tenant_admin.rename(TenantId(tenant_id), body.name))
    except TenantNameRefused as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except UnknownTenant as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such tenant") from exc


@router.delete(
    "/tenants/{tenant_id}",
    response_model=TenantDeletionResponse,
    responses={
        status.HTTP_202_ACCEPTED: {"model": TenantDeletionResponse, "description": "The deletion is underway."},
        status.HTTP_404_NOT_FOUND: {"description": "No tenant has the id."},
        status.HTTP_409_CONFLICT: {"description": "The tenant is the hub's only open tenant."},
        status.HTTP_410_GONE: {"description": "The tenant was deleted."},
    },
)
def delete_tenant(tenant_id: str, actor: TenantAdmin, services: Hub, response: Response) -> TenantDeletionResponse:
    """Close a tenant and drain it inline under a row budget: ``200`` with the rows removed when the drain finishes
    inside it, else ``202`` while the ``tenant_teardown`` sweep carries on. A tenant whose deletion is underway answers
    ``202`` and is left as it is; one already deleted answers ``410``; the hub's only open tenant is refused ``409``."""
    tenant = TenantId(tenant_id)
    try:
        progress = services.tenant_teardown.delete(tenant, by=actor.user_id)
    except UnknownTenant as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such tenant") from exc
    except LastTenant as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "last_tenant", "message": "the hub's only open tenant cannot be deleted"},
        ) from exc
    except TenantAlreadyDeleted as exc:
        raise HTTPException(status.HTTP_410_GONE, detail="tenant deleted") from exc
    if progress.state is DeletionState.DELETING:
        response.status_code = status.HTTP_202_ACCEPTED
    return TenantDeletionResponse(tenant_id=tenant, state=progress.state.value, rows_removed=progress.rows_removed)


@router.post(
    "/graphs/sync",
    response_model=AdminGraphSyncResponse,
    dependencies=[Depends(require_hub(Permission.TENANT_ADMIN))],
)
def sync_every_tenant(services: Hub) -> AdminGraphSyncResponse:
    """Reconcile the packaged graph set into every open tenant, each through its own stores, and answer per tenant.
    Always ``200``: a graph that fails is a ``failed`` row, and each ``ok`` carries its verdict."""

    def reconciliation_of(tenant: TenantId) -> GraphReconciliation:
        opened = services.tenants.open_services(StoreScope(tenant))
        return GraphReconciliation(opened.graph_mint, opened.graphs)

    reports = reconcile_open_tenants(services.tenants.open_tenants.refresh(), reconciliation_of)
    tenants = [
        TenantGraphSync(
            tenant_id=report.tenant,
            name=report.name,
            ok=all(o.status is not GraphSyncStatus.FAILED for o in report.outcomes),
            entries=[
                GraphSyncEntry(name=o.name, status=o.status.value, graph_id=o.graph_id, detail=o.detail)
                for o in report.outcomes
            ],
        )
        for report in reports
    ]
    return AdminGraphSyncResponse(ok=all(t.ok for t in tenants), tenants=tenants)
