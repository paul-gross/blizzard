"""The exhaustive three-plane route classification guard.

Every mounted route is asserted human (gated by ``require(<permission>)``, or by
``require_hub(<permission>)`` when it serves no tenant), fleet (mounted under ``/api/fleet/*``,
gated at router level), or public (no gate at all). Human tenant routes and fleet routes resolve
their tenant at router level; hub-level and public routes resolve none. The table is exhaustive
both ways: every live route must appear, every entry must resolve. A tenant route that admits a
hub-level permission without a membership is named, with that permission, in :data:`_ADMITTING`.
"""

from __future__ import annotations

from pathlib import Path
from types import FunctionType

from fastapi import APIRouter, Depends, FastAPI
from fastapi.routing import APIRoute

from blizzard.hub.api.auth_session import TenantBoundAdmitting, require_either
from blizzard.hub.auth.permissions import Permission
from tests.route_classes import FLEET, FLEET_CLAIM, HUB_LEVEL, HUMAN, api_routes, dependency_names, live_routes
from tests.support import build_hub

# --- The three-plane table --------------------------------------------------

#: Public plane — no permission gate; reachable with no session at all.
_PUBLIC: set[tuple[str, str]] = {
    ("GET", "/api/health"),
    ("GET", "/api/flags"),
    ("GET", "/api/ready"),
    ("GET", "/api/me"),
    ("GET", "/api/auth/providers"),
    ("GET", "/api/auth/{name}/authorize"),
    ("GET", "/api/auth/{name}/callback"),
    ("POST", "/api/auth/logout"),
    # The hub-as-IdP surface — `authorize` authenticates the browser
    # itself rather than being gated by one; `jwks.json` is public key material.
    ("GET", "/api/auth/authorize"),
    ("GET", "/api/auth/jwks.json"),
    # The CLI's PKCE code exchange — there is no session yet at this
    # point, that is what this route mints.
    ("POST", "/api/auth/cli/token"),
    # The runner identity route — under `/api/fleet` but outside the fleet gate: it
    # reads the bearer token itself, so it can answer why the hub refuses one.
    ("GET", "/api/fleet/identity"),
}


#: The tenant routes whose router admits a person holding the named hub-level permission into a tenant they
#: hold no membership in (``TenantBoundAdmitting``) — each gated by ``require_either``, the admitted
#: permission its alternative. Every other tenant route answers a non-member ``404``.
_ADMITTING: dict[tuple[str, str], Permission] = {
    # A hub administrator grants and revokes a membership in any tenant; listing members takes a membership.
    ("PUT", "/api/members/{user_id}"): Permission.MEMBERSHIP_GRANT_ANY,
    ("DELETE", "/api/members/{user_id}"): Permission.MEMBERSHIP_GRANT_ANY,
    # The hub administrator gives a tenant with no admin yet its first one by invitation.
    ("POST", "/api/invitations"): Permission.MEMBERSHIP_GRANT_ANY,
    ("GET", "/api/invitations"): Permission.MEMBERSHIP_GRANT_ANY,
    ("POST", "/api/invitations/{invitation_id}/revocations"): Permission.MEMBERSHIP_GRANT_ANY,
}


#: Host plane — the five ``/api/fleet/host*`` routes a host's bearer token reaches, and nothing else. Mounted on a
#: router of their own, because the fleet router's runner-principal gate cannot be lifted per route.
_HOST: set[tuple[str, str]] = {
    ("POST", "/api/fleet/host"),
    ("GET", "/api/fleet/host"),
    ("POST", "/api/fleet/host/runners"),
    ("POST", "/api/fleet/host/runners/{runner_id}/retire"),
    ("POST", "/api/fleet/host/events"),
}


def _permission_of(call: object) -> Permission | None:
    """The exact :class:`Permission` a ``require(<permission>)`` dependency closes
    over, or ``None`` if ``call`` is not one — introspects the closure cell rather
    than trusting a second hand-maintained map, so this cannot silently drift from
    what the route actually enforces."""
    if isinstance(call, FunctionType) and call.__name__ in ("_dependency", "_hub_dependency"):
        freevars = call.__code__.co_freevars
        closure = call.__closure__ or ()
        for name, cell in zip(freevars, closure, strict=True):
            if name == "permission":
                return cell.cell_contents
        return None
    return None


def _closed_over(call: object, name: str) -> object | None:
    """The value ``call`` closes over as ``name``, or ``None`` when it closes over none by that name."""
    freevars = getattr(getattr(call, "__code__", None), "co_freevars", ())
    closure = getattr(call, "__closure__", None) or ()
    return next((cell.cell_contents for var, cell in zip(freevars, closure, strict=True) if var == name), None)


def _admitted_permission(route: APIRoute) -> Permission | None:
    """The hub-level permission ``route``'s router admits a non-member under, or ``None`` on a plain router."""
    for dep in route.dependant.dependencies:
        if isinstance(dep.call, TenantBoundAdmitting):
            return dep.call.permission
    return None


def _gate_alternative(route: APIRoute) -> Permission | None:
    """The permission ``route``'s ``require_either`` gate admits in place of its own, or ``None``."""
    for dep in route.dependant.dependencies:
        if getattr(dep.call, "__name__", None) == "_dependency":
            alternative = _closed_over(dep.call, "alternative")
            if isinstance(alternative, Permission):
                return alternative
    return None


def _required_permission(route: APIRoute) -> Permission | None:
    """The permission (see :func:`_permission_of`) the first recognized dependency on
    ``route`` enforces, or ``None`` if none of its dependencies are of a recognized
    shape."""
    for dep in route.dependant.dependencies:
        permission = _permission_of(dep.call)
        if permission is not None:
            return permission
    return None


def _routes_by_key(app: FastAPI) -> dict[tuple[str, str], APIRoute]:
    by_key: dict[tuple[str, str], APIRoute] = {}
    for route in api_routes(app):
        for method in route.methods or set():
            if method == "HEAD":
                continue
            by_key[(method, route.path)] = route
    return by_key


def test_every_live_route_is_classified(tmp_path: Path) -> None:
    """No route is unclassified: every live route appears in exactly one of the three
    plane tables — a newly added, unnamed route fails this assertion."""
    app = build_hub(tmp_path).wired_app
    assert isinstance(app, FastAPI)
    live = live_routes(app)
    classified = _PUBLIC | set(HUMAN) | FLEET | _HOST | {FLEET_CLAIM}
    unclassified = live - classified
    assert not unclassified, f"unclassified route(s): {sorted(unclassified)}"


def test_every_classified_route_is_still_live(tmp_path: Path) -> None:
    """The inverse check: no table entry names a route that no longer exists (a
    renamed/removed route leaving a stale, silently-untested table row)."""
    app = build_hub(tmp_path).wired_app
    assert isinstance(app, FastAPI)
    live = live_routes(app)
    classified = _PUBLIC | set(HUMAN) | FLEET | _HOST | {FLEET_CLAIM}
    stale = classified - live
    assert not stale, f"table entry(ies) naming a route no longer mounted: {sorted(stale)}"


def test_human_routes_require_their_declared_permission(tmp_path: Path) -> None:
    """Every route named **human** actually carries a ``require(<permission>)``
    dependency for exactly the permission the table declares — introspected off the
    live route, not merely asserted by table membership."""
    by_key = _routes_by_key(build_hub(tmp_path).wired_app)
    for key, expected_permission in HUMAN.items():
        route = by_key[key]
        assert _required_permission(route) == expected_permission, key


def test_public_routes_carry_no_permission_gate(tmp_path: Path) -> None:
    """Every route named **public** carries no ``require(<permission>)`` dependency."""
    by_key = _routes_by_key(build_hub(tmp_path).wired_app)
    for key in _PUBLIC:
        route = by_key[key]
        assert _required_permission(route) is None, key


def test_fleet_routes_carry_the_runner_principal_gate_not_a_permission(tmp_path: Path) -> None:
    """Every route named **fleet** is gated by ``require_runner_principal``
    and carries no human ``require(<permission>)`` dependency —
    the fleet plane is not human-permission-gated at all."""
    by_key = _routes_by_key(build_hub(tmp_path).wired_app)
    for key in FLEET:
        route = by_key[key]
        assert "require_runner_principal" in dependency_names(route), key
        assert _required_permission(route) is None, key
    claim = by_key[FLEET_CLAIM]
    assert "require_runner_principal_even_if_retired" in dependency_names(claim)
    assert "require_runner_principal" not in dependency_names(claim)
    assert _required_permission(claim) is None


def test_host_routes_carry_the_host_principal_gate_and_never_the_runner_one(tmp_path: Path) -> None:
    """Every route named **host** is gated by ``require_host_principal`` and by no runner principal or human
    permission, so a runner's token resolves no host and a host's token resolves no runner."""
    by_key = _routes_by_key(build_hub(tmp_path).wired_app)
    for key in _HOST:
        route = by_key[key]
        names = dependency_names(route)
        assert "require_host_principal" in names, key
        assert "require_runner_principal" not in names, key
        assert "require_runner_principal_even_if_retired" not in names, key
        assert _required_permission(route) is None, key


def test_no_other_fleet_route_accepts_the_host_principal(tmp_path: Path) -> None:
    by_key = _routes_by_key(build_hub(tmp_path).wired_app)
    for key in FLEET | {FLEET_CLAIM}:
        assert "require_host_principal" not in dependency_names(by_key[key]), key


def test_human_routes_resolve_their_tenant_unless_hub_level(tmp_path: Path) -> None:
    """A tenant route resolves its tenant at router level (``tenant_bound``, or
    ``TenantBoundAdmitting`` for a route in :data:`_ADMITTING`) and gates on a permission in it
    (``require``); a hub-level route resolves none and gates on a hub permission (``require_hub``)."""
    by_key = _routes_by_key(build_hub(tmp_path).wired_app)
    for key in HUMAN:
        names = dependency_names(by_key[key])
        resolvers = names & {"tenant_bound", "TenantBoundAdmitting"}
        if key in HUB_LEVEL:
            assert not resolvers and "_hub_dependency" in names, key
        else:
            expected = "TenantBoundAdmitting" if key in _ADMITTING else "tenant_bound"
            assert resolvers == {expected} and "_hub_dependency" not in names, key


def test_only_the_named_routes_admit_a_hub_permission_without_a_membership(tmp_path: Path) -> None:
    """Exhaustive both ways: a live route's router admits a non-member exactly when the route is in
    :data:`_ADMITTING`, under exactly the permission named there, which its gate takes as the
    alternative to its tenant permission."""
    by_key = _routes_by_key(build_hub(tmp_path).wired_app)
    assert set(_ADMITTING) <= set(HUMAN) - HUB_LEVEL
    for key, route in by_key.items():
        assert _admitted_permission(route) == _ADMITTING.get(key), key
    for key, admitted in _ADMITTING.items():
        assert _gate_alternative(by_key[key]) == admitted, key


def test_the_admission_introspection_reads_an_admitting_route() -> None:
    """The introspection the admission table is checked by recognizes an admitting router and its gate."""
    router = APIRouter(dependencies=[Depends(TenantBoundAdmitting(Permission.MEMBERSHIP_GRANT_ANY))])

    @router.get(
        "/scratch", dependencies=[Depends(require_either(Permission.USER_MANAGE, Permission.MEMBERSHIP_GRANT_ANY))]
    )
    def scratch() -> None: ...

    app = FastAPI()
    app.include_router(router)
    (route,) = api_routes(app)

    assert _admitted_permission(route) is Permission.MEMBERSHIP_GRANT_ANY
    assert _gate_alternative(route) is Permission.MEMBERSHIP_GRANT_ANY
    assert _required_permission(route) is Permission.USER_MANAGE


def test_fleet_routes_resolve_their_tenant_and_public_routes_none(tmp_path: Path) -> None:
    """A fleet route's tenant is its credential's, resolved at router level; a public route
    serves no tenant."""
    by_key = _routes_by_key(build_hub(tmp_path).wired_app)
    for key in FLEET | _HOST | {FLEET_CLAIM}:
        assert "tenant_bound" in dependency_names(by_key[key]), key
    for key in _PUBLIC:
        assert "tenant_bound" not in dependency_names(by_key[key]), key
