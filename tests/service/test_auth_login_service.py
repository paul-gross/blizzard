"""OAuth provider login — the real hub against the real stub IdP (service tier, issue #92).

The **matrix gap** the plan names: no declared method drives a real HTTP
authorize->callback exchange. This is that method — a running hub (real subprocess,
``auth.mode = "oauth"``) whose ``authorize`` 302s to the ``blizzard-mock`` stub IdP
(a real subprocess, both provider shapes), whose ``callback`` exchanges the stub's
code, ending in a resolving session cookie and a working ``GET /api/me`` — for both the
``oidc`` and ``github`` conformers, over the real wire, no tokens hard-coded, no network
beyond the two local subprocesses.

Reproduce — from a provisioned feature env::

    BLIZZARD_SERVICE=1 uv run pytest tests/service/test_auth_login_service.py
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from blizzard.hub.config import AuthConfig, HubConfig, OAuthProviderConfig
from tests.e2e.test_acceptance_loop import _await_http, _free_port, _terminate
from tests.service.support import require_stub_idp, service_gate, stub_idp

pytestmark = [pytest.mark.service, service_gate]

_SECRET_ENV = "BZ_OAUTH_TEST_SECRET"
_SECRET = "test-secret"


@contextlib.contextmanager
def _oauth_hub(hub_dir: Path, port: int, *, providers: tuple[OAuthProviderConfig, ...]) -> Iterator[httpx.Client]:
    """A real ``blizzard hub host`` subprocess, ``auth.mode = "oauth"`` with one or more
    configured providers — mirrors ``tests/e2e/test_acceptance_loop.py``'s own ``_hub``,
    minus the PM-source wiring this scenario does not need (login/`` /api/me`` alone)."""
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


def test_oidc_login_dance_against_the_stub_idp_ends_authenticated(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "1001", "handle": "octocat", "email": "octocat@example.com", "email_verified": True},
        )
        provider = OAuthProviderConfig(
            name="oidc-co",
            type="oidc",
            display_name="Stub SSO",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            issuer=f"http://127.0.0.1:{idp_port}",
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,)) as hub:
            providers_resp = hub.get("/api/auth/providers")
            assert providers_resp.status_code == 200
            assert providers_resp.json() == [{"name": "oidc-co", "display_name": "Stub SSO", "type": "oidc"}]

            hub.get("/api/auth/oidc-co/authorize", follow_redirects=True)
            assert "bz_session" in hub.cookies

            me_resp = hub.get("/api/me")
            assert me_resp.status_code == 200
            body = me_resp.json()
            assert body["username"] == "octocat"
            assert body["role"] == "guest"


def test_github_login_dance_against_the_stub_idp_ends_authenticated(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "42", "handle": "hubbernetes", "email": "hub@example.com", "email_verified": True},
        )
        provider = OAuthProviderConfig(
            name="github",
            type="github",
            display_name="GitHub",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            api_base=f"http://127.0.0.1:{idp_port}",
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,)) as hub:
            hub.get("/api/auth/github/authorize", follow_redirects=True)
            assert "bz_session" in hub.cookies

            me_resp = hub.get("/api/me")
            assert me_resp.status_code == 200
            assert me_resp.json()["username"] == "hubbernetes"


def test_login_then_logout_round_trips_over_the_wire(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "1001", "handle": "octocat", "email": "octocat@example.com", "email_verified": True},
        )
        provider = OAuthProviderConfig(
            name="oidc-co",
            type="oidc",
            display_name="Stub SSO",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            issuer=f"http://127.0.0.1:{idp_port}",
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,)) as hub:
            hub.get("/api/auth/oidc-co/authorize", follow_redirects=True)
            assert hub.get("/api/me").status_code == 200

            logout_resp = hub.post("/api/auth/logout")
            assert logout_resp.status_code == 204

            assert hub.get("/api/me").status_code == 401


def test_providers_endpoint_lists_both_configured_providers_over_the_wire(tmp_path: Path) -> None:
    """#92 criterion 1, the ``lists both`` half over the real wire: a single hub with
    **both** an ``oidc`` and a ``github`` provider configured (both pointed at the one
    stub IdP, which serves both shapes at one origin) lists both from
    ``GET /api/auth/providers``, and a full dance against one of them still ends
    authenticated — proving the two-provider registry the AC names, not just a
    single-provider hub."""
    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "1001", "handle": "octocat", "email": "octocat@example.com", "email_verified": True},
        )
        oidc = OAuthProviderConfig(
            name="oidc-co",
            type="oidc",
            display_name="Stub SSO",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            issuer=f"http://127.0.0.1:{idp_port}",
        )
        github = OAuthProviderConfig(
            name="github",
            type="github",
            display_name="GitHub",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            api_base=f"http://127.0.0.1:{idp_port}",
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(github, oidc)) as hub:
            providers_resp = hub.get("/api/auth/providers")
            assert providers_resp.status_code == 200
            listed = {p["name"]: p for p in providers_resp.json()}
            assert listed["github"] == {"name": "github", "display_name": "GitHub", "type": "github"}
            assert listed["oidc-co"] == {"name": "oidc-co", "display_name": "Stub SSO", "type": "oidc"}

            hub.get("/api/auth/oidc-co/authorize", follow_redirects=True)
            assert "bz_session" in hub.cookies
            assert hub.get("/api/me").json()["username"] == "octocat"


def test_refused_callback_lever_surfaces_as_a_login_failure(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put("/_levers/refuse_callback", json={"refuse": True})
        provider = OAuthProviderConfig(
            name="oidc-co",
            type="oidc",
            display_name="Stub SSO",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            issuer=f"http://127.0.0.1:{idp_port}",
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,)) as hub:
            resp = hub.get("/api/auth/oidc-co/authorize", follow_redirects=True)
            # The stub redirects back to the hub's own callback with `error=
            # access_denied` and no `code` — the hub's callback still runs (the
            # round-tripped `state` still resolves) but the missing code fails the
            # exchange step, landing on the same `login_failed` response the
            # component-tier fake-provider test already covers, now proven over the
            # real wire.
            assert resp.status_code == 400
            assert resp.json()["error"] == "login_failed"
            assert "bz_session" not in hub.cookies
