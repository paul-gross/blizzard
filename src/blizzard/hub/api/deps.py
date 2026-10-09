"""Shared FastAPI dependencies for the fleet routers (``bzh:dependency-injection``).

The composition root stashes the wired hub-level
:class:`~blizzard.hub.composition.HubServices` on ``app.state.services``; routers reach
it, and the tenant services it opens, through these dependencies. A handler names the
half it serves from: :data:`Services` for the request's tenant's services, :data:`Hub`
for the hub-level ones (identity, sessions, signing, exports, sweeps). With no services
wired, a route reports the store is unwired (503) rather than serving on none."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, status
from fastapi.exceptions import HTTPException

from blizzard.hub.composition import HubServices, TenantServices


def get_services(request: Request) -> HubServices:
    services: HubServices | None = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="hub store not wired — start via `blizzard hub host`",
        )
    return services


def hub_services(request: Request) -> HubServices:
    """A dependency: the hub-level services, shared by every tenant."""
    return get_services(request)


def tenant_services(request: Request) -> TenantServices:
    """A dependency: the services of the tenant the request is served in — the store's only tenant."""
    return get_services(request).tenants.sole_tenant


Hub = Annotated[HubServices, Depends(hub_services)]
Services = Annotated[TenantServices, Depends(tenant_services)]
