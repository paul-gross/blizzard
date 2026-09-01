"""The exhaustive three-plane route classification guard (issue #91).

Every mounted route is asserted human (gated by ``require(<permission>)``), fleet
(mounted under ``/api/fleet/*``, gated at router level), or public (no gate at all). The
table is exhaustive both ways: every live route must appear, every entry must resolve.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, _IncludedRouter

from blizzard.auth_core import (
    ANALYTICS_ADMIN,
    CHUNK_CONTROL,
    CHUNK_INGEST,
    FLEET_VIEW,
    GATE_RESOLVE,
    GRAPH_EDIT,
    QUESTION_ANSWER,
    QUEUE_REORDER,
    RUNNER_PAUSE,
    TRANSCRIPT_READ,
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
    # itself rather than being gated by one; `jwks.json` is public key material.
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
    ("GET", "/api/activity"): FLEET_VIEW,
    ("POST", "/api/graphs"): GRAPH_EDIT,
    # Reconciliation mints (issue #146), so it needs exactly what an explicit mint needs.
    ("POST", "/api/graphs/sync"): GRAPH_EDIT,
    ("GET", "/api/graphs"): FLEET_VIEW,
    ("GET", "/api/graphs/{graph_id}"): FLEET_VIEW,
    ("POST", "/api/graphs/{graph_id}/retire"): GRAPH_EDIT,
    ("POST", "/api/graphs/{graph_id}/enable"): GRAPH_EDIT,
    # The follow-latest policy (issue #164) — a graph lifecycle write like retire/enable.
    ("POST", "/api/graphs/{graph_id}/follow-latest"): GRAPH_EDIT,
    # Scopes (blizzard#389) — reads take FLEET_VIEW, writes take GRAPH_EDIT (D8).
    ("POST", "/api/scopes"): GRAPH_EDIT,
    ("GET", "/api/scopes"): FLEET_VIEW,
    ("GET", "/api/scopes/{slug}"): FLEET_VIEW,
    ("PATCH", "/api/scopes/{slug}"): GRAPH_EDIT,
    ("POST", "/api/scopes/{slug}/retire"): GRAPH_EDIT,
    ("POST", "/api/scopes/{slug}/enable"): GRAPH_EDIT,
    # Routines (blizzard#389) — reads take FLEET_VIEW, writes take GRAPH_EDIT (D8).
    ("POST", "/api/routines"): GRAPH_EDIT,
    ("GET", "/api/routines"): FLEET_VIEW,
    ("GET", "/api/routines/{routine_id}"): FLEET_VIEW,
    ("PATCH", "/api/routines/{routine_id}"): GRAPH_EDIT,
    ("GET", "/api/routines/trend"): FLEET_VIEW,  # blizzard#394 Phase 4
    ("GET", "/api/routines/{routine_id}/sweeps"): FLEET_VIEW,
    # Mint, ingest, and promote a run in one act (blizzard#392) — the same CHUNK_CONTROL
    # the acts it composes (ingest, promote) already require.
    ("POST", "/api/routines/{routine_id}/run"): CHUNK_CONTROL,
    # The per-scope delta baseline a routine has swept (blizzard#399 D5) — a read, FLEET_VIEW.
    ("GET", "/api/routines/{routine_id}/baselines"): FLEET_VIEW,
    # Findings and garden proposals (blizzard#390) — read-only routes, both FLEET_VIEW (D8).
    ("GET", "/api/findings"): FLEET_VIEW,
    ("GET", "/api/findings/{finding_id}"): FLEET_VIEW,
    ("GET", "/api/garden-proposals"): FLEET_VIEW,
    ("GET", "/api/garden-proposals/{proposal_id}"): FLEET_VIEW,
    # Closing a garden proposal (blizzard#395) — CHUNK_CONTROL, the same permission a
    # not-chunk-scoped work-item write already carries (D8).
    ("POST", "/api/garden-proposals/{proposal_id}/pass"): CHUNK_CONTROL,
    ("POST", "/api/garden-proposals/{proposal_id}/accept"): CHUNK_CONTROL,
    # The human-driven exit verbs and `reopen` over findings (blizzard#394 Phase 2) — the
    # same CHUNK_CONTROL a garden-proposal closure already carries.
    ("POST", "/api/findings/resolve"): CHUNK_CONTROL,
    ("POST", "/api/findings/confirm-gone"): CHUNK_CONTROL,
    ("POST", "/api/findings/wont-fix"): CHUNK_CONTROL,
    ("POST", "/api/findings/not-a-finding"): CHUNK_CONTROL,
    ("POST", "/api/findings/supersede"): CHUNK_CONTROL,
    ("POST", "/api/findings/reopen"): CHUNK_CONTROL,
    ("POST", "/api/chunks"): CHUNK_INGEST,
    ("GET", "/api/chunks"): FLEET_VIEW,
    ("GET", "/api/chunks/{chunk_id}"): FLEET_VIEW,
    ("POST", "/api/chunks/{chunk_id}/hub-markers"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/garden-delivery"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/requeues"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/restart"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/detach"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/pause"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/resume"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/stop"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/complete"): CHUNK_CONTROL,
    ("POST", "/api/chunks/{chunk_id}/promote"): CHUNK_CONTROL,
    ("PATCH", "/api/chunks/{chunk_id}"): CHUNK_CONTROL,
    ("DELETE", "/api/chunks/{chunk_id}"): CHUNK_CONTROL,
    ("GET", "/api/chunks/{chunk_id}/work-items"): FLEET_VIEW,
    # The issue-#55 deprecated alias onto the same handler — classified identically,
    # because an alias that fell into a different plane would be an authz hole.
    ("GET", "/api/chunks/{chunk_id}/pm-items"): FLEET_VIEW,
    ("GET", "/api/decisions"): FLEET_VIEW,
    ("POST", "/api/decisions/{decision_id}/resolutions"): GATE_RESOLVE,
    ("GET", "/api/queue"): FLEET_VIEW,
    ("PUT", "/api/queue"): QUEUE_REORDER,
    ("POST", "/api/queue/position"): QUEUE_REORDER,
    # Backlog (``not_ready``) reorder — QUEUE_REORDER even to read, an operator
    # triage surface, narrower than the ready queue's FLEET_VIEW.
    ("GET", "/api/backlog"): QUEUE_REORDER,
    ("PUT", "/api/backlog"): QUEUE_REORDER,
    ("POST", "/api/backlog/position"): QUEUE_REORDER,
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
    # Transcript-segment discovery/content reads (blizzard#247, D11) — above
    # FLEET_VIEW, since a transcript carries everything a worker saw.
    ("GET", "/api/chunks/{chunk_id}/transcripts"): TRANSCRIPT_READ,
    ("GET", "/api/chunks/{chunk_id}/transcripts/{segment_id}"): TRANSCRIPT_READ,
    # Forced transcript-event re-derivation (blizzard#254 D7) — a mutation, above the
    # read-only TRANSCRIPT_READ.
    ("POST", "/api/analytics/re-derive"): ANALYTICS_ADMIN,
    # The read-only events/counts surfaces (blizzard#255 D2) — no grant of their own.
    ("GET", "/api/analytics/events"): TRANSCRIPT_READ,
    ("GET", "/api/analytics/events/ndjson"): TRANSCRIPT_READ,
    ("GET", "/api/analytics/counts/files"): TRANSCRIPT_READ,
    ("GET", "/api/analytics/counts/skills"): TRANSCRIPT_READ,
    ("GET", "/api/analytics/counts/agent-types"): TRANSCRIPT_READ,
    ("GET", "/api/analytics/counts/nodes"): TRANSCRIPT_READ,
    # The operational datasets (blizzard#256 D9) — durations, spend, outcomes — no
    # grant of their own, strictly narrower than the FLEET_VIEW the same numbers
    # already sit behind at /api/spend and on every board card.
    ("GET", "/api/analytics/durations/nodes"): TRANSCRIPT_READ,
    ("GET", "/api/analytics/durations/graphs"): TRANSCRIPT_READ,
    ("GET", "/api/analytics/spend/nodes"): TRANSCRIPT_READ,
    ("GET", "/api/analytics/spend/graphs"): TRANSCRIPT_READ,
    ("GET", "/api/analytics/spend/chunks"): TRANSCRIPT_READ,
    ("GET", "/api/analytics/spend/chunks/ndjson"): TRANSCRIPT_READ,
    ("GET", "/api/analytics/outcomes/nodes"): TRANSCRIPT_READ,
    # The work-source item routes (blizzard#358) — the same two permissions the chunk
    # work-item read and its mutations already sit behind.
    ("GET", "/api/work-sources"): FLEET_VIEW,
    ("GET", "/api/work-sources/{source}/items"): FLEET_VIEW,
    ("POST", "/api/work-sources/{source}/items"): CHUNK_CONTROL,
    ("GET", "/api/work-sources/{source}/items/{ref}"): FLEET_VIEW,
    ("PATCH", "/api/work-sources/{source}/items/{ref}"): CHUNK_CONTROL,
    ("DELETE", "/api/work-sources/{source}/items/{ref}"): CHUNK_CONTROL,
}

#: Fleet plane — every route mounted under ``/api/fleet/*`` (issue #87's own
#: ``require_runner_principal``-at-router-level confinement); no per-route permission.
_FLEET: set[tuple[str, str]] = {
    ("GET", "/api/fleet/queue/peek"),
    ("GET", "/api/fleet/chunks/{chunk_id}"),
    ("GET", "/api/fleet/chunks/{chunk_id}/work-items"),
    ("GET", "/api/fleet/chunks/{chunk_id}/pm-items"),  # the issue-#55 deprecated alias
    ("POST", "/api/fleet/chunks/{chunk_id}/pause"),
    ("POST", "/api/fleet/chunks/{chunk_id}/resume"),
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
    ("POST", "/api/fleet/transcripts"),  # the transcript lane's own push (blizzard#247, D7)
    # A lease's own read-back of its shipped segments, confined by its own always-raising
    # ownership check rather than `assert_owns` (blizzard#249, D3).
    ("GET", "/api/fleet/chunks/{chunk_id}/transcript-segments"),
    # blizzard's own published `ArtifactScope.SYSTEM` set (blizzard#391) — read-only,
    # resolved live off the packaged set rather than any lease or chunk.
    ("GET", "/api/fleet/system-artifacts"),
    ("GET", "/api/fleet/system-artifacts/{name:path}"),
}


def _api_routes(app: FastAPI) -> list[APIRoute]:
    """Every mounted :class:`APIRoute`, recursively unwrapped.

    This FastAPI version does not flatten an included router's routes onto
    ``app.routes`` eagerly, so recursing through ``_IncludedRouter.original_router``
    is the stable way to enumerate the live surface regardless of nesting."""
    routes: list[APIRoute] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            # The web root is the SPA shell, not an API surface — stays out of the
            # plane table.
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


def _permission_of(call: object) -> Permission | None:
    """The exact :class:`Permission` a ``require(<permission>)`` dependency closes
    over, or ``None`` if ``call`` is not one — introspects the closure cell rather
    than trusting a second hand-maintained map, so this cannot silently drift from
    what the route actually enforces."""
    if getattr(call, "__name__", None) == "_dependency":
        freevars = call.__code__.co_freevars  # type: ignore[union-attr]
        closure = call.__closure__ or ()  # type: ignore[union-attr]
        for name, cell in zip(freevars, closure, strict=True):
            if name == "permission":
                return cell.cell_contents
        return None
    if getattr(call, "__name__", None) == "require_marker_authority":
        fallback = call.__globals__.get("_require_chunk_control")  # type: ignore[union-attr]
        return _permission_of(fallback) if fallback is not None else None
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
