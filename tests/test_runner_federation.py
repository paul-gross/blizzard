"""The runner's SSO federation bounce end to end, over the app's own TestClient —
``GET /api/auth/login``, ``POST /api/auth/callback``, the three-tenant partition, and
the authless-under-none fallback (component tier, issue #95).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.app import create_app
from blizzard.runner.auth.internal.jti_cache_repository import JtiCacheRepository
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.schema import metadata

pytestmark = pytest.mark.component

_KID = "hub-kid-1"
_RUNNER_ID = "runner-a"


def _keypair() -> tuple[object, dict[str, str]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = _KID
    return private_key, jwk


def _sign(private_key: object, **claim_overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "usr_1",
        "username": "alice",
        "email": "alice@example.com",
        "role": "contributor",
        "aud": _RUNNER_ID,
        "jti": "jti-1",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": _KID})  # type: ignore[arg-type]


def _hub_client(*, oauth_enabled: bool, jwk: dict[str, str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/auth/jwks.json"
        if not oauth_enabled:
            return httpx.Response(404)
        return httpx.Response(200, json={"keys": [jwk]})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://hub.example")


def _build_app(tmp_path: Path, *, oauth_enabled: bool, jwk: dict[str, str] | None = None) -> TestClient:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'runner.db'}")
    metadata.create_all(engine)
    config = RunnerConfig(
        root=tmp_path,
        db_url=f"sqlite:///{tmp_path / 'runner.db'}",
        runner_id=_RUNNER_ID,
        hub_url="http://hub.example",
        public_url="https://runner-a.example",
    )
    store = SqlAlchemyRunnerStore(engine)
    runner_status = RunnerStatusService(
        store=store,
        clock=SystemClock(),
        harness=None,  # type: ignore[arg-type]  # unused by the two routes this test drives
        runner_id=_RUNNER_ID,
        workspace_id="workspace-1",
        max_agents=1,
        hub_url=config.hub_url,
        env_pool=("e1",),
    )
    app = create_app(
        config,
        runner_store=store,
        runner_status=runner_status,
        hub_http_client=_hub_client(oauth_enabled=oauth_enabled, jwk=jwk),
        jti_cache=JtiCacheRepository(engine),
    )
    return TestClient(app)


def test_the_web_surface_is_reachable_with_no_session_when_the_hub_runs_no_idp_surface(tmp_path: Path) -> None:
    client = _build_app(tmp_path, oauth_enabled=False)
    resp = client.get("/")
    assert resp.status_code == 200


def test_the_web_surface_bounces_to_login_when_the_hub_runs_an_idp_surface(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 307 or resp.status_code == 302
    assert resp.headers["location"].startswith("/api/auth/login?return_to=")


def test_the_worker_hook_lane_stays_ungated_over_tcp_even_with_the_idp_surface_active(tmp_path: Path) -> None:
    """The three-tenant partition (issue #95): the **worker-hook lane** stays reachable
    with no SSO session even under an oauth-mode hub — workers call these over TCP via
    ``BLIZZARD_RUNNER_URL`` and cannot SSO-bounce. Reaching the route (a ``2xx``/``422``/
    ``404``/``503``, never the gate's ``401``) is the property under test."""
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    assert client.post("/api/heartbeat", json={}).status_code != 401  # reaches the route, not the gate
    assert client.post("/api/leases/l1/asks", json={"question": "?"}).status_code != 401
    assert client.post("/api/leases/l1/attachments", json={"name": "n", "content": "c"}).status_code != 401


def test_the_human_lane_api_is_gated_401_over_tcp_under_oauth(tmp_path: Path) -> None:
    """The panel's own JSON reads (``runner/status`` open-asks, leases, environments, the
    runner status view, the fact ledger) are the **human web lane**: under an oauth-mode
    hub an unauthenticated TCP request is refused with ``401``, not served — a
    session-gated HTML shell over an ungated JSON API would be no gated surface at all
    (any browser or curl would read it). The exhaustive per-route split is
    ``tests/test_runner_route_gating.py``; this pins the representative reads end to end
    through the app the bounce mints a session for."""
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    for path in ("/api/facts", "/api/asks", "/api/environments", "/api/runner", "/api/leases"):
        assert client.get(path).status_code == 401, path


def test_the_human_lane_api_is_open_when_the_hub_runs_no_idp_surface(tmp_path: Path) -> None:
    """Under a ``none``-mode hub the runner's human surface is authless (issue #95): the
    same reads reach their handler (never ``401``), preserving today's fully-unauthed
    behaviour so existing none-mode contracts stay intact."""
    client = _build_app(tmp_path, oauth_enabled=False)
    for path in ("/api/facts", "/api/asks", "/api/environments", "/api/runner", "/api/leases"):
        assert client.get(path).status_code != 401, path


def test_login_redirects_to_the_hub_authorize_endpoint_with_this_runners_own_callback(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    resp = client.get("/api/auth/login?return_to=/api/environments", follow_redirects=False)
    assert resp.status_code == 307 or resp.status_code == 302
    location = urlparse(resp.headers["location"])
    assert location.path == "/api/auth/authorize"
    params = parse_qs(location.query)
    assert params["client"] == [_RUNNER_ID]
    assert params["redirect_uri"] == ["https://runner-a.example/api/auth/callback"]
    assert params["response_mode"] == ["form_post"]
    assert "bz_runner_bounce_state" in resp.cookies


def test_the_full_bounce_mints_a_runner_session_and_unlocks_the_web_surface(tmp_path: Path) -> None:
    private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)

    login_resp = client.get("/api/auth/login?return_to=/", follow_redirects=False)
    state = login_resp.cookies["bz_runner_bounce_state"]
    client.cookies.set("bz_runner_bounce_state", state)
    client.cookies.set("bz_runner_bounce_return", "/")

    token = _sign(private_key, jti="jti-bounce-1")
    callback_resp = client.post(
        "/api/auth/callback",
        content=f"token={token}&state={state}",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert callback_resp.status_code == 303
    assert callback_resp.headers["location"] == "/"
    assert "bz_runner_session" in callback_resp.cookies

    client.cookies.set("bz_runner_session", callback_resp.cookies["bz_runner_session"])
    gated = client.get("/")
    assert gated.status_code == 200


def test_a_state_mismatch_is_refused(tmp_path: Path) -> None:
    private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    client.cookies.set("bz_runner_bounce_state", "expected-state")
    token = _sign(private_key)
    resp = client.post(
        "/api/auth/callback",
        content=f"token={token}&state=wrong-state",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 400


def test_a_replayed_jti_is_refused_at_the_callback(tmp_path: Path) -> None:
    private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    token = _sign(private_key, jti="jti-replay-1")

    client.cookies.set("bz_runner_bounce_state", "s1")
    first = client.post(
        "/api/auth/callback",
        content=f"token={token}&state=s1",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert first.status_code == 303

    client.cookies.set("bz_runner_bounce_state", "s2")
    second = client.post(
        "/api/auth/callback",
        content=f"token={token}&state=s2",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert second.status_code == 400


def _bounce_in(client: TestClient, private_key: object, *, jti: str) -> None:
    """Drive the full SSO bounce so ``client`` holds a live runner session cookie."""
    login_resp = client.get("/api/auth/login?return_to=/", follow_redirects=False)
    state = login_resp.cookies["bz_runner_bounce_state"]
    client.cookies.set("bz_runner_bounce_state", state)
    client.cookies.set("bz_runner_bounce_return", "/")
    token = _sign(private_key, jti=jti)
    callback_resp = client.post(
        "/api/auth/callback",
        content=f"token={token}&state={state}",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert callback_resp.status_code == 303
    client.cookies.set("bz_runner_session", callback_resp.cookies["bz_runner_session"])


def test_logout_clears_the_session_and_the_next_visit_bounces(tmp_path: Path) -> None:
    """`POST /api/auth/logout` clears the runner session cookie (issue #129): the served
    surface, reachable while the session was live, bounces to `GET /api/auth/login`
    again on the next visit, and the panel's JSON reads `401`."""
    private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    _bounce_in(client, private_key, jti="jti-logout-1")

    # The session is live: the served shell renders and the JSON API answers.
    assert client.get("/").status_code == 200
    assert client.get("/api/environments").status_code != 401

    logout_resp = client.post("/api/auth/logout")
    assert logout_resp.status_code == 204
    # The response clears the session cookie (empty value, immediate expiry) — a browser
    # drops it, so model that on the jar before the next visit.
    set_cookie = logout_resp.headers["set-cookie"]
    assert "bz_runner_session=" in set_cookie and "Max-Age=0" in set_cookie
    client.cookies.delete("bz_runner_session")

    bounce = client.get("/", follow_redirects=False)
    assert bounce.status_code in (302, 307)
    assert bounce.headers["location"].startswith("/api/auth/login?return_to=")
    assert client.get("/api/environments").status_code == 401


def test_logout_is_a_harmless_no_op_without_a_session(tmp_path: Path) -> None:
    """Logout cannot itself require a live session — clearing an absent cookie is a
    204 no-op, mirroring the hub's own public logout."""
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    assert client.post("/api/auth/logout").status_code == 204


def test_session_read_reports_the_signed_in_username_under_oauth(tmp_path: Path) -> None:
    """`GET /api/auth/session` carries the hub username behind the panel's identity/
    logout control (issue #129) once a session is established."""
    private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    _bounce_in(client, private_key, jti="jti-session-1")

    resp = client.get("/api/auth/session")
    assert resp.status_code == 200
    assert resp.json() == {"auth_enabled": True, "username": "alice"}


def test_session_read_reports_no_username_without_a_session_under_oauth(tmp_path: Path) -> None:
    """Self-resolving, never `401`: under oauth with no session the read still answers
    200, reporting the surface is gated (`auth_enabled`) but no one is signed in."""
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    resp = client.get("/api/auth/session")
    assert resp.status_code == 200
    assert resp.json() == {"auth_enabled": True, "username": None}


def test_session_read_reports_authless_under_a_none_mode_hub(tmp_path: Path) -> None:
    """Under a `none`-mode hub the surface is authless — `auth_enabled` false — so the
    panel renders neither the username nor the logout control."""
    client = _build_app(tmp_path, oauth_enabled=False)
    resp = client.get("/api/auth/session")
    assert resp.status_code == 200
    assert resp.json() == {"auth_enabled": False, "username": None}
