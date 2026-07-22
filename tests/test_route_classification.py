"""The exhaustive three-plane route classification guard (unit tier, issue #91).

Mirrors ``reject_runner_principal``'s own structural-confinement shape
(``tests/test_fleet_auth.py``) one level up: every mounted route is asserted to be
**human** (gated by ``require(<permission>)``, tagged with the exact permission),
**fleet** (mounted under ``/api/fleet/*``, gated by ``require_runner_principal`` at
router level — issue #87), or **public** (no permission gate at all). A route this
table does not name — or a route whose live gating no longer matches its named
plane — fails the suite, the same "no route unclassified" guarantee
``test_fleet_auth.py`` already gives the fleet/operator split.

The table is asserted **exhaustive in both directions**: every live route must appear
in the table (catches a newly added, unclassified route) and every table entry must
still resolve to a live route (catches a route renamed/removed out from under a stale
entry).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, _IncludedRouter

from blizzard.auth_core import (
    CHUNK_CONTROL,
    CHUNK_INGEST,
    FLEET_VIEW,
    GATE_RESOLVE,
    GRAPH_EDIT,
    QUESTION_ANSWER,
    QUEUE_REORDER,
    RUNNER_PAUSE,
    USER_MANAGE,
    Permission,
)
from tests.support import build_hub

pytestmark = pytest.mark.unit

# --- The three-plane table --------------------------------------------------

#: Public plane — no permission gate; reachable with no session at all.
_PUBLIC: set[tuple[str, str]] = {
    ("GET", "/api/health"),
    ("GET", "/api/ready"),
    ("GET", "/api/me"),
    ("GET", "/api/auth/providers"),
    ("GET", "/api/auth/{name}/authorize"),
    ("GET", "/api/auth/{name}/callback"),
    ("POST", "/api/auth/logout"),
    # The hub-as-IdP surface (issue #95) — `authorize` authenticates the browser
    # itself (via an existing session, or the #92 dance) rather than being gated by
    # one; `jwks.json` is by definition public key material.
    ("GET", "/api/auth/authorize"),
    ("GET", "/api/auth/jwks.json"),
    # The CLI's PKCE code exchange (issue #96) — there is no session yet at this
    # point, that is what this route mints.
    ("POST", "/api/auth/cli/token"),
}

#: Human plane — ``(method, path) -> permission`` required via ``require(<permission>)``.
_HUMAN: dict[tuple[str, str], Permission] = {
    ("GET", "/api/events/stream"): FLEET_VIEW,
    ("GET", "/api/events"): FLEET_VIEW,
    ("POST", "/api/graphs"): GRAPH_EDIT,
    ("GET", "/api/graphs"): FLEET_VIEW,
    ("GET", "/api/graphs/{graph_id}"): FLEET_VIEW,
    ("POST", "/api/graphs/{graph_id}/retire"): GRAPH_EDIT,
    ("POST", "/api/graphs/{graph_id}/enable"): GRAPH_EDIT,
    ("POST", "/api/chunks"): CHUNK_INGEST,
    ("GET", "/api/chunks"): FLEET_VIEW,
    ("GET", "/api/chunks/{chunk_id}"): FLEET_VIEW,
    ("POST", "/api/chunks/{chunk_id}/hub-markers"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/requeues"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/detach"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/pause"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/resume"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/stop"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/promote"): CHUNK_CONTROL,
    ("PATCH", "/api/chunks/{chunk_id}"): CHUNK_CONTROL,
    ("GET", "/api/chunks/{chunk_id}/pm-items"): FLEET_VIEW,
    ("GET", "/api/decisions"): FLEET_VIEW,
    ("POST", "/api/decisions/{decision_id}/resolutions"): GATE_RESOLVE,
    ("GET", "/api/queue"): FLEET_VIEW,
    ("PUT", "/api/queue"): QUEUE_REORDER,
    ("POST", "/api/chunks/{chunk_id}/group"): QUEUE_REORDER,
    ("POST", "/api/questions"): QUESTION_ANSWER,
    ("POST", "/api/questions/{question_id}/answers"): QUESTION_ANSWER,
    ("GET", "/api/questions"): FLEET_VIEW,
    ("POST", "/api/runners/{runner_id}/enrollments"): RUNNER_PAUSE,
    ("GET", "/api/runners"): FLEET_VIEW,
    ("GET", "/api/runners/{runner_id}"): FLEET_VIEW,
    ("POST", "/api/runners/{runner_id}/pause"): RUNNER_PAUSE,
    ("POST", "/api/runners/{runner_id}/resume"): RUNNER_PAUSE,
    ("GET", "/api/spend"): FLEET_VIEW,
    ("GET", "/api/users"): USER_MANAGE,
    ("POST", "/api/users/{user_id}/role"): USER_MANAGE,
    # Key rotation (issue #95) — the same admin-tier permission the user-management
    # API uses; no new permission is minted for this one verb.
    ("POST", "/api/auth/rotate-signing-key"): USER_MANAGE,
}

#: Fleet plane — every route mounted under ``/api/fleet/*`` (issue #87's own
#: ``require_runner_principal``-at-router-level confinement); no per-route permission.
_FLEET: set[tuple[str, str]] = {
    ("GET", "/api/fleet/queue/peek"),
    ("GET", "/api/fleet/chunks/{chunk_id}"),
    ("GET", "/api/fleet/chunks/{chunk_id}/pm-items"),
    ("GET", "/api/fleet/summary"),
    ("GET", "/api/fleet/questions/{question_id}"),
    ("GET", "/api/fleet/chunks/{chunk_id}/envelope"),
    ("POST", "/api/fleet/chunks/{chunk_id}/hub-advance"),
    ("POST", "/api/fleet/routes"),
    ("POST", "/api/fleet/chunks/{chunk_id}/route-token"),
    ("POST", "/api/fleet/chunks/{chunk_id}/completions"),
    ("POST", "/api/fleet/chunks/{chunk_id}/decisions"),
    ("POST", "/api/fleet/chunks/{chunk_id}/leases"),
    ("POST", "/api/fleet/chunks/{chunk_id}/escalations"),
    ("POST", "/api/fleet/events"),
    ("POST", "/api/fleet/runners"),
    ("POST", "/api/fleet/runners/{runner_id}/heartbeats"),
    ("GET", "/api/fleet/runners/{runner_id}"),
}


def _api_routes(app: FastAPI) -> list[APIRoute]:
    """Every mounted :class:`APIRoute`, recursively unwrapped.

    This FastAPI version (0.139) does not flatten an included router's routes onto
    ``app.routes`` eagerly — each ``include_router`` call leaves a lazy
    ``_IncludedRouter`` wrapper whose ``original_router.routes`` holds the real,
    already-prefixed :class:`APIRoute` objects (a sub-router's own ``prefix`` is baked
    into ``route.path`` at the point its routes were declared, not at inclusion time),
    so recursing through that attribute is the stable way to enumerate the live
    surface regardless of nesting."""
    routes: list[APIRoute] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            # The web root is the SPA shell, not an API surface: with a built bundle
            # it is a Starlette Mount (never an APIRoute), without one it is
            # foundation/web.py's placeholder GET / — public by construction either
            # way, and environment-dependent, so it stays out of the plane table.
            if route.path == "/":
                continue
            routes.append(route)
        elif isinstance(route, _IncludedRouter):
            routes.extend(_api_routes_of(route))
    return routes


def _api_routes_of(included: _IncludedRouter) -> list[APIRoute]:
    routes: list[APIRoute] = []
    for route in included.original_router.routes:
        if isinstance(route, APIRoute):
            routes.append(route)
        elif isinstance(route, _IncludedRouter):
            routes.extend(_api_routes_of(route))
    return routes


def _live_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Every ``(method, path)`` pair the app actually mounts, ``HEAD`` excluded
    (FastAPI auto-adds it alongside every ``GET``, so it carries no separate
    classification)."""
    live: set[tuple[str, str]] = set()
    for route in _api_routes(app):
        for method in route.methods or set():
            if method == "HEAD":
                continue
            live.add((method, route.path))
    return live


def _dependency_names(route: APIRoute) -> set[str]:
    """The ``__name__`` of every dependency callable resolved for ``route`` — both
    route-level (``dependencies=[...]``) and router-level (attached at
    ``APIRouter(dependencies=[...])``, e.g. ``reject_runner_principal``/
    ``require_runner_principal``), so a single check covers both attachment shapes."""
    return {dep.call.__name__ for dep in route.dependant.dependencies if dep.call is not None}


def _required_permission(route: APIRoute) -> Permission | None:
    """The exact :class:`Permission` a ``require(<permission>)`` dependency closes
    over, or ``None`` if the route carries no such dependency — introspects the
    closure cell rather than trusting a second hand-maintained map, so this check
    cannot silently drift from what the route actually enforces."""
    for dep in route.dependant.dependencies:
        call = dep.call
        if call is None or call.__name__ != "_dependency":
            continue
        freevars = call.__code__.co_freevars
        closure = call.__closure__ or ()
        for name, cell in zip(freevars, closure, strict=True):
            if name == "permission":
                return cell.cell_contents
    return None


def _routes_by_key(app: FastAPI) -> dict[tuple[str, str], APIRoute]:
    by_key: dict[tuple[str, str], APIRoute] = {}
    for route in _api_routes(app):
        for method in route.methods or set():
            if method == "HEAD":
                continue
            by_key[(method, route.path)] = route
    return by_key


def test_every_live_route_is_classified(tmp_path: Path) -> None:
    """No route is unclassified: every live route appears in exactly one of the three
    plane tables — a newly added, unnamed route fails this assertion."""
    app = build_hub(tmp_path).client.app
    assert isinstance(app, FastAPI)
    live = _live_routes(app)
    classified = _PUBLIC | set(_HUMAN) | _FLEET
    unclassified = live - classified
    assert not unclassified, f"unclassified route(s): {sorted(unclassified)}"


def test_every_classified_route_is_still_live(tmp_path: Path) -> None:
    """The inverse check: no table entry names a route that no longer exists (a
    renamed/removed route leaving a stale, silently-untested table row)."""
    app = build_hub(tmp_path).client.app
    assert isinstance(app, FastAPI)
    live = _live_routes(app)
    classified = _PUBLIC | set(_HUMAN) | _FLEET
    stale = classified - live
    assert not stale, f"table entry(ies) naming a route no longer mounted: {sorted(stale)}"


def test_human_routes_require_their_declared_permission(tmp_path: Path) -> None:
    """Every route named **human** actually carries a ``require(<permission>)``
    dependency for exactly the permission the table declares — introspected off the
    live route, not merely asserted by table membership."""
    by_key = _routes_by_key(build_hub(tmp_path).client.app)
    for key, expected_permission in _HUMAN.items():
        route = by_key[key]
        assert _required_permission(route) == expected_permission, key


def test_public_routes_carry_no_permission_gate(tmp_path: Path) -> None:
    """Every route named **public** carries no ``require(<permission>)`` dependency."""
    by_key = _routes_by_key(build_hub(tmp_path).client.app)
    for key in _PUBLIC:
        route = by_key[key]
        assert _required_permission(route) is None, key


def test_fleet_routes_carry_the_runner_principal_gate_not_a_permission(tmp_path: Path) -> None:
    """Every route named **fleet** is gated by ``require_runner_principal`` (issue
    #87's own confinement) and carries no human ``require(<permission>)`` dependency —
    the fleet plane is not human-permission-gated at all (issue #91's stated residue)."""
    by_key = _routes_by_key(build_hub(tmp_path).client.app)
    for key in _FLEET:
        route = by_key[key]
        assert "require_runner_principal" in _dependency_names(route), key
        assert _required_permission(route) is None, key
