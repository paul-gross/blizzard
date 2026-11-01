"""``GET /api/auth/authorize`` / ``GET /api/auth/jwks.json`` / ``POST
/api/auth/rotate-signing-key`` — the hub-as-IdP surface (component tier).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import jwt
import pytest
from httpx2 import Response
from jwt.algorithms import RSAAlgorithm

from blizzard.auth_core import Role
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.auth.models import User
from blizzard.hub.config import AUTH_MODE_NONE, AUTH_MODE_OAUTH
from blizzard.hub.domain.runners.host import HostAddition, HostLifecycleFact, HostReport, IWriteHostRegistry
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry
from tests.support import (
    HubHarness,
    TenantHarness,
    added_runner,
    build_hub,
    default_tenant,
    seed_membership,
    seed_runner,
    seed_session,
    seed_user,
)

pytestmark = pytest.mark.component

_DEFAULT_REDIRECT_URIS = ("https://runner-a.example/api/auth/callback",)


def _register_runner(
    hub: HubHarness, *, runner_id: str = "runner-a", redirect_uris: tuple[str, ...] = _DEFAULT_REDIRECT_URIS
) -> None:
    seed_runner(hub, runner_id, register=False)
    hub.services.fleet.register(
        added_runner(hub, runner_id), "workspace-1", public_url="https://runner-a.example", redirect_uris=redirect_uris
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
    public_key = RSAAlgorithm.from_jwk(key_jwk)  #
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


def test_authorize_with_no_session_and_multiple_providers_bounces_to_the_login_chooser(tmp_path: Path) -> None:
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
    # Two providers: no single dance to auto-run, so the browser is handed to the
    # board's /login page carrying the pending authorize request.
    assert resp.status_code in (302, 307)
    location = resp.headers["location"]
    assert location.startswith("/login?return_to=")
    assert "%2Fapi%2Fauth%2Fauthorize" in location
    assert "client%3Drunner-a" in location
    assert "state%3Ds" in location


def test_authorize_with_no_session_and_no_providers_refuses(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH, oauth_providers={})
    _register_runner(hub)
    resp = hub.client.get(
        "/api/auth/authorize",
        params={"client": "runner-a", "redirect_uri": "https://runner-a.example/api/auth/callback", "state": "s"},
        follow_redirects=False,
    )
    # Zero configured providers: nothing to authenticate against, so the refusal stays
    # an actionable error rather than a chooser bounce.
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
    admin = seed_user(hub, username="admin1", role=Role.SUPERUSER, email="admin1@example.com")
    session = seed_session(hub, admin)
    hub.client.cookies.set("bz_session", session)

    before = {k["kid"] for k in hub.client.get("/api/auth/jwks.json").json()["keys"]}
    resp = hub.client.post("/api/auth/rotate-signing-key")
    assert resp.status_code == 204
    after = {k["kid"] for k in hub.client.get("/api/auth/jwks.json").json()["keys"]}
    assert len(after) == 2
    assert before <= after


def test_authorize_refuses_a_retired_runner_distinctly_only_past_the_redirect_match(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _register_runner(hub)
    user = seed_user(hub, username="alice", role=Role.CONTRIBUTOR, email="alice@example.com")
    hub.client.cookies.set("bz_session", seed_session(hub, user))
    registered = _DEFAULT_REDIRECT_URIS[0]

    def authorize(client: str, redirect_uri: str = registered) -> tuple[int, dict]:
        resp = hub.client.get(
            "/api/auth/authorize",
            params={"client": client, "redirect_uri": redirect_uri, "state": "s"},
            follow_redirects=False,
        )
        return resp.status_code, resp.json() if resp.status_code in (400, 403) else {}

    assert authorize("runner-a")[0] not in (400, 403)
    writer = cast(IWriteRunnerRegistry, hub.services.registry)
    writer.record_lifecycle("runner-a", retired=True, at=hub.clock.now(), by="op")

    status_code, body = authorize("runner-a")
    assert status_code == 403
    assert "retired" in body["detail"]
    unknown = authorize("no-such-runner")
    assert unknown[0] == 400
    assert authorize("runner-a", "https://elsewhere.example/cb") == unknown

    writer.record_lifecycle("runner-a", retired=False, at=hub.clock.now(), by="op")
    assert authorize("runner-a")[0] not in (400, 403)


_BRAVO_RUNNER = "bravo-runner"
_BRAVO_REDIRECT = "https://bravo-runner.example/api/auth/callback"


def _bravo(hub: HubHarness) -> TenantHarness:
    """A second tenant, ``bravo``, holding the registered runner ``_BRAVO_RUNNER``."""
    bravo = hub.add_tenant("bravo")
    seed_runner(bravo.services, _BRAVO_RUNNER, register=False)
    bravo.services.fleet.register(
        added_runner(bravo.services, _BRAVO_RUNNER),
        "workspace-1",
        public_url="https://bravo-runner.example",
        redirect_uris=(_BRAVO_REDIRECT,),
    )
    return bravo


def _sign_in(hub: HubHarness, user: User, client: str, redirect_uri: str, tenant: str | None = None) -> Response:
    headers = {"Cookie": f"bz_session={seed_session(hub, user)}"}
    if tenant is not None:
        headers[TENANT_HEADER] = tenant
    return hub.client.get(
        "/api/auth/authorize",
        params={"client": client, "redirect_uri": redirect_uri, "state": "s"},
        headers=headers,
        follow_redirects=False,
    )


def _claims(hub: HubHarness, resp: Response, audience: str) -> dict[str, object]:
    assert resp.status_code == 200, resp.text
    token_match = re.search(r'name="token" value="([^"]+)"', resp.text)
    assert token_match is not None
    jwks = hub.client.get("/api/auth/jwks.json").json()
    kid = jwt.get_unverified_header(token_match.group(1))["kid"]
    public_key = RSAAlgorithm.from_jwk(next(k for k in jwks["keys"] if k["kid"] == kid))  #
    return jwt.decode(
        token_match.group(1),
        key=public_key,  # type: ignore[arg-type]
        algorithms=["RS256"],
        audience=audience,
        options={"verify_exp": False},
    )


def test_a_person_with_no_membership_in_the_runners_tenant_is_answered_as_an_unknown_client(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _bravo(hub)
    alice = seed_user(hub, username="alice", role=Role.ADMIN, email="alice@example.com")
    root = seed_user(hub, username="root", role=Role.SUPERUSER, email="root@example.com")

    unknown = _sign_in(hub, alice, "no-such-runner", _BRAVO_REDIRECT)
    unregistered = _sign_in(hub, alice, _BRAVO_RUNNER, "https://elsewhere.example/cb")
    not_a_member = _sign_in(hub, alice, _BRAVO_RUNNER, _BRAVO_REDIRECT)
    hub_admin = _sign_in(hub, root, _BRAVO_RUNNER, _BRAVO_REDIRECT)

    answers = {(r.status_code, r.text) for r in (unknown, unregistered, not_a_member, hub_admin)}
    assert answers == {(400, unknown.text)}


def test_the_runners_tenant_settles_the_sign_in_and_its_membership_role_is_the_tokens(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    bravo = _bravo(hub)
    alice = seed_user(hub, username="alice", role=Role.ADMIN, email="alice@example.com")
    seed_membership(hub.engine, bravo.tenant, alice.user_id, Role.GUEST)

    for named in (None, default_tenant(hub.engine), bravo.tenant):
        claims = _claims(hub, _sign_in(hub, alice, _BRAVO_RUNNER, _BRAVO_REDIRECT, named), _BRAVO_RUNNER)
        assert claims["role"] == "guest"
        assert set(claims) == {"sub", "username", "email", "role", "aud", "jti", "iat", "exp"}
        assert (claims["sub"], claims["username"], claims["email"], claims["aud"]) == (
            alice.user_id,
            "alice",
            "alice@example.com",
            _BRAVO_RUNNER,
        )


def test_a_retired_runner_is_told_apart_only_to_a_member_of_its_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    bravo = _bravo(hub)
    member = seed_user(hub, username="member", role=Role.GUEST, email="member@example.com")
    seed_membership(hub.engine, bravo.tenant, member.user_id, Role.GUEST)
    stranger = seed_user(hub, username="stranger", role=Role.ADMIN, email="stranger@example.com")
    writer = cast(IWriteRunnerRegistry, bravo.services.registry)
    writer.record_lifecycle(_BRAVO_RUNNER, retired=True, at=hub.clock.now(), by="op")

    assert _sign_in(hub, member, _BRAVO_RUNNER, _BRAVO_REDIRECT).status_code == 403
    assert _sign_in(hub, stranger, _BRAVO_RUNNER, _BRAVO_REDIRECT).status_code == 400


_HOST_ID = "hs_bravo"
_HOST_REDIRECT = "https://host.example/api/auth/callback"


def _bravo_host(hub: HubHarness, bravo: TenantHarness) -> IWriteHostRegistry:
    """The host ``_HOST_ID`` in ``bravo``, having reported ``_HOST_REDIRECT``."""
    hosts = cast(IWriteHostRegistry, bravo.services.hosts)
    hosts.add(HostAddition(_HOST_ID, "host-bravo", "host-hash", at=hub.clock.now(), by="op"))
    report = HostReport(
        name=None, url="https://host.example", redirect_uris=(_HOST_REDIRECT,), workspaces=(), subscriptions=()
    )
    hosts.record_report(_HOST_ID, report, name=None, at=hub.clock.now())
    return hosts


def test_a_host_is_a_client_whose_token_audience_is_the_host_and_role_the_hosts_tenant_membership(
    tmp_path: Path,
) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    bravo = hub.add_tenant("bravo")
    _bravo_host(hub, bravo)
    alice = seed_user(hub, username="alice", role=Role.ADMIN, email="alice@example.com")
    seed_membership(hub.engine, bravo.tenant, alice.user_id, Role.GUEST)

    claims = _claims(hub, _sign_in(hub, alice, _HOST_ID, _HOST_REDIRECT), _HOST_ID)

    assert (claims["aud"], claims["role"], claims["sub"]) == (_HOST_ID, "guest", alice.user_id)


def test_a_host_sign_in_is_refused_as_a_runners_is(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    bravo = hub.add_tenant("bravo")
    hosts = _bravo_host(hub, bravo)
    member = seed_user(hub, username="member", role=Role.GUEST, email="member@example.com")
    seed_membership(hub.engine, bravo.tenant, member.user_id, Role.GUEST)
    stranger = seed_user(hub, username="stranger", role=Role.ADMIN, email="stranger@example.com")

    unknown = _sign_in(hub, member, "hs_ghost", _HOST_REDIRECT)
    unreported = _sign_in(hub, member, _HOST_ID, "https://elsewhere.example/cb")
    not_a_member = _sign_in(hub, stranger, _HOST_ID, _HOST_REDIRECT)
    assert {(r.status_code, r.text) for r in (unknown, unreported, not_a_member)} == {(400, unknown.text)}

    hosts.record_retirement(
        _HOST_ID, fact=HostLifecycleFact(_HOST_ID, True, hub.clock.now(), "op"), at=hub.clock.now(), by="op"
    )

    retired = _sign_in(hub, member, _HOST_ID, _HOST_REDIRECT)
    assert retired.status_code == 403
    assert "retired" in retired.json()["detail"]
    assert _sign_in(hub, member, _HOST_ID, "https://elsewhere.example/cb").text == unknown.text
    assert _sign_in(hub, stranger, _HOST_ID, _HOST_REDIRECT).text == unknown.text
