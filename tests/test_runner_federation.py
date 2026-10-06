"""The runner's SSO federation bounce end to end, over the app's own TestClient — ``GET
/api/auth/login``, ``POST /api/auth/callback``, the three-tenant partition, the authless-under-none
fallback (component tier), and per-request declared-origin selection."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm
from structlog.testing import capture_logs

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.app import create_app
from blizzard.runner.auth.session import CookieNames
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.hub.identity import RunnerIdentityHolder
from blizzard.runner.status.view import RunnerStatusService
from blizzard.runner.store.errors import RunnerStoreConnections
from blizzard.runner.store.internal.jti_cache_store import JtiCacheRepository
from blizzard.runner.store.schema import metadata
from tests.runner_fakes import SqlAlchemyRunnerStore, make_stores, registered_identity, runner_store_errors

pytestmark = pytest.mark.component

_KID = "hub-kid-1"
_RUNNER_ID = "runner-a"
_NAMES = CookieNames(_RUNNER_ID)
_TAILNET = "https://tailnet.example:8431"
_LOOPBACK = "http://127.0.0.1:8431"


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


def _build_app(
    tmp_path: Path,
    *,
    oauth_enabled: bool,
    jwk: dict[str, str] | None = None,
    trusted_proxies: tuple[str, ...] = (),
    client_host: str | None = None,
    base_url: str | None = None,
    extra_public_urls: tuple[str, ...] = (),
    canonical_url: str = "https://runner-a.example",
    runner_id: str = _RUNNER_ID,
    runner_name: str | None = None,
    registered: bool = True,
) -> TestClient:
    name = runner_name or runner_id
    identity = registered_identity(runner_id, name) if registered else RunnerIdentityHolder()
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'runner.db'}")
    metadata.create_all(engine)
    config = RunnerConfig(
        root=tmp_path,
        db_url=f"sqlite:///{tmp_path / 'runner.db'}",
        name=name,
        hub_url="http://hub.example",
        public_urls=tuple(url for url in (canonical_url, *extra_public_urls) if url),
        trusted_proxies=trusted_proxies,
    )
    store = SqlAlchemyRunnerStore(engine, runner_store_errors())
    runner_status = RunnerStatusService(
        clock=SystemClock(),
        pause=store,
        lease_record=store,
        outbound=store,
        environments=store,
        asks=store,
        takeover=store,
        escalations=store,
        identity=identity,
        runner_name=name,
        workspace_id="workspace-1",
        max_agents=1,
        hub_url=config.hub_url,
        env_pool=("e1",),
        workspace_root="",
        harnesses=HarnessRegistry({}),  # unused by the two routes this test drives
    )
    app = create_app(
        config,
        runner_stores=make_stores(store),
        runner_status=runner_status,
        identity=identity,
        hub_http_client=_hub_client(oauth_enabled=oauth_enabled, jwk=jwk),
        jti_cache=JtiCacheRepository(RunnerStoreConnections(engine, runner_store_errors()), SystemClock()),
    )
    kwargs: dict[str, object] = {}
    if client_host is not None:
        kwargs["client"] = (client_host, 41000)
    # The canonical Host, so login bounces rather than rehoming; plain http, so no cookie is Secure.
    kwargs["base_url"] = base_url or "http://runner-a.example"
    return TestClient(app, **kwargs)  # type: ignore[arg-type]


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
    """The worker-hook lane stays reachable with no SSO session even under
    an oauth-mode hub, since workers call it over TCP and cannot SSO-bounce."""
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    assert client.post("/api/heartbeat", json={}).status_code != 401  # reaches the route, not the gate
    assert client.post("/api/leases/l1/asks", json={"question": "?"}).status_code != 401
    assert client.post("/api/leases/l1/attachments", json={"name": "n", "content": "c"}).status_code != 401


def test_the_human_lane_api_is_gated_401_over_tcp_under_oauth(tmp_path: Path) -> None:
    """The panel's own JSON reads are the human web lane: under an oauth-mode hub an
    unauthenticated TCP request is refused with ``401``, not served."""
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    for path in ("/api/facts", "/api/asks", "/api/environments", "/api/runner", "/api/leases"):
        assert client.get(path).status_code == 401, path


def test_the_human_lane_api_is_open_when_the_hub_runs_no_idp_surface(tmp_path: Path) -> None:
    """Under a ``none``-mode hub the runner's human surface is authless: the
    same reads reach their handler, never ``401``."""
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
    assert _NAMES.bounce_state in resp.cookies


def test_the_full_bounce_mints_a_runner_session_and_unlocks_the_web_surface(tmp_path: Path) -> None:
    private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)

    login_resp = client.get("/api/auth/login?return_to=/", follow_redirects=False)
    state = login_resp.cookies[_NAMES.bounce_state]
    client.cookies.set(_NAMES.bounce_state, state)
    client.cookies.set(_NAMES.bounce_return, "/")

    token = _sign(private_key, jti="jti-bounce-1")
    callback_resp = client.post(
        "/api/auth/callback",
        content=f"token={token}&state={state}",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert callback_resp.status_code == 303
    assert callback_resp.headers["location"] == "/"
    assert _NAMES.session in callback_resp.cookies

    client.cookies.set(_NAMES.session, callback_resp.cookies[_NAMES.session])
    gated = client.get("/")
    assert gated.status_code == 200


def test_a_state_mismatch_is_refused(tmp_path: Path) -> None:
    private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    client.cookies.set(_NAMES.bounce_state, "expected-state")
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

    client.cookies.set(_NAMES.bounce_state, "s1")
    first = client.post(
        "/api/auth/callback",
        content=f"token={token}&state=s1",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert first.status_code == 303

    client.cookies.set(_NAMES.bounce_state, "s2")
    second = client.post(
        "/api/auth/callback",
        content=f"token={token}&state=s2",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert second.status_code == 400


def test_a_token_without_exp_is_refused_at_the_callback(tmp_path: Path) -> None:
    """A token carrying no ``exp`` is refused: its ``jti`` would have no retention to hold it
    against a replay."""
    private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    claims = jwt.decode(_sign(private_key, jti="jti-no-exp"), options={"verify_signature": False})
    del claims["exp"]
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": _KID})  # type: ignore[arg-type]

    client.cookies.set(_NAMES.bounce_state, "s1")
    resp = client.post(
        "/api/auth/callback",
        content=f"token={token}&state=s1",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert _NAMES.session not in resp.cookies


def _bounce_in(client: TestClient, private_key: object, *, jti: str, runner_id: str = _RUNNER_ID) -> None:
    """Drive the full SSO bounce so ``client`` holds a live runner session cookie."""
    names = CookieNames(runner_id)
    login_resp = client.get("/api/auth/login?return_to=/", follow_redirects=False)
    state = login_resp.cookies[names.bounce_state]
    client.cookies.set(names.bounce_state, state)
    client.cookies.set(names.bounce_return, "/")
    token = _sign(private_key, jti=jti, aud=runner_id)
    callback_resp = client.post(
        "/api/auth/callback",
        content=f"token={token}&state={state}",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert callback_resp.status_code == 303
    client.cookies.set(names.session, callback_resp.cookies[names.session])


def test_logout_clears_the_session_and_the_next_visit_bounces(tmp_path: Path) -> None:
    """`POST /api/auth/logout` clears the runner session cookie: the served
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
    # The response clears the session cookie (empty value, immediate expiry); mirror that on
    # the test client's jar before the next visit.
    set_cookie = logout_resp.headers["set-cookie"]
    assert f"{_NAMES.session}=" in set_cookie and "Max-Age=0" in set_cookie
    client.cookies.delete(_NAMES.session)

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
    """`GET /api/auth/session` reports the signed-in hub username once a session is established."""
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


# --- forwarded-header trust behind a reverse proxy --------------------

_PROXY_IP = "10.0.0.4"
_DIRECT_IP = "203.0.113.9"


def _bounce_callback(client: TestClient, private_key: object, *, headers: dict[str, str]):
    """Run the SSO callback leg with a valid round-tripped state and token, returning
    the response so a test can inspect the minted session cookie's attributes."""
    client.cookies.set(_NAMES.bounce_state, "s-fwd")
    token = _sign(private_key, jti="jti-fwd-1")
    return client.post(
        "/api/auth/callback",
        content=f"token={token}&state=s-fwd",
        headers={"content-type": "application/x-www-form-urlencoded", **headers},
        follow_redirects=False,
    )


def test_callback_mints_a_secure_cookie_on_forwarded_proto_https_from_a_trusted_proxy(tmp_path: Path) -> None:
    private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk, trusted_proxies=(_PROXY_IP,), client_host=_PROXY_IP)
    resp = _bounce_callback(client, private_key, headers={"x-forwarded-proto": "https"})
    assert resp.status_code == 303
    set_cookie = resp.headers["set-cookie"]
    assert f"{_NAMES.session}=" in set_cookie
    assert "Secure" in set_cookie


def test_callback_ignores_forwarded_proto_from_an_unlisted_peer(tmp_path: Path) -> None:
    private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk, trusted_proxies=(_PROXY_IP,), client_host=_DIRECT_IP)
    resp = _bounce_callback(client, private_key, headers={"x-forwarded-proto": "https"})
    assert resp.status_code == 303
    assert "Secure" not in resp.headers["set-cookie"]


def test_callback_ignores_forwarded_proto_with_no_trusted_proxies_configured(tmp_path: Path) -> None:
    private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk, client_host=_PROXY_IP)  # empty default
    resp = _bounce_callback(client, private_key, headers={"x-forwarded-proto": "https"})
    assert resp.status_code == 303
    assert "Secure" not in resp.headers["set-cookie"]


# The bounce cookies' SameSite/Secure policy, asserted on the Set-Cookie headers.


def _bounce_set_cookie_headers(resp) -> str:
    return " | ".join(v for k, v in resp.headers.items() if k.lower() == "set-cookie")


def test_bounce_cookies_are_samesite_none_secure_on_a_loopback_runner(tmp_path: Path) -> None:
    """A loopback runner's bounce cookies are `SameSite=None` and `Secure`."""
    _private_key, jwk = _keypair()
    client = _build_app(
        tmp_path, oauth_enabled=True, jwk=jwk, extra_public_urls=(_LOOPBACK,), base_url="http://127.0.0.1:8431"
    )
    resp = client.get("/api/auth/login", follow_redirects=False)
    header = _bounce_set_cookie_headers(resp)
    assert _NAMES.bounce_state in header
    assert "samesite=none" in header.lower()
    assert "secure" in header.lower()


def test_bounce_cookies_are_samesite_none_secure_over_https(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk, base_url="https://runner-a.example")
    resp = client.get("/api/auth/login", follow_redirects=False)
    header = _bounce_set_cookie_headers(resp)
    assert "samesite=none" in header.lower()
    assert "secure" in header.lower()


def test_bounce_cookies_stay_lax_on_a_plain_http_non_loopback_runner(tmp_path: Path) -> None:
    """On a plain-http, non-loopback origin the bounce cookies are `SameSite=Lax` with no `Secure`."""
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk, base_url="http://runner-a.example")
    resp = client.get("/api/auth/login", follow_redirects=False)
    header = _bounce_set_cookie_headers(resp).lower()
    assert _NAMES.bounce_state in header
    assert "samesite=lax" in header
    assert "secure" not in header


# --- Multi-origin callback selection ------------------------------


def test_login_presents_the_declared_origin_the_browser_actually_reached(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk, extra_public_urls=(_TAILNET,), base_url=_TAILNET)
    resp = client.get("/api/auth/login", follow_redirects=False)
    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert params["redirect_uri"] == [f"{_TAILNET}/api/auth/callback"]


def test_login_presents_each_declared_origin_to_the_browser_that_reached_it(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    for origin in (_TAILNET, _LOOPBACK, "https://runner-a.example"):
        client = _build_app(
            tmp_path,
            oauth_enabled=True,
            jwk=jwk,
            extra_public_urls=(_TAILNET, _LOOPBACK),
            base_url=origin,
        )
        resp = client.get("/api/auth/login", follow_redirects=False)
        params = parse_qs(urlparse(resp.headers["location"]).query)
        assert params["redirect_uri"] == [f"{origin}/api/auth/callback"], origin


def test_login_rehomes_an_undeclared_loopback_alias_before_setting_bounce_cookies(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(
        tmp_path, oauth_enabled=True, jwk=jwk, extra_public_urls=(_LOOPBACK,), base_url="http://localhost:8431"
    )
    with capture_logs() as logs:
        resp = client.get("/api/auth/login?return_to=/board", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "https://runner-a.example/api/auth/login?return_to=%2Fboard&rehomed=true"
    assert _NAMES.bounce_state not in _bounce_set_cookie_headers(resp)
    warned = [entry for entry in logs if entry["log_level"] == "warning"]
    assert [(entry["arrived_host"], entry["rehoming_to"]) for entry in warned] == [
        ("localhost:8431", "https://runner-a.example")
    ]


def test_the_rehomed_login_bounces_from_the_canonical_origin_with_its_callback(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk, base_url="http://localhost:8431")
    rehome = client.get("/api/auth/login?return_to=/board", follow_redirects=False)
    with capture_logs() as logs:
        resp = client.get(rehome.headers["location"], follow_redirects=False)
    location = urlparse(resp.headers["location"])
    assert location.path == "/api/auth/authorize"
    assert parse_qs(location.query)["redirect_uri"] == ["https://runner-a.example/api/auth/callback"]
    assert _NAMES.bounce_state in _bounce_set_cookie_headers(resp)
    assert not [entry for entry in logs if entry["log_level"] == "warning"]


def test_login_does_not_rehome_when_no_origin_is_declared(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk, canonical_url="", base_url="http://localhost:8431")
    resp = client.get("/api/auth/login", follow_redirects=False)
    assert urlparse(resp.headers["location"]).path == "/api/auth/authorize"


def test_rehoming_carries_only_a_safe_return_to(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk, base_url="http://localhost:8431")
    resp = client.get("/api/auth/login?return_to=//evil.example", follow_redirects=False)
    assert resp.headers["location"] == "https://runner-a.example/api/auth/login?return_to=%2F&rehomed=true"


def test_a_rehomed_login_on_an_unmatched_host_falls_back_to_the_canonical_origin(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(
        tmp_path,
        oauth_enabled=True,
        jwk=jwk,
        extra_public_urls=(_TAILNET,),
        base_url="https://evil.example",
    )
    resp = client.get("/api/auth/login?rehomed=true", follow_redirects=False)
    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert params["redirect_uri"] == ["https://runner-a.example/api/auth/callback"]


def test_a_declared_https_origin_keeps_its_scheme_when_the_proxy_forwards_cleartext(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(
        tmp_path,
        oauth_enabled=True,
        jwk=jwk,
        extra_public_urls=(_TAILNET,),
        base_url="http://tailnet.example:8431",  # cleartext hop from the proxy
    )
    resp = client.get("/api/auth/login", follow_redirects=False)
    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert params["redirect_uri"] == [f"{_TAILNET}/api/auth/callback"]


def test_bounce_cookies_are_samesite_none_secure_on_a_proxied_declared_https_origin(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(
        tmp_path,
        oauth_enabled=True,
        jwk=jwk,
        extra_public_urls=(_TAILNET,),
        base_url="http://tailnet.example:8431",
        trusted_proxies=(_PROXY_IP,),
        client_host=_PROXY_IP,
    )
    resp = client.get("/api/auth/login", follow_redirects=False, headers={"x-forwarded-proto": "https"})
    header = _bounce_set_cookie_headers(resp).lower()
    assert _NAMES.bounce_state in header
    assert "samesite=none" in header
    assert "secure" in header


def test_a_proxied_origin_without_trusted_proxies_still_falls_back_to_lax(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(
        tmp_path,
        oauth_enabled=True,
        jwk=jwk,
        extra_public_urls=(_TAILNET,),
        base_url="http://tailnet.example:8431",
        client_host=_PROXY_IP,  # no trusted_proxies configured
    )
    resp = client.get("/api/auth/login", follow_redirects=False, headers={"x-forwarded-proto": "https"})
    header = _bounce_set_cookie_headers(resp).lower()
    assert "samesite=lax" in header


def test_declaring_no_extra_origins_presents_the_canonical_callback_unchanged(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk)
    resp = client.get("/api/auth/login", follow_redirects=False)
    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert params["redirect_uri"] == ["https://runner-a.example/api/auth/callback"]


def test_a_proxy_that_rewrites_host_falls_back_and_is_not_silent(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(
        tmp_path,
        oauth_enabled=True,
        jwk=jwk,
        extra_public_urls=(_TAILNET,),
        base_url="http://127.0.0.1:8431",  # nginx's default Host: $proxy_host, not the browser's
        trusted_proxies=(_PROXY_IP,),
        client_host=_PROXY_IP,
    )
    with capture_logs() as logs:
        resp = client.get(
            "/api/auth/login?rehomed=true", follow_redirects=False, headers={"x-forwarded-proto": "https"}
        )
    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert params["redirect_uri"] == ["https://runner-a.example/api/auth/callback"]
    warned = [entry for entry in logs if entry["log_level"] == "warning"]
    assert warned
    assert warned[0]["arrived_host"] == "127.0.0.1:8431"
    assert _TAILNET in warned[0]["declared"]


def test_a_runner_id_outside_the_cookie_name_token_set_still_yields_valid_names() -> None:
    names = CookieNames('runner a/1;x=é,"')
    for name in (names.session, names.bounce_state, names.bounce_return):
        assert re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", name)


def test_two_runners_mint_differently_named_session_cookies_and_ignore_each_others(tmp_path: Path) -> None:
    private_key, jwk = _keypair()
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    client_a = _build_app(tmp_path / "a", oauth_enabled=True, jwk=jwk)
    client_b = _build_app(tmp_path / "b", oauth_enabled=True, jwk=jwk, runner_id="runner-b")
    names_b = CookieNames("runner-b")
    assert names_b.session != _NAMES.session

    _bounce_in(client_a, private_key, jti="jti-ns-a")
    cookie_a = next(c.value for c in client_a.cookies.jar if c.name == _NAMES.session)
    # Runner B ignores A's cookie, even replayed under A's name.
    client_b.cookies.set(_NAMES.session, cookie_a)
    assert client_b.get("/api/environments").status_code == 401
    # Nor does A's session value authenticate when presented under B's name.
    client_b.cookies.clear()
    client_b.cookies.set(names_b.session, cookie_a)
    assert client_b.get("/api/environments").status_code == 401


def test_two_same_host_runners_sharing_a_name_stay_signed_in_through_one_cookie_jar(tmp_path: Path) -> None:
    """A browser keys a host's cookies by name alone, ignoring the port: a later cookie of the same
    name replaces the earlier one, so each runner's cookies must be named apart by its id."""
    private_key, jwk = _keypair()
    ids = ("rn_01AAAAAAAAAAAAAAAAAAAAAAAA", "rn_01BBBBBBBBBBBBBBBBBBBBBBBB")
    clients = []
    for runner_id in ids:
        (tmp_path / runner_id).mkdir()
        client = _build_app(
            tmp_path / runner_id, oauth_enabled=True, jwk=jwk, runner_id=runner_id, runner_name="runner-local"
        )
        _bounce_in(client, private_key, jti=f"jti-{runner_id}", runner_id=runner_id)
        clients.append(client)
    jar = {cookie.name: cookie.value for client in clients for cookie in client.cookies.jar}
    for client in clients:
        client.cookies.clear()
        for name, value in jar.items():
            client.cookies.set(name, value)
    assert [client.get("/api/environments").status_code for client in clients] == [200, 200]


def test_before_its_first_registration_a_runner_reads_no_session_and_sign_in_waits(tmp_path: Path) -> None:
    _private_key, jwk = _keypair()
    client = _build_app(tmp_path, oauth_enabled=True, jwk=jwk, registered=False)
    client.cookies.set(_NAMES.session, "any")
    assert client.get("/api/environments").status_code == 401
    assert client.get("/api/auth/login", follow_redirects=False).status_code == 503
    assert client.post("/api/auth/logout").status_code == 204
