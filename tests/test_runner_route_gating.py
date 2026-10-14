"""The exhaustive three-lane gating guard for the runner's API seam.

Every mounted ``/api/*`` route is either human-web-lane (session-gated: an
unauthenticated TCP request under an oauth-mode hub gets a ``401``) or open (never
gated). A behavioural guard: drives real requests against a real app and reads the
status the gate produces; an unclassified route or a stale lane entry fails the suite."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from tests.runner_client import live_api_routes
from tests.runner_fakes import registered_identity

pytestmark = pytest.mark.unit

# --- The two-lane table -----------------------------------------------------

#: Human web lane — the panel's own reads/writes; ``require_human_api`` gates each so an
#: unauthenticated TCP request under an oauth-mode hub is ``401``.
_HUMAN: set[tuple[str, str]] = {
    ("GET", "/runners/{runner_id}/api/asks"),
    ("GET", "/runners/{runner_id}/api/leases"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/transcript"),
    ("GET", "/runners/{runner_id}/api/chunks/{chunk_id}/transcripts"),
    ("GET", "/runners/{runner_id}/api/chunks/{chunk_id}/transcripts/{segment_id}"),
    ("POST", "/runners/{runner_id}/api/selftests"),
    ("GET", "/runners/{runner_id}/api/selftests/{selftest_id}"),
    ("GET", "/runners/{runner_id}/api/fleet-summary"),
    ("GET", "/api/host/workspaces/{workspace_id}/prompt"),
    ("PUT", "/api/host/workspaces/{workspace_id}/prompt"),
    ("DELETE", "/api/host/workspaces/{workspace_id}/prompt"),
    ("GET", "/runners/{runner_id}/api/runner"),
    ("PATCH", "/runners/{runner_id}/api/runner"),
    ("GET", "/api/host/environments"),
    ("GET", "/api/host/runners"),
    ("GET", "/api/host/workspaces"),
    ("GET", "/runners/{runner_id}/api/escalations"),
    ("GET", "/runners/{runner_id}/api/facts"),
    ("GET", "/runners/{runner_id}/api/harness-health"),
    ("POST", "/runners/{runner_id}/api/chunks/{chunk_id}/takeovers"),
    ("PATCH", "/runners/{runner_id}/api/chunks/{chunk_id}/takeovers/{takeover_id}"),
    ("GET", "/runners/{runner_id}/api/takeovers"),
    ("GET", "/runners/{runner_id}/api/dashboard"),
    ("GET", "/api/host/subscriptions"),
    ("GET", "/api/events/stream"),
    ("POST", "/runners/{runner_id}/api/chunks/{chunk_id}/requeues"),
    ("GET", "/runners/{runner_id}/api/chunks/{chunk_id}"),
    ("POST", "/runners/{runner_id}/api/chunks/{chunk_id}/pause"),
    ("POST", "/runners/{runner_id}/api/chunks/{chunk_id}/resume"),
    ("GET", "/runners/{runner_id}/api/traces/status"),
    ("POST", "/runners/{runner_id}/api/traces/replay"),
}

#: Open — the worker-hook lane (workers call over TCP and cannot SSO-bounce) plus the
#: public routes (health/readiness, the auth bounce, logout/session). Never ``401``.
_OPEN: set[tuple[str, str]] = {
    ("GET", "/api/health"),
    ("GET", "/api/ready"),
    ("GET", "/api/auth/login"),
    ("POST", "/api/auth/callback"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/auth/session"),
    ("POST", "/runners/{runner_id}/api/heartbeat"),
    ("POST", "/runners/{runner_id}/v1/traces"),
    ("POST", "/runners/{runner_id}/v1/metrics"),
    ("POST", "/runners/{runner_id}/v1/logs"),
    ("POST", "/runners/{runner_id}/api/leases/{lease_id}/session-end"),
    ("POST", "/runners/{runner_id}/api/leases/{lease_id}/asks"),
    ("POST", "/runners/{runner_id}/api/leases/{lease_id}/attachments"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/attachments"),
    ("POST", "/runners/{runner_id}/api/leases/{lease_id}/git-commits"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/artifacts"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/artifacts/{name:path}"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/history"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/asks"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/garden/findings"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/garden/proposals"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/findings"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/findings/{finding_id}"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/scopes"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/analytics/counts/files"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/analytics/counts/skills"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/analytics/counts/agent-types"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/analytics/counts/nodes"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/analytics/spend/nodes"),
    ("GET", "/runners/{runner_id}/api/leases/{lease_id}/analytics/spend/graphs"),
    ("GET", "/runners/{runner_id}/api/chunks/{chunk_id}/work-items"),
    # The issue-#55 deprecated alias onto the same handler — classified into the same
    # lane, because an alias that landed in a different one would be a gating hole.
    ("GET", "/runners/{runner_id}/api/chunks/{chunk_id}/pm-items"),
}


_RUNNER_ID = "runner-guard"


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
        name="runner-guard",
        hub_url="http://hub.example",
        public_urls=("https://runner-guard.example",),
    )
    return TestClient(create_app(config, hub_http_client=_oauth_hub_client(), identity=registered_identity()))


def _request(client: TestClient, method: str, path_template: str) -> int:
    """Issue ``method`` against ``path_template`` with placeholders substituted, returning
    the status. A body-carrying verb sends ``{}`` — a gated route ``401``s before body
    validation; an open worker-hook route reaches its handler (a ``422``/``404``/``503``,
    all non-``401``), which is exactly the distinction under test."""
    path = path_template.replace("{runner_id}", _RUNNER_ID).replace("{workspace_id}", "default")
    path = path.replace("{lease_id}", "lease-x").replace("{chunk_id}", "chunk-x")
    path = path.replace("{takeover_id}", "takeover-x").replace("{selftest_id}", "selftest-x")
    path = path.replace("{name}", "artifact-x")
    return client.request(method, path, json={}).status_code


def test_every_live_route_is_classified() -> None:
    """No route is unclassified: every live ``(method, path)`` appears in exactly one
    lane — a newly added, unnamed route fails here."""
    client = _oauth_app()
    live = {(m, path) for path, r in live_api_routes(client.app) for m in (r.methods or set()) if m != "HEAD"}  # type: ignore[arg-type]
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


@pytest.mark.parametrize("path", ["/v1/traces", "/v1/metrics", "/v1/logs"])
def test_the_otlp_receiver_paths_are_never_redirected_to_the_login_bounce(path: str) -> None:
    """The OTLP receivers' paths are OTLP/HTTP's fixed ones under the runner's prefix, outside ``/api`` — the web gate
    exempts them, so a worker's export meets its own token check, not a browser bounce."""
    client = _oauth_app()
    refused = client.post(f"/runners/{_RUNNER_ID}{path}", content=b"", follow_redirects=False)
    assert refused.status_code == 403
    assert client.get(f"/runners/{_RUNNER_ID}/v1/other", follow_redirects=False).status_code == 307
    assert client.post(path, content=b"", follow_redirects=False).status_code == 307


def test_a_worker_heartbeat_under_the_runner_prefix_is_never_redirected_to_the_login_bounce() -> None:
    client = _oauth_app()
    reached = client.post(f"/runners/{_RUNNER_ID}/api/heartbeat", json={}, follow_redirects=False)
    assert reached.status_code not in (307, 401)


def test_a_gated_served_shell_request_to_an_app_with_no_federation_settings_is_503() -> None:
    config = RunnerConfig(root=Path("/tmp/runner-gating-guard"), db_url="sqlite://", hub_url="http://hub.example")
    client = TestClient(create_app(config, hub_http_client=_oauth_hub_client()))
    refused = client.get("/some-page", follow_redirects=False)
    assert refused.status_code == 503
    assert "federation settings" in refused.json()["detail"]


def test_a_store_free_apps_auth_callback_is_503_naming_the_jti_cache() -> None:
    config = RunnerConfig(root=Path("/tmp/runner-gating-guard"), db_url="sqlite://", hub_url="http://hub.example")
    client = TestClient(create_app(config, hub_http_client=_oauth_hub_client(), identity=registered_identity()))
    refused = client.post("/api/auth/callback", content="token=t&state=s")
    assert refused.status_code == 503
    assert "JTI cache" in refused.json()["detail"]
