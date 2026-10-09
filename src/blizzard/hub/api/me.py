"""``GET /api/me`` — the resolved identity and its expanded permission set.

Public plane (no ``require(<permission>)``): a ``pending`` identity must reach this
route to discover it has no permissions. It is the one route that distinguishes
*anonymous* (no or expired session — 401) from *denied* (a resolved identity lacking
a permission — 403), resolving the identity directly."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from blizzard.auth_core import Permission, Role
from blizzard.hub.api.auth_session import TENANT_HEADER, implicit_operator, in_tenant, resolve_identity
from blizzard.hub.api.hub_level import hub_services
from blizzard.hub.auth.models import ResolvedIdentity, wire_role
from blizzard.hub.auth.service import ASSIGNABLE_ROLES
from blizzard.hub.composition import HubServices
from blizzard.hub.config import AUTH_MODE_NONE
from blizzard.hub.tenancy import TenantId

router = APIRouter(prefix="/api", tags=["auth"])


class MeResponse(BaseModel):
    """The resolved identity's wire view. ``role`` is the membership's role in the request's
    tenant — ``pending`` with none, ``superuser`` for a hub administrator."""

    user_id: str
    username: str
    display_name: str
    role: Role
    permissions: list[Permission]
    #: Every role the role-assignment API may grant.
    assignable_roles: list[Role] = []


@router.get("/me", response_model=MeResponse)
def me(request: Request) -> MeResponse:
    """Under ``auth.mode = "none"`` the implicit operator/superuser; under ``oauth``
    with a resolving session, the session's identity; under ``oauth`` with none, 401.
    ``role`` and ``permissions`` are those of the tenant the request names in
    ``X-Blizzard-Tenant``, else of the caller's one membership, else the hub-level ones."""
    mode = request.app.state.config.auth.mode
    services = None if mode == AUTH_MODE_NONE else hub_services(request)
    identity = resolve_identity(request, services)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    if mode == AUTH_MODE_NONE:
        identity = _operator_in_tenant(request)
    elif services is not None:
        identity = _person_in_tenant(request, services, identity)
    return MeResponse(
        user_id=identity.user_id,
        username=identity.username,
        display_name=identity.display_name,
        role=wire_role(identity.role, hub_admin=identity.hub_admin),
        permissions=sorted(identity.permissions),
        assignable_roles=list(ASSIGNABLE_ROLES),
    )


def _operator_in_tenant(request: Request) -> ResolvedIdentity:
    services: HubServices | None = getattr(request.app.state, "services", None)
    if services is None:
        return implicit_operator(None)
    open_tenants = services.tenants.open_tenants
    named = request.headers.get(TENANT_HEADER)
    tenant = TenantId(named) if named and open_tenants.name(TenantId(named)) is not None else open_tenants.only()
    return implicit_operator(tenant)


def _person_in_tenant(request: Request, services: HubServices, identity: ResolvedIdentity) -> ResolvedIdentity:
    named = request.headers.get(TENANT_HEADER)
    if named:
        tenant = TenantId(named)
        role = services.memberships.role_in(tenant, identity.user_id)
        return identity if role is None else in_tenant(identity, tenant, role)
    memberships = services.memberships.memberships_for(identity.user_id)
    if len(memberships) != 1:
        return identity
    return in_tenant(identity, memberships[0].tenant_id, memberships[0].role)
