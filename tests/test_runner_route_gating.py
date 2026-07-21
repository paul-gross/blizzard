"""The exhaustive three-lane gating guard for the runner's API seam (issue #95).

The runner counterpart to the hub's ``tests/test_route_classification.py``: every
mounted ``/api/*`` route is asserted to be either **human-web-lane** (session-gated —
an unauthenticated TCP request under an oauth-mode hub gets a ``401``) or **open** (the
worker-hook lane and the public routes — never gated by a human session, so an
unauthenticated TCP request reaches the handler, whatever non-``401`` it then returns).

This is a **behavioural** guard, not an introspective one: it drives real requests
against a real (store-free) app whose hub is oauth-mode, and reads the status the gate
actually produces. That is deliberate — the router-level ``Depends(require_human_api)``
attached at ``include_router`` time is not reliably visible on the pre-inclusion route
objects in this FastAPI version, and the property that matters is the *effect* (a 401 or
not), which only a request observes. The table is exhaustive in both directions: a newly
added route absent from both lanes fails the suite, and a lane entry that no longer
resolves to a live route fails too — the same "no route unclassified" guarantee the hub
guard gives, now for the runner's own three-tenant partition.

The socket lane (``request.client is None``) and the ``none``-mode hub are covered by
``tests/test_runner_federation.py`` (both resolve to the implicit identity, ungated);
this guard pins the *TCP-under-oauth* split those two carve-outs are measured against.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute, _IncludedRouter
from fastapi.testclient import TestClient

from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig

pytestmark = pytest.mark.unit

# --- The two-lane table -----------------------------------------------------

#: Human web lane — the panel's own reads/writes; ``require_human_api`` gates each so an
#: unauthenticated TCP request under an oauth-mode hub is ``401`` (a CLI reaching these
#: over the socket, or any request under a ``none``-mode hub, resolves to the implicit
#: identity and is ungated — measured elsewhere).
_HUMAN: set[tuple[str, str]] = {
    ("GET", "/api/asks"),
    ("GET", "/api/leases"),
    ("GET", "/api/leases/{lease_id}/transcript"),
    ("POST", "/api/selftests"),
    ("GET", "/api/selftests/{selftest_id}"),
    ("GET", "/api/fleet-summary"),
    ("GET", "/api/workspace-prompt"),
    ("PUT", "/api/workspace-prompt"),
    ("GET", "/api/runner"),
    ("PATCH", "/api/runner"),
    ("GET", "/api/environments"),
    ("GET", "/api/escalations"),
    ("GET", "/api/facts"),
    ("POST", "/api/chunks/{chunk_id}/takeovers"),
    ("PATCH", "/api/chunks/{chunk_id}/takeovers/{takeover_id}"),
    ("GET", "/api/takeovers"),
    ("POST", "/api/chunks/{chunk_id}/requeues"),
}

#: Open — the worker-hook lane (workers call over TCP via ``BLIZZARD_RUNNER_URL`` and
#: cannot SSO-bounce) plus the public routes (health/readiness and the auth bounce
#: itself, which *establishes* a session so cannot be session-gated). Never ``401``.
_OPEN: set[tuple[str, str]] = {
    ("GET", "/api/health"),
    ("GET", "/api/ready"),
    ("GET", "/api/auth/login"),
    ("POST", "/api/auth/callback"),
    ("POST", "/api/heartbeat"),
    ("POST", "/api/leases/{lease_id}/session-end"),
    ("POST", "/api/leases/{lease_id}/asks"),
    ("POST", "/api/leases/{lease_id}/attachments"),
    ("GET", "/api/leases/{lease_id}/artifacts"),
    ("GET", "/api/leases/{lease_id}/artifacts/{name}"),
    ("GET", "/api/chunks/{chunk_id}/pm-items"),
}


def _oauth_hub_client() -> httpx.Client:
    """A hermetic hub double whose ``jwks.json`` answers ``200`` — the runner reads that
    as "the hub runs an IdP surface" (``HubAuthModeCache.enabled()``), engaging the
    human-lane gate exactly as a real oauth-mode hub would."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": []})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://hub.example")


def _oauth_app() -> TestClient:
    config = RunnerConfig(
        root=Path("/tmp/runner-gating-guard"),  # store-free app: a gated route 401s before any store read
        db_url="sqlite://",
        runner_id="runner-guard",
        hub_url="http://hub.example",
        public_url="https://runner-guard.example",
    )
    return TestClient(create_app(config, hub_http_client=_oauth_hub_client()))


def _live_routes(client: TestClient) -> list[APIRoute]:
    def collect(routes: object) -> list[APIRoute]:
        out: list[APIRoute] = []
        for route in routes:  # type: ignore[attr-defined]
            if isinstance(route, APIRoute):
                out.append(route)
            elif isinstance(route, _IncludedRouter):
                out.extend(collect(route.original_router.routes))
        return out

    return collect(client.app.routes)  # type: ignore[attr-defined]


def _request(client: TestClient, method: str, path_template: str) -> int:
    """Issue ``method`` against ``path_template`` with placeholders substituted, returning
    the status. A body-carrying verb sends ``{}`` — a gated route ``401``s before body
    validation; an open worker-hook route reaches its handler (a ``422``/``404``/``503``,
    all non-``401``), which is exactly the distinction under test."""
    path = path_template.replace("{lease_id}", "lease-x").replace("{chunk_id}", "chunk-x")
    path = path.replace("{takeover_id}", "takeover-x").replace("{selftest_id}", "selftest-x")
    path = path.replace("{name}", "artifact-x")
    return client.request(method, path, json={}).status_code


def test_every_live_route_is_classified() -> None:
    """No route is unclassified: every live ``(method, path)`` appears in exactly one
    lane — a newly added, unnamed route fails here."""
    client = _oauth_app()
    live = {(m, r.path) for r in _live_routes(client) for m in (r.methods or set()) if m != "HEAD"}
    classified = _HUMAN | _OPEN
    assert not (live - classified), f"unclassified route(s): {sorted(live - classified)}"
    assert not (classified - live), f"lane entry naming a route no longer mounted: {sorted(classified - live)}"


def test_human_lane_routes_are_gated_401_over_tcp_under_oauth() -> None:
    """Every human-lane route ``401``s for an unauthenticated TCP request under an
    oauth-mode hub — the panel's JSON API is a session-gated surface, not an open one
    behind a gated HTML shell."""
    client = _oauth_app()
    for method, path in sorted(_HUMAN):
        assert _request(client, method, path) == 401, (method, path)


def test_open_lane_routes_reach_their_handler_over_tcp_under_oauth() -> None:
    """Every worker-hook / public route stays reachable (never ``401``) even with the
    hub's IdP surface active — a running worker's heartbeat/asks/attachments and the
    public bounce/health routes are never gated by a human session."""
    client = _oauth_app()
    for method, path in sorted(_OPEN):
        assert _request(client, method, path) != 401, (method, path)
