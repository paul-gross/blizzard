"""The tenant services dependency for the people and fleet routers (``bzh:dependency-injection``).

A handler names the half it serves from: :data:`Services` for the legacy services of the tenant the
request admits (:func:`~blizzard.hub.api.auth_session.resolve_tenant`), opened over its scope, :data:`Hub`
(``src/blizzard/hub/api/hub_level.py``) for the hub-level ones."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from blizzard.hub.api.auth_session import AdmittedTenant, resolve_tenant
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.composition import TenantServices


def tenant_services(tenant: Annotated[AdmittedTenant, Depends(resolve_tenant)], hub: Hub) -> TenantServices:
    """A dependency: the services of the tenant the request admits. A tenant closed since its admission raises
    ``TenantClosed``, which is refused as any later use of a closed tenant is."""
    return hub.tenants.open_services(tenant.scope)


Services = Annotated[TenantServices, Depends(tenant_services)]
