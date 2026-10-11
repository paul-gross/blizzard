"""The tenant services dependency for the people and fleet routers (``bzh:dependency-injection``).

A handler names the half it serves from: :data:`Services` for the services of the tenant the
request resolves (:func:`~blizzard.hub.api.auth_session.resolve_tenant`), :data:`Hub`
(``src/blizzard/hub/api/hub_level.py``) for the hub-level ones."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from blizzard.hub.api.auth_session import TenantRequest, resolve_tenant
from blizzard.hub.composition import TenantServices


def tenant_services(tenant: Annotated[TenantRequest, Depends(resolve_tenant)]) -> TenantServices:
    """A dependency: the services of the tenant the request resolves."""
    return tenant.services


Services = Annotated[TenantServices, Depends(tenant_services)]
