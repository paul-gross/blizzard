"""``blizzard hub login``'s wire leg — a real hub + the real stub IdP, no browser
(service tier, issue #96).

The plan's own matrix-gap note: no declared method drives a browserless loopback OAuth
exchange against a running hub. This is that method — mirrors
``tests/service/test_auth_login_service.py``'s own ``_oauth_hub`` fixture and its
"drive the provider dance with ``httpx`` following redirects" shape, then continues
past the hub session into the CLI's own ``client=cli`` authorize branch and
``POST /api/auth/cli/token`` exchange: a scripted "browser" is just the same
``httpx.Client`` (already carrying the hub session cookie from the provider dance)
hitting ``authorize`` with ``follow_redirects=False`` to capture the delivered code,
for both the loopback-redirect form and the paste-code (out-of-band) form. The CLI
never contacts the stub IdP directly — only the hub's own authorize/token routes.

Reproduce — from a provisioned feature env::

    BLIZZARD_SERVICE=1 uv run pytest tests/service/test_cli_login_service.py
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from blizzard.hub.auth.pkce import challenge_from_verifier
from blizzard.hub.config import AuthConfig, HubConfig, OAuthProviderConfig
from tests.e2e.test_acceptance_loop import _await_http, _free_port, _terminate
from tests.service.support import require_stub_idp, service_gate, stub_idp

pytestmark = [pytest.mark.service, service_gate]

_SECRET_ENV = "BZ_OAUTH_TEST_SECRET"
_SECRET = "test-secret"
_OOB_REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"
_VERIFIER = "a-fixed-service-test-verifier-with-plenty-of-entropy"
_CHALLENGE = challenge_from_verifier(_VERIFIER)


@contextlib.contextmanager
def _oauth_hub(hub_dir: Path, port: int, *, providers: tuple[OAuthProviderConfig, ...]) -> Iterator[httpx.Client]:
    """A real ``blizzard hub host`` subprocess, ``auth.mode = "oauth"`` — mirrors
    ``tests/service/test_auth_login_service.py``'s own fixture of the same name."""
    env = {**os.environ, _SECRET_ENV: _SECRET}
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    config = HubConfig.load(hub_dir)
    config = dataclasses.replace(config, auth=AuthConfig(mode="oauth", oauth_providers=providers))
    config.config_path.write_text(config.to_toml())
    proc = subprocess.Popen(
        [hub_bin, "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    try:
        _await_http(proc, client, "/api/health")
        yield client
    finally:
        client.close()
        _terminate(proc)


def _establish_hub_session(hub: httpx.Client, idp_port: int) -> None:
    """The #92 provider dance, driven exactly like ``test_auth_login_service.py``'s
    own scenarios — ``hub`` ends carrying a ``bz_session`` cookie."""
    hub.get("/api/auth/oidc-svc/authorize", follow_redirects=True)
    assert "bz_session" in hub.cookies


def _mint_cli_code(hub: httpx.Client, *, redirect_uri: str) -> tuple[str, str]:
    """Drive ``client=cli`` authorize (as the already-hub-session'd browser would) and
    return ``(code, state)``."""
    state = "svc-outer-state"
    resp = hub.get(
        "/api/auth/authorize",
        params={
            "client": "cli",
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": _CHALLENGE,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    if redirect_uri == _OOB_REDIRECT_URI:
        assert resp.status_code == 200, resp.text
        code = resp.text.split("<pre")[1].split(">", 1)[1].split("<")[0]
        return code, state
    assert resp.status_code == 302, resp.text
    location = urlparse(resp.headers["location"])
    query = parse_qs(location.query)
    assert query["state"] == [state]
    return query["code"][0], state


def test_the_loopback_form_ends_in_a_working_bearer_session(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "5001", "handle": "cli-user", "email": "cli-user@example.com", "email_verified": True},
        )
        provider = OAuthProviderConfig(
            name="oidc-svc",
            type="oidc",
            display_name="Stub SSO",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            issuer=f"http://127.0.0.1:{idp_port}",
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,)) as hub:
            _establish_hub_session(hub, idp_port)

            redirect_uri = "http://127.0.0.1:54123/callback"
            code, _state = _mint_cli_code(hub, redirect_uri=redirect_uri)

            token_resp = hub.post(
                "/api/auth/cli/token",
                json={"code": code, "code_verifier": _VERIFIER, "redirect_uri": redirect_uri},
            )
            assert token_resp.status_code == 200, token_resp.text
            token = token_resp.json()["token"]
            assert token

            # A bare bearer client, no cookie jar at all — proves the token alone
            # authenticates, exactly what `blizzard hub status` would send.
            bearer_only = httpx.Client(base_url=hub.base_url, timeout=15.0)
            try:
                me = bearer_only.get("/api/me", headers={"Authorization": f"Bearer {token}"})
                assert me.status_code == 200, me.text
                assert me.json()["username"] == "cli-user"
            finally:
                bearer_only.close()


def test_logout_revokes_the_bearer_session_server_side(tmp_path: Path) -> None:
    """AC 4's server-side half over the real wire: after a working bearer session is
    minted, ``POST /api/auth/logout`` presenting that same bearer (the CLI's own
    ``blizzard hub logout`` path — no cookie jar) must make the session stop resolving
    at the hub, not merely disappear from the local ``sessions.json``. Proven by a
    fresh bearer-only client whose ``GET /api/me`` succeeds, then 401s once the bearer
    is logged out."""
    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={
                "subject": "5003",
                "handle": "cli-logout-user",
                "email": "logout@example.com",
                "email_verified": True,
            },
        )
        provider = OAuthProviderConfig(
            name="oidc-svc",
            type="oidc",
            display_name="Stub SSO",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            issuer=f"http://127.0.0.1:{idp_port}",
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,)) as hub:
            _establish_hub_session(hub, idp_port)

            redirect_uri = "http://127.0.0.1:54124/callback"
            code, _state = _mint_cli_code(hub, redirect_uri=redirect_uri)
            token = hub.post(
                "/api/auth/cli/token",
                json={"code": code, "code_verifier": _VERIFIER, "redirect_uri": redirect_uri},
            ).json()["token"]

            # A bare bearer client — the CLI's own shape, no cookie jar at all.
            bearer_only = httpx.Client(base_url=hub.base_url, timeout=15.0)
            try:
                auth = {"Authorization": f"Bearer {token}"}
                assert bearer_only.get("/api/me", headers=auth).status_code == 200

                # `blizzard hub logout` presents the bearer to the revoke route.
                assert bearer_only.post("/api/auth/logout", headers=auth).status_code == 204

                # The revoked session no longer resolves server-side — a raw retry 401s
                # even though the token bytes are unchanged.
                assert bearer_only.get("/api/me", headers=auth).status_code == 401
            finally:
                bearer_only.close()


def test_the_paste_code_form_completes_the_same_login_with_no_loopback_listener(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "5002", "handle": "cli-paste-user", "email": "paste@example.com", "email_verified": True},
        )
        provider = OAuthProviderConfig(
            name="oidc-svc",
            type="oidc",
            display_name="Stub SSO",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            issuer=f"http://127.0.0.1:{idp_port}",
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,)) as hub:
            _establish_hub_session(hub, idp_port)

            # No loopback listener anywhere in this test — the out-of-band redirect
            # form renders a paste-able code instead of a 302.
            code, _state = _mint_cli_code(hub, redirect_uri=_OOB_REDIRECT_URI)

            token_resp = hub.post(
                "/api/auth/cli/token",
                json={"code": code, "code_verifier": _VERIFIER, "redirect_uri": _OOB_REDIRECT_URI},
            )
            assert token_resp.status_code == 200, token_resp.text
            token = token_resp.json()["token"]

            bearer_only = httpx.Client(base_url=hub.base_url, timeout=15.0)
            try:
                me = bearer_only.get("/api/me", headers={"Authorization": f"Bearer {token}"})
                assert me.status_code == 200, me.text
                assert me.json()["username"] == "cli-paste-user"
            finally:
                bearer_only.close()
