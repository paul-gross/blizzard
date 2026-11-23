"""``GET /api/me`` — the resolved identity, every tenant it holds a membership in, and the
expanded permission set of the tenant the request resolves.

Public plane (no ``require(<permission>)``) and hub-level: a person with no membership must
reach this route to discover it, and a client reads it to choose a tenant before calling any
tenant route. It is the one route that distinguishes *anonymous* (no or expired session —
401) from *denied* (a resolved identity lacking a permission — 403), resolving the identity
directly."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER, implicit_operator, resolve_identity
from blizzard.hub.api.hub_level import hub_services
from blizzard.hub.api.problem_rendering import problem_response
from blizzard.hub.api.problems import authentication_required_problem
from blizzard.hub.auth.memberships import Membership
from blizzard.hub.auth.permissions import permissions_for
from blizzard.hub.auth.service import ASSIGNABLE_ROLES
from blizzard.hub.composition import HubServices
from blizzard.hub.config import AUTH_MODE_NONE
from blizzard.hub.tenancy import TenantId
from blizzard.wire.me import MeResponse, MeTenant

router = APIRouter(prefix="/api", tags=["auth"])


@router.get("/me", response_model=MeResponse)
def me(request: Request) -> MeResponse | JSONResponse:
    """Under ``auth.mode = "none"`` the implicit operator, a member of every open tenant; under
    ``oauth`` with a resolving session, the session's person; under ``oauth`` with none, 401.
    A header naming a tenant the caller cannot enter resolves no tenant — never a 404 here."""
    mode = request.app.state.config.auth.mode
    services = None if mode == AUTH_MODE_NONE else hub_services(request)
    identity = resolve_identity(request)
    if identity is None:
        return problem_response(
            authentication_required_problem(instance=request.url.path, detail="not authenticated"),
        )
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
