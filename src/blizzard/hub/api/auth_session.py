"""Who a request speaks for and which tenant it acts in — the edge seam every route's gate
depends on.

A presented session is hashed and resolved via the **read** repository; the sliding-expiry
write is delegated to the domain (``bzh:controller-read-only``). ``auth.mode = "none"`` (the
default) short-circuits a person to the implicit operator identity.

:func:`resolve_tenant` is the one place a request's tenant is resolved, by the first rule that
applies: a machine credential (a runner bearer token, a marker token) decides; else the
``X-Blizzard-Tenant`` header names it, checked against the caller's memberships; else the caller's
one membership settles it; else the request answers ``409 tenant_required``. Every people and fleet
router depends on it through :func:`tenant_bound`; :func:`require` checks a permission in the
resolved tenant and :func:`require_hub` one that is the hub's own.

A person names a tenant they hold no membership in and is answered ``404`` — except on a router
bound by :class:`TenantBoundAdmitting`, which admits a person holding its one hub-level permission
into any open tenant they name, acting there with that permission alone and no role."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.routing import APIRoute

from blizzard.auth_core import HUB_PERMISSIONS, Permission, Role, permissions_for
from blizzard.foundation.platform_tracing.attributes import Caller, annotate_caller, annotate_tenant
from blizzard.foundation.roles import collaborator
from blizzard.hub.api.auth import RunnerAuth, RunnerPrincipal
from blizzard.hub.api.bearer import presented_bearer
from blizzard.hub.api.hub_level import Hub, hub_services
from blizzard.hub.auth.hashing import SessionId
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices, TenantServices
from blizzard.hub.config import AUTH_MODE_NONE
from blizzard.hub.delivery.marker_auth import MarkerPrincipal
from blizzard.hub.tenancy import StoreScope, TenantClosed, TenantId, UnknownTenant

_SESSION_COOKIE_NAME = "bz_session"

#: The header a person names the tenant they act in with — the tenant's id, never its name.
TENANT_HEADER = "X-Blizzard-Tenant"

#: The header, documented once as an OpenAPI parameter component rather than on every operation.
TENANT_HEADER_COMPONENT = "TenantHeader"
TENANT_HEADER_PARAMETER: dict[str, object] = {
    "name": TENANT_HEADER,
    "in": "header",
    "required": False,
    "schema": {"type": "string"},
    "description": (
        "The id of the tenant the request acts in. A caller with one membership may omit it; a runner "
        "or marker token acts in its own tenant, and a header naming another is refused 400."
    ),
}

#: The header a hub command node's land script presents its marker token in.
MARKER_TOKEN_HEADER = "X-Blizzard-Marker-Token"

#: ASGI-scope key holding the identity the trace gate already resolved, so the route's own
#: dependency reuses it — one session lookup and one sliding-expiry touch per request.
_IDENTITY_SCOPE_KEY = "blizzard.resolved_identity"

#: The user id, username, and display name of the implicit operator.
OPERATOR = "operator"

#: One body for a tenant that does not exist and one the caller cannot enter, so the two are
#: indistinguishable.
_NO_SUCH_TENANT = "no such tenant"

#: Whoever a request's tenant was resolved for: a person, a runner, or a hub node's marker write.
Principal = ResolvedIdentity | RunnerPrincipal | MarkerPrincipal


def implicit_operator(tenant: TenantId | None) -> ResolvedIdentity:
    """The identity every request resolves to under ``auth.mode = "none"`` — the unauthenticated
    ``"operator"``: a hub administrator acting as an ``admin`` member of ``tenant``, or with no
    tenant role at all on a hub-level route (``tenant`` ``None``)."""
    role = None if tenant is None else Role.ADMIN
    return ResolvedIdentity(
        user_id=OPERATOR,
        username=OPERATOR,
        display_name=OPERATOR,
        tenant=tenant,
        role=role,
        permissions=permissions_for(role, True),
        hub_admin=True,
    )


@dataclass(frozen=True)
class PresentedSession:
    """The session credential one request carries: the ``HttpOnly`` cookie or an
    ``Authorization: Bearer`` header (the CLI path, #96) — the cookie wins."""

    request: Request

    @property
    def id_hash(self) -> str | None:  # ast-grep-ignore: bzh:property-delegates
        """The digest to look the session up by, or ``None`` when none was presented."""
        session_id = self.request.cookies.get(_SESSION_COOKIE_NAME) or presented_bearer(self.request)
        return None if session_id is None else SessionId(session_id).hash

    def caller(self) -> Caller:
        """Whom a resolved session speaks for: the board's cookie, else the operator's bearer."""
        return "board" if self.request.cookies.get(_SESSION_COOKIE_NAME) else "operator"


def resolve_identity(request: Request, services: HubServices | None) -> ResolvedIdentity | None:
    """Resolve the presented session to the person it belongs to — hub-level, holding no tenant's
    role — or ``None`` when no session is presented, it does not resolve, or it has expired.

    Under ``auth.mode = "none"`` this never touches ``services`` (``None`` is legal
    then) and always answers :func:`implicit_operator`."""
    mode = request.app.state.config.auth.mode
    if mode == AUTH_MODE_NONE:
        return implicit_operator(None)
    assert services is not None  # every oauth caller resolved services first
    if _IDENTITY_SCOPE_KEY not in request.scope:
        request.scope[_IDENTITY_SCOPE_KEY] = _resolve_session(request, services)
    return request.scope[_IDENTITY_SCOPE_KEY]


def _resolve_session(request: Request, services: HubServices) -> ResolvedIdentity | None:
    id_hash = PresentedSession(request).id_hash
    if id_hash is None:
        return None
    session = services.sessions.get_by_hash(id_hash)
    if session is None:
        return None
    return services.auth.touch_session(session, tenant=None)


def in_tenant(identity: ResolvedIdentity, tenant: TenantId, role: Role | None) -> ResolvedIdentity:
    """``identity`` acting in ``tenant`` under ``role``, its permissions expanded once."""
    return ResolvedIdentity(
        user_id=identity.user_id,
        username=identity.username,
        display_name=identity.display_name,
        tenant=tenant,
        role=role,
        permissions=permissions_for(role, identity.hub_admin),
        hub_admin=identity.hub_admin,
    )


@dataclass(frozen=True)
class TenantRequest:
    """One request's resolved tenant: whom it was resolved for (``identity``, whose tenant is
    ``scope.tenant``), the tenant's name, and the services opened for it."""

    identity: Principal
    scope: StoreScope
    name: str
    services: TenantServices


def resolve_tenant(request: Request, hub: Hub) -> TenantRequest:
    """The tenant ``request`` acts in, by the first rule that applies — a machine credential, the
    ``X-Blizzard-Tenant`` header, the caller's one membership — else ``409 tenant_required``.

    A header disagreeing with a credential answers ``400``; a tenant that does not exist and one the
    caller cannot enter both answer the same ``404``; a person with no membership at all answers
    ``404``. Under oauth a request with neither a credential nor a session answers ``401``."""
    named = _named_tenant(request)
    credential = _machine_credential(request, hub)
    if credential is not None:
        if named is not None and named != credential.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{TENANT_HEADER} names a tenant other than the presented credential's",
            )
        return _served(hub, credential.tenant_id, credential)
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        tenant = named if named is not None else hub.tenants.open_tenants.only()
        if tenant is None:
            raise _tenant_required()
        return _served(hub, tenant, implicit_operator(tenant))
    identity = resolve_identity(request, hub)
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
    if named is not None:
        role = hub.memberships.role_in(named, identity.user_id)
        if role is None:
            return _served(hub, named, _admitted(request, identity, named))
        return _served(hub, named, in_tenant(identity, named, role))
    memberships = hub.memberships.memberships_for(identity.user_id)
    if not memberships:
        raise _no_such_tenant()
    if len(memberships) > 1:
        raise _tenant_required()
    only = memberships[0]
    return _served(hub, only.tenant_id, in_tenant(identity, only.tenant_id, only.role))


async def tenant_bound(tenant: Annotated[TenantRequest, Depends(resolve_tenant)]) -> None:
    """A router dependency: resolve the request's tenant, and record it on every log line and span
    the request produces. Async, so the log binding lands in the request's own context rather than
    a worker thread's copy of it."""
    structlog.contextvars.bind_contextvars(tenant_id=tenant.scope.tenant)
    annotate_tenant(tenant.scope.tenant, tenant.name)


@collaborator
@dataclass(frozen=True)
class TenantBoundAdmitting:
    """A router dependency standing where :func:`tenant_bound` would: the request's tenant resolves
    as :func:`resolve_tenant` resolves it, except that a person naming a tenant they hold no
    membership in is admitted when they hold the hub-level ``permission`` — acting there with that
    one permission and no role. A tenant that does not exist or is closed, a person without
    ``permission``, and every other caller answer exactly as on a plain tenant router.

    :func:`resolve_tenant` finds it among the matched route's dependencies, so it admits on exactly
    the routes of the router that lists it; a route there gates with :func:`require_either`, naming
    ``permission`` as the alternative to its tenant permission."""

    permission: Permission

    def __post_init__(self) -> None:
        if self.permission not in HUB_PERMISSIONS:
            raise ValueError(f"{self.permission!r} is no hub-level permission; only one of those admits a non-member")

    async def __call__(self, tenant: Annotated[TenantRequest, Depends(resolve_tenant)]) -> None:
        await tenant_bound(tenant)


def _route_admission(request: Request) -> Permission | None:
    """The hub-level permission the matched route's router admits a non-member under
    (:class:`TenantBoundAdmitting`), or ``None`` on a plain router."""
    route = request.scope.get("route")
    if not isinstance(route, APIRoute):
        return None
    for dependency in route.dependant.dependencies:
        if isinstance(dependency.call, TenantBoundAdmitting):
            return dependency.call.permission
    return None


def _admitted(request: Request, identity: ResolvedIdentity, tenant: TenantId) -> ResolvedIdentity:
    """``identity``, a person with no membership in ``tenant``, acting there under the hub-level
    permission the request's router admits — when they hold it; anyone else answers the ``404`` an
    unknown tenant does."""
    admitted = _route_admission(request)
    if admitted is None or admitted not in identity.permissions:
        raise _no_such_tenant()
    return ResolvedIdentity(
        user_id=identity.user_id,
        username=identity.username,
        display_name=identity.display_name,
        tenant=tenant,
        role=None,
        permissions=frozenset({admitted}),
        hub_admin=identity.hub_admin,
    )


def _named_tenant(request: Request) -> TenantId | None:
    named = request.headers.get(TENANT_HEADER)
    return TenantId(named) if named else None


def _machine_credential(request: Request, hub: HubServices) -> RunnerPrincipal | MarkerPrincipal | None:
    """The runner bearer or marker token ``request`` carries, verified — in every auth mode."""
    runner = RunnerAuth.of(request, hub).principal
    if runner is not None:
        return runner
    token = request.headers.get(MARKER_TOKEN_HEADER)
    if not token:
        return None
    # Path and query params are read off the request because FastAPI resolves a route's
    # dependencies alongside its own param validation: a bad ``epoch`` verifies nothing.
    chunk_id = request.path_params.get("chunk_id")
    node_id = request.query_params.get("node_id")
    try:
        epoch = int(request.query_params.get("epoch", ""))
    except ValueError:
        return None
    if not isinstance(chunk_id, str) or node_id is None:
        return None
    return hub.marker_authority.verify(token, chunk_id=chunk_id, node_id=node_id, epoch=epoch)


def _served(hub: HubServices, tenant: TenantId, identity: Principal) -> TenantRequest:
    name = hub.tenants.open_tenants.name(tenant)
    if name is None:
        raise _no_such_tenant()
    scope = StoreScope(tenant)
    try:
        services = hub.tenants.open_services(scope)
    except (UnknownTenant, TenantClosed):
        raise _no_such_tenant() from None
    return TenantRequest(identity=identity, scope=scope, name=name, services=services)


def _no_such_tenant() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_SUCH_TENANT)


def _tenant_required() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "tenant_required", "message": f"name the tenant to act in with {TENANT_HEADER}"},
    )


def _caller_of(request: Request) -> Caller:
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        return "operator"
    return PresentedSession(request).caller()


def granted(request: Request, tenant: TenantRequest, permission: Permission) -> ResolvedIdentity:
    """The person ``tenant`` was resolved for, holding ``permission`` there — else ``403``, for a
    person without it and for a machine credential, which no permission gate admits."""
    identity = tenant.identity
    if not isinstance(identity, ResolvedIdentity):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="a machine credential is not valid here")
    annotate_caller(_caller_of(request))
    if permission not in identity.permissions:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"missing permission {permission!r}")
    return identity


def require(permission: Permission) -> Callable[..., ResolvedIdentity]:
    """A dependency factory: the returned dependency resolves the request's tenant
    (:func:`resolve_tenant`) and the person acting in it, then raises ``403`` when that person's
    membership there lacks ``permission``."""

    def _dependency(request: Request, tenant: Annotated[TenantRequest, Depends(resolve_tenant)]) -> ResolvedIdentity:
        return granted(request, tenant, permission)

    return _dependency


def require_either(permission: Permission, alternative: Permission) -> Callable[..., ResolvedIdentity]:
    """:func:`require` admitting ``alternative`` in place of ``permission`` — the gate of a route on a
    :class:`TenantBoundAdmitting` router, whose admitted person holds the hub-level ``alternative``
    and no tenant permission."""

    def _dependency(request: Request, tenant: Annotated[TenantRequest, Depends(resolve_tenant)]) -> ResolvedIdentity:
        if isinstance(tenant.identity, ResolvedIdentity) and alternative in tenant.identity.permissions:
            return granted(request, tenant, alternative)
        return granted(request, tenant, permission)

    return _dependency


def require_hub(permission: Permission) -> Callable[..., ResolvedIdentity]:
    """A dependency factory for a hub-level route, which serves no tenant: the returned dependency
    resolves the person — ``401`` without a session under oauth — and raises ``403`` unless they
    hold the hub-level ``permission``."""

    def _hub_dependency(request: Request) -> ResolvedIdentity:
        if request.app.state.config.auth.mode == AUTH_MODE_NONE:
            annotate_caller("operator")
            return implicit_operator(None)
        identity = resolve_identity(request, hub_services(request))
        if identity is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
        annotate_caller(PresentedSession(request).caller())
        if permission not in identity.permissions:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"missing permission {permission!r}")
        return identity

    return _hub_dependency
