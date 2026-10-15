"""The tenant services dependency for the permit router (``bzh:dependency-injection``)."""

# exemplar: bzh:dependency-injection — a handler names :data:`Services` and never constructs what it serves from.

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from exemplar.hub.composition import TenantServices


def tenant_services(request: Request) -> TenantServices:
    """A dependency: the services of the request's tenant."""
    # exemplar: production returns ``resolve_tenant``'s ``tenant.services``; the stand-in bag sits on the app state.
    return request.app.state.tenant_services


Services = Annotated[TenantServices, Depends(tenant_services)]
