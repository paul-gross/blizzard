"""The runner SSO federation JWT/JWKS wire leg — real hub + real runner subprocesses,
no browser (service tier, issue #95).

The plan's own matrix-gap note: the full browser-driven multi-daemon bounce is a
``blizzard:e2e`` scenario; **this** is the lighter service-tier companion that proves
the JWT/JWKS wire leg alone — a real hub (``auth.mode = "oauth"``) delivers a
hub-signed token via ``response_mode=form_post`` to a real runner's
``POST /api/auth/callback``, ending in a runner-domain session and an unlocked
human-lane route, entirely over real localhost HTTP with no in-process fakes. A hub
session is established through the real ``blizzard-mock`` stub IdP (mirrors
``tests/service/test_auth_login_service.py``'s own dance) rather than seeded directly,
so the whole chain — provider dance -> hub session -> IdP authorize -> runner
callback — is real.

Reproduce — from a provisioned feature env::

    BLIZZARD_SERVICE=1 uv run pytest tests/service/test_idp_federation_service.py
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from blizzard.hub.config import AuthConfig, HubConfig, OAuthProviderConfig
from blizzard.runner.config import RunnerConfig
from tests.e2e.test_acceptance_loop import _await_http, _free_port, _terminate
from tests.service.support import require_stub_idp, service_gate, stub_idp

pytestmark = [pytest.mark.service, service_gate]

_SECRET_ENV = "BZ_OAUTH_TEST_SECRET"
_SECRET = "test-secret"
_RUNNER_ID = "runner-svc-a"


@contextlib.contextmanager
def _oauth_hub(
    hub_dir: Path, port: int, *, providers: tuple[OAuthProviderConfig, ...], superuser: str | None = None
) -> Iterator[httpx.Client]:
    """A real ``blizzard hub host`` subprocess, ``auth.mode = "oauth"`` — mirrors
    ``tests/service/test_auth_login_service.py``'s own fixture of the same name."""
    env = {**os.environ, _SECRET_ENV: _SECRET}
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    config = HubConfig.load(hub_dir)
    config = dataclasses.replace(config, auth=AuthConfig(mode="oauth", oauth_providers=providers, superuser=superuser))
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


@contextlib.contextmanager
def _federated_runner(
    runner_dir: Path, *, hub_port: int, port: int, runner_id: str = _RUNNER_ID
) -> Iterator[httpx.Client]:
    """A real ``blizzard runner host`` subprocess registered at ``hub_port`` with its
    own federation identity (``public_url``) — the runner this scenario bounces into."""
    public_url = f"http://127.0.0.1:{port}"
    runner_bin = str(Path(sys.executable).parent / "blizzard-runner")
    subprocess.run(
        [runner_bin, "init", str(runner_dir)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "BZ_HUB_URL": f"http://127.0.0.1:{hub_port}"},
    )
    config = RunnerConfig.load(runner_dir)
    config = dataclasses.replace(config, runner_id=runner_id, public_url=public_url)
    config.config_path.write_text(config.to_toml())

    # Registration (issue #95) — an authenticated-by-default-warn-mode fleet write,
    # exactly like every other runner registration; carries this runner's own
    # federation identity so the hub's authorize endpoint will accept a bounce to it.
    reg_client = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=15.0)
    try:
        reg_resp = reg_client.post(
            "/api/fleet/runners",
            json={
                "runner_id": runner_id,
                "workspace_id": "workspace-svc",
                "url": public_url,
                "redirect_uris": [f"{public_url}/api/auth/callback"],
            },
        )
        assert reg_resp.status_code == 201, reg_resp.text
    finally:
        reg_client.close()

    proc = subprocess.Popen(
        [runner_bin, "host", "--dir", str(runner_dir), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=public_url, timeout=15.0)
    try:
        _await_http(proc, client, "/api/health")
        yield client
    finally:
        client.close()
        _terminate(proc)


def _bounce_once(hub: httpx.Client, runner: httpx.Client) -> None:
    """Drive one federation round trip: the runner mints its own bounce ``state``
    (``GET /api/auth/login``), the hub (already carrying a session cookie in ``hub``'s
    own jar) mints+delivers a token for it, and the runner's callback consumes it."""
    login_resp = runner.get("/api/auth/login?return_to=/", follow_redirects=False)
    assert login_resp.status_code in (302, 307)
    authorize_url = login_resp.headers["location"]
    state = login_resp.cookies["bz_runner_bounce_state"]

    authorize_resp = hub.get(authorize_url, follow_redirects=False)
    assert authorize_resp.status_code == 200, authorize_resp.text
    body = authorize_resp.text
    token_match = re.search(r'name="token" value="([^"]+)"', body)
    state_match = re.search(r'name="state" value="([^"]+)"', body)
    assert token_match is not None and state_match is not None
    assert state_match.group(1) == state  # round-tripped intact

    callback_resp = runner.post(
        "/api/auth/callback",
        content=f"token={token_match.group(1)}&state={state_match.group(1)}",
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert callback_resp.status_code == 303, callback_resp.text
    assert "bz_runner_session" in callback_resp.cookies


def test_the_wire_leg_ends_in_an_unlocked_runner_route(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()
    runner_port = _free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "9001", "handle": "svc-user", "email": "svc-user@example.com", "email_verified": True},
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
            hub.get("/api/auth/oidc-svc/authorize", follow_redirects=True)
            assert "bz_session" in hub.cookies

            with _federated_runner(tmp_path / "runner", hub_port=hub_port, port=runner_port) as runner:
                assert runner.get("/", follow_redirects=False).status_code in (302, 307)

                _bounce_once(hub, runner)

                session_cookie = runner.cookies.get("bz_runner_session")
                assert session_cookie
                gated = runner.get("/")
                assert gated.status_code == 200


def test_key_rotation_is_picked_up_by_a_live_runner_with_no_restart(tmp_path: Path) -> None:
    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()
    runner_port = _free_port()

    with stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "9002", "handle": "svc-admin", "email": "svc-admin@example.com", "email_verified": True},
        )
        provider = OAuthProviderConfig(
            name="oidc-svc",
            type="oidc",
            display_name="Stub SSO",
            client_id="cid",
            client_secret_env=_SECRET_ENV,
            issuer=f"http://127.0.0.1:{idp_port}",
        )
        with _oauth_hub(tmp_path / "hub", hub_port, providers=(provider,), superuser="svc-admin@example.com") as hub:
            hub.get("/api/auth/oidc-svc/authorize", follow_redirects=True)
            assert "bz_session" in hub.cookies

            with _federated_runner(tmp_path / "runner", hub_port=hub_port, port=runner_port) as runner:
                _bounce_once(hub, runner)  # first bounce, current key — proves the baseline works

                rotate_resp = hub.post("/api/auth/rotate-signing-key")
                assert rotate_resp.status_code == 204, rotate_resp.text

                # A fresh bounce, minted under the just-rotated key: the runner's JWKS
                # cache has never seen this `kid` before — its own cache-miss refetch
                # (`JwksCache.key_for`) must pick it up with no restart of the process
                # already running above.
                _bounce_once(hub, runner)
