"""OAuth provider login — the real hub against the real stub IdP (service tier).

A running hub (``auth.mode = "oauth"``) whose ``authorize`` 302s to the real stub IdP
subprocess, whose ``callback`` exchanges the stub's code, ending in a resolving session
cookie and a working ``GET /api/me`` — for both ``oidc`` and ``github`` conformers, over
the real wire. The hub is invite-only: a person it has not seen arrives through an invitation
(:func:`invite`) or as the configured superuser, and anyone else lands on the login page's
refusal with no session. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import contextlib
import dataclasses
import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from blizzard.auth_core import Role
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import AuthConfig, OAuthProviderConfig
from tests.config_inputs import load_hub_config
from tests.fleet_launchers import await_http, terminate
from tests.service.support import require_stub_idp, service_gate, stub_idp
from tests.support import daemon_log_sink, default_tenant, free_port, seed_invitation

pytestmark = [pytest.mark.service, service_gate]

_SECRET_ENV = "BZ_OAUTH_TEST_SECRET"
_SECRET = "test-secret"


@contextlib.contextmanager
def _oauth_hub(
    hub_dir: Path, port: int, *, providers: tuple[OAuthProviderConfig, ...], superuser: str | None = None
) -> Iterator[httpx2.Client]:
    """A real ``blizzard hub host`` subprocess, ``auth.mode = "oauth"`` with one or more
    configured providers — mirrors ``tests/fleet_launchers.py``'s own ``run_hub``,
    minus the work-source wiring this scenario does not need (login/`` /api/me`` alone).
    ``superuser`` sets ``auth.superuser`` so the bootstrap lifecycle runs at
    this real boot, same as any other deployment."""
    env = {**os.environ, _SECRET_ENV: _SECRET}
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    config = load_hub_config(hub_dir)
    config = dataclasses.replace(config, auth=AuthConfig(mode="oauth", oauth_providers=providers, superuser=superuser))
    config.config_path.write_text(config.to_toml())
    log = hub_dir / "daemon.log"
    proc = subprocess.Popen(
        [hub_bin, "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    try:
        await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        terminate(proc)


def invite(hub_dir: Path, email: str, *, role: Role = Role.CONTRIBUTOR) -> str:
    """Invite ``email`` into the running hub's first tenant as ``role``, issued now — the way a person the hub has
    not seen arrives — and return the link's token for ``authorize?invite=``."""
    engine = create_engine_from_url(load_hub_config(hub_dir).db_url)
    try:
        _invitation_id, token = seed_invitation(engine, default_tenant(engine), email, role, at=datetime.now(UTC))
    finally:
        engine.dispose()
    return token


def test_oidc_login_dance_against_the_stub_idp_ends_authenticated(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = free_port()
    hub_port = free_port()

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
            emails_authoritative=True,
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,)) as hub:
            providers_resp = hub.get("/api/auth/providers")
            assert providers_resp.status_code == 200
            assert providers_resp.json() == [{"name": "oidc-co", "display_name": "Stub SSO", "type": "oidc"}]

            token = invite(tmp_path / "hub", "octocat@example.com")
            hub.get(f"/api/auth/oidc-co/authorize?invite={token}", follow_redirects=True)
            assert "bz_session" in hub.cookies

            me_resp = hub.get("/api/me")
            assert me_resp.status_code == 200
            body = me_resp.json()
            assert body["username"] == "octocat"
            assert [tenant["role"] for tenant in body["tenants"]] == ["contributor"]


def test_github_login_dance_against_the_stub_idp_ends_authenticated(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = free_port()
    hub_port = free_port()

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
            token = invite(tmp_path / "hub", "hub@example.com")
            hub.get(f"/api/auth/github/authorize?invite={token}", follow_redirects=True)
            assert "bz_session" in hub.cookies

            me_resp = hub.get("/api/me")
            assert me_resp.status_code == 200
            assert me_resp.json()["username"] == "hubbernetes"


def test_login_then_logout_round_trips_over_the_wire(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = free_port()
    hub_port = free_port()

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
            emails_authoritative=True,
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,)) as hub:
            token = invite(tmp_path / "hub", "octocat@example.com")
            hub.get(f"/api/auth/oidc-co/authorize?invite={token}", follow_redirects=True)
            assert hub.get("/api/me").status_code == 200

            logout_resp = hub.post("/api/auth/logout")
            assert logout_resp.status_code == 204

            assert hub.get("/api/me").status_code == 401


def test_providers_endpoint_lists_both_configured_providers_over_the_wire(tmp_path: Path) -> None:
    """#92 criterion 1: a hub with both an ``oidc`` and ``github`` provider configured
    lists both from ``GET /api/auth/providers``, and a full dance against one still ends
    authenticated."""
    bin_dir = require_stub_idp()
    idp_port = free_port()
    hub_port = free_port()

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
            emails_authoritative=True,
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

            token = invite(tmp_path / "hub", "octocat@example.com")
            hub.get(f"/api/auth/oidc-co/authorize?invite={token}", follow_redirects=True)
            assert "bz_session" in hub.cookies
            assert hub.get("/api/me").json()["username"] == "octocat"


def test_refused_callback_lever_surfaces_as_a_login_failure(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = free_port()
    hub_port = free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put("/_levers/refuse_callback", json={"refuse": True})
        provider = OAuthProviderConfig(
            name="oidc-co",
            type="oidc",
            display_name="Stub SSO",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            issuer=f"http://127.0.0.1:{idp_port}",
            emails_authoritative=True,
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,)) as hub:
            resp = hub.get("/api/auth/oidc-co/authorize", follow_redirects=True)
            # The stub redirects back with `error=access_denied` and no `code`; the
            # callback's `state` still resolves but the missing code fails the exchange.
            assert resp.status_code == 400
            assert resp.json()["error"] == "login_failed"
            assert "bz_session" not in hub.cookies


def test_an_uninvited_first_sign_in_lands_on_the_invite_only_refusal_with_no_session(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = free_port()
    hub_port = free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "42", "handle": "stranger", "email": "stranger@example.com", "email_verified": True},
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
            landed = hub.get("/api/auth/github/authorize", follow_redirects=True)

            callback = landed.history[-1]
            assert callback.url.path == "/api/auth/github/callback"
            assert callback.headers["location"] == "/login?refusal=invite_only"
            assert "bz_session" not in hub.cookies
            assert hub.get("/api/me").status_code == 401


@pytest.mark.parametrize("authoritative", [True, False], ids=["authoritative", "not-authoritative"])
def test_named_superuser_email_lands_superuser_on_first_verified_login(tmp_path: Path, authoritative: bool) -> None:
    """The bootstrap-claim path, over the real wire: a fresh store with
    ``auth.superuser`` set pre-provisions an unclaimed intent, and the first verified
    login for that exact email lands the freshly-minted user as ``superuser`` — when the
    provider is authoritative for its addresses. Through one that is not, the same login is
    refused as invite-only, with no session."""
    bin_dir = require_stub_idp()
    idp_port = free_port()
    hub_port = free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "9001", "handle": "root", "email": "root@example.com", "email_verified": True},
        )
        provider = OAuthProviderConfig(
            name="oidc-co",
            type="oidc",
            display_name="Stub SSO",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            issuer=f"http://127.0.0.1:{idp_port}",
            emails_authoritative=authoritative,
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,), superuser="root@example.com") as hub:
            landed = hub.get("/api/auth/oidc-co/authorize", follow_redirects=True)
            if not authoritative:
                assert landed.history[-1].headers["location"] == "/login?refusal=invite_only"
                assert "bz_session" not in hub.cookies
                assert hub.get("/api/me").status_code == 401
                return
            assert "bz_session" in hub.cookies

            me_resp = hub.get("/api/me")
            assert me_resp.status_code == 200
            body = me_resp.json()
            assert body["username"] == "root"
            assert (body["role"], body["hub_admin"]) == ("admin", True)
