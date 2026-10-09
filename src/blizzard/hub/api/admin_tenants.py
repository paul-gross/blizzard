"""Tenant administration routes — hub-level, under ``TENANT_ADMIN``: create, list and rename the hub's tenants, and
reconcile the packaged graphs into every open tenant. A runner principal is refused; no route resolves a request
tenant, since each names the tenants it acts on itself."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.domain.tenancy.administration import Tenant, TenantAdminUnknown
from blizzard.hub.graph_sync import GraphReconciliation, GraphSyncStatus, reconcile_open_tenants
from blizzard.hub.tenancy import StoreScope, TenantId, UnknownTenant
from blizzard.wire.graph import GraphSyncEntry
from blizzard.wire.tenant import (
    AdminGraphSyncResponse,
    TenantCreateRequest,
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
    )


@router.post("/tenants", response_model=TenantView, status_code=status.HTTP_201_CREATED)
def create_tenant(body: TenantCreateRequest, actor: TenantAdmin, services: Hub) -> TenantView:
    """Create a tenant in one transaction, its packaged graphs minted and, when named, its first admin granted; it is
    usable the moment this answers. ``404`` when ``admin_user_id`` names no user — nothing is created."""
    try:
        tenant = services.tenant_admin.create(body.name, admin_user_id=body.admin_user_id, by=actor.user_id)
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
    """Rename an open tenant; ``404`` when no open tenant has the id."""
    try:
        return _view(services.tenant_admin.rename(TenantId(tenant_id), body.name))
    except UnknownTenant as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such tenant") from exc


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
