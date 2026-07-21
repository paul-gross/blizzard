"""``GET /api/auth/authorize`` / ``GET /api/auth/jwks.json`` / ``POST
/api/auth/rotate-signing-key`` — the hub-as-IdP surface (component tier, issue #95).
"""

from __future__ import annotations

import re
from pathlib import Path

import jwt
import pytest
from jwt.algorithms import RSAAlgorithm

from blizzard.auth_core import Role
from blizzard.hub.config import AUTH_MODE_NONE, AUTH_MODE_OAUTH
from tests.support import HubHarness, build_hub, seed_session, seed_user

pytestmark = pytest.mark.component

_DEFAULT_REDIRECT_URIS = ("https://runner-a.example/api/auth/callback",)


def _register_runner(
    hub: HubHarness, *, runner_id: str = "runner-a", redirect_uris: tuple[str, ...] = _DEFAULT_REDIRECT_URIS
) -> None:
    hub.services.fleet.register(
        runner_id, "workspace-1", public_url="https://runner-a.example", redirect_uris=redirect_uris
    )


def test_jwks_404s_under_none_mode(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_NONE)
    resp = hub.client.get("/api/auth/jwks.json")
    assert resp.status_code == 404


def test_authorize_404s_under_none_mode(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_NONE)
    resp = hub.client.get(
        "/api/auth/authorize", params={"client": "runner-a", "redirect_uri": "https://x/callback", "state": "s"}
    )
    assert resp.status_code == 404


def test_jwks_publishes_a_key_under_oauth_mode(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    resp = hub.client.get("/api/auth/jwks.json")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["keys"]) == 1
    assert body["keys"][0]["kid"]


def test_authorize_rejects_unknown_client(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    user = seed_user(hub, username="alice", role=Role.CONTRIBUTOR, email="alice@example.com")
    session = seed_session(hub, user)
    hub.client.cookies.set("bz_session", session)
    resp = hub.client.get(
        "/api/auth/authorize",
        params={"client": "no-such-runner", "redirect_uri": "https://x/callback", "state": "s"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_authorize_rejects_unregistered_redirect_uri(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _register_runner(hub)
    user = seed_user(hub, username="alice", role=Role.CONTRIBUTOR, email="alice@example.com")
    session = seed_session(hub, user)
    hub.client.cookies.set("bz_session", session)
    resp = hub.client.get(
        "/api/auth/authorize",
        params={"client": "runner-a", "redirect_uri": "https://evil.example/callback", "state": "s"},
        follow_redirects=False,
    )
    assert resp.status_code == 400


def test_authorize_with_session_delivers_a_signed_jwt_via_form_post(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _register_runner(hub)
    user = seed_user(hub, username="alice", role=Role.CONTRIBUTOR, email="alice@example.com")
    session = seed_session(hub, user)
    hub.client.cookies.set("bz_session", session)

    resp = hub.client.get(
        "/api/auth/authorize",
        params={
            "client": "runner-a",
            "redirect_uri": "https://runner-a.example/api/auth/callback",
            "state": "opaque-state",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 200
    body = resp.text
    assert 'action="https://runner-a.example/api/auth/callback"' in body
    assert 'name="state" value="opaque-state"' in body

    token_match = re.search(r'name="token" value="([^"]+)"', body)
    assert token_match is not None
    token = token_match.group(1)

    jwks = hub.client.get("/api/auth/jwks.json").json()
    header = jwt.get_unverified_header(token)
    key_jwk = next(k for k in jwks["keys"] if k["kid"] == header["kid"])
    public_key = RSAAlgorithm.from_jwk(key_jwk)  # type: ignore[arg-type]
    claims = jwt.decode(
        token,
        key=public_key,  # type: ignore[arg-type]
        algorithms=["RS256"],
        audience="runner-a",
        options={"verify_exp": False},
    )
    assert claims["sub"] == user.user_id
    assert claims["username"] == "alice"
    assert claims["email"] == "alice@example.com"
    assert claims["role"] == "contributor"
    assert claims["aud"] == "runner-a"
    assert claims["jti"]
    assert claims["exp"] - claims["iat"] <= 60


def test_authorize_with_no_session_bounces_into_the_single_configured_provider(tmp_path: Path) -> None:
    from tests.test_auth_login_api import FakeOAuthProvider

    provider = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH, oauth_providers={"github": provider})
    _register_runner(hub)

    resp = hub.client.get(
        "/api/auth/authorize",
        params={"client": "runner-a", "redirect_uri": "https://runner-a.example/api/auth/callback", "state": "s"},
        follow_redirects=False,
    )
    assert resp.status_code == 307 or resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith("/api/auth/github/authorize?return_to=")
    assert "%2Fapi%2Fauth%2Fauthorize" in location


def test_authorize_with_no_session_and_multiple_providers_refuses(tmp_path: Path) -> None:
    from tests.test_auth_login_api import FakeOAuthProvider

    hub = build_hub(
        tmp_path,
        auth_mode=AUTH_MODE_OAUTH,
        oauth_providers={"github": FakeOAuthProvider(name="github"), "oidc": FakeOAuthProvider(name="oidc")},
    )
    _register_runner(hub)
    resp = hub.client.get(
        "/api/auth/authorize",
        params={"client": "runner-a", "redirect_uri": "https://runner-a.example/api/auth/callback", "state": "s"},
        follow_redirects=False,
    )
    assert resp.status_code == 501


def test_rotate_signing_key_requires_user_manage(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    guest = seed_user(hub, username="guest1", role=Role.GUEST, email="guest1@example.com")
    session = seed_session(hub, guest)
    hub.client.cookies.set("bz_session", session)
    resp = hub.client.post("/api/auth/rotate-signing-key")
    assert resp.status_code == 403


def test_rotate_signing_key_rotates_the_published_jwks(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    admin = seed_user(hub, username="admin1", role=Role.ADMIN, email="admin1@example.com")
    session = seed_session(hub, admin)
    hub.client.cookies.set("bz_session", session)

    before = {k["kid"] for k in hub.client.get("/api/auth/jwks.json").json()["keys"]}
    resp = hub.client.post("/api/auth/rotate-signing-key")
    assert resp.status_code == 204
    after = {k["kid"] for k in hub.client.get("/api/auth/jwks.json").json()["keys"]}
    assert len(after) == 2
    assert before <= after
