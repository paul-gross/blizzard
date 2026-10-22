"""``GET /api/me`` — the resolved identity, every tenant it holds a membership in, and the
expanded permission set of the tenant the request resolves.

Public plane (no ``require(<permission>)``) and hub-level: a person with no membership must
reach this route to discover it, and a client reads it to choose a tenant before calling any
tenant route. It is the one route that distinguishes *anonymous* (no or expired session —
401) from *denied* (a resolved identity lacking a permission — 403), resolving the identity
directly."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from blizzard.auth_core import Permission, Role
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.hub.api.auth_session import TENANT_HEADER, implicit_operator, resolve_identity
from blizzard.hub.api.hub_level import hub_services
from blizzard.hub.auth.memberships import Membership
from blizzard.hub.auth.permissions import permissions_for
from blizzard.hub.auth.service import ASSIGNABLE_ROLES
from blizzard.hub.composition import HubServices
from blizzard.hub.config import AUTH_MODE_NONE
from blizzard.hub.tenancy import TenantId
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN

router = APIRouter(prefix="/api", tags=["auth"])


class MeTenant(BaseModel):
    """One tenant the caller holds a membership in: its id (the value an
    ``X-Blizzard-Tenant`` header names), its name, the membership's role, and every
    permission the caller holds there."""

    tenant_id: str
    name: str
    role: Annotated[Role | Unrecognized, OPEN]
    permissions: list[Annotated[Permission | Unrecognized, OPEN]]


class MeResponse(WireModel):
    """The resolved identity's wire view. ``role`` and ``permissions`` are those of the tenant
    the request resolves — the one ``X-Blizzard-Tenant`` names among ``tenants``, else the
    caller's only membership. With no such tenant ``role`` is null and ``permissions`` holds
    only the hub-level permissions of a hub administrator."""

    user_id: str
    username: str
    display_name: str
    role: Annotated[Role | Unrecognized, OPEN] | None
    permissions: list[Annotated[Permission | Unrecognized, OPEN]]
    #: Every role the role-assignment API may grant.
    assignable_roles: list[Annotated[Role | Unrecognized, OPEN]] = []
    #: Every open tenant the caller holds a membership in, ordered by id.
    tenants: list[MeTenant] = []
    #: Whether the caller is a hub administrator.
    hub_admin: bool = False


@router.get("/me", response_model=MeResponse)
def me(request: Request) -> MeResponse:
    """Under ``auth.mode = "none"`` the implicit operator, a member of every open tenant; under
    ``oauth`` with a resolving session, the session's person; under ``oauth`` with none, 401.
    A header naming a tenant the caller cannot enter resolves no tenant — never a 404 here."""
    mode = request.app.state.config.auth.mode
    services = None if mode == AUTH_MODE_NONE else hub_services(request)
    identity = resolve_identity(request, services)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    if mode == AUTH_MODE_NONE:
        tenants = _operator_tenants(request)
        only = tenants[0] if len(tenants) == 1 else None
    else:
        assert services is not None  # oauth resolved services above
        memberships = services.memberships.memberships_for(identity.user_id)
        tenants = _open(services, memberships, hub_admin=identity.hub_admin)
        # A headerless tenant route settles on the one membership in force, a closed tenant's counted.
        only = tenants[0] if len(memberships) == 1 and len(tenants) == 1 else None
    named = request.headers.get(TENANT_HEADER)
    resolved = next((t for t in tenants if t.tenant_id == named), None) if named else only
    role = None if resolved is None else Role(resolved.role)
    return MeResponse(
        user_id=identity.user_id,
        username=identity.username,
        display_name=identity.display_name,
        role=role,
        permissions=[*sorted(permissions_for(role, identity.hub_admin))],
        assignable_roles=[*ASSIGNABLE_ROLES],
        tenants=tenants,
        hub_admin=identity.hub_admin,
    )


def _tenant(tenant: TenantId, name: str, role: Role, *, hub_admin: bool) -> MeTenant:
    return MeTenant(tenant_id=tenant, name=name, role=role, permissions=[*sorted(permissions_for(role, hub_admin))])


def _operator_tenants(request: Request) -> list[MeTenant]:
    """The implicit operator's: an ``admin`` membership in every open tenant."""
    services: HubServices | None = getattr(request.app.state, "services", None)
    if services is None:
        return []
    operator = implicit_operator(None)
    every = services.tenants.open_tenants.every()
    return [_tenant(t, every[t], Role.ADMIN, hub_admin=operator.hub_admin) for t in sorted(every)]


def _open(services: HubServices, memberships: list[Membership], *, hub_admin: bool) -> list[MeTenant]:
    """``memberships``, each in a tenant that is still open."""
    open_tenants = services.tenants.open_tenants
    return [
        _tenant(m.tenant_id, name, m.role, hub_admin=hub_admin)
        for m in memberships
        if (name := open_tenants.name(m.tenant_id)) is not None
    ]
