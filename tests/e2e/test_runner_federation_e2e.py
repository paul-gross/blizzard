"""The multi-daemon SSO bounce, driven by a real browser — e2e scenario 13 (issue #95,
the plan's phase-5 matrix gap).

Real subprocesses throughout: a hub (``auth.mode = "oauth"``) against the real
``blizzard-mock`` stub IdP, and **two** runners (A, B), each with its own registered
federation identity. A real Chromium (Playwright) navigates to runner A with no
session, is bounced through the hub (which itself bounces through the stub IdP's
provider dance since no hub session exists yet), and lands back on runner A's own
served page authenticated — the token delivered via the hub's auto-submitting
``form_post`` page, never a query string (asserted by inspecting every request URL
Chromium makes across the whole dance). The captured token (read off the real
``POST /api/auth/callback`` Chromium itself makes) is then replayed against runner B
(rejected — audience-bound) and against runner A a second time (rejected — single-use
``jti``); a mismatched ``state`` is rejected; and a hub key rotation mid-run is picked
up by a second, live browser bounce into runner B with **no restart** of either
process.

Reproduce — from a provisioned feature env::

    uv run playwright install chromium   # once, out of band
    BLIZZARD_E2E=1 uv run pytest tests/e2e/test_runner_federation_e2e.py
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
from tests.service.support import require_stub_idp, stub_idp

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e runner federation needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_SECRET_ENV = "BZ_OAUTH_E2E_FED_SECRET"
_SECRET = "e2e-fed-oauth-secret"
_PROVIDER_NAME = "oidc-fed"
_PROFILE_EMAIL = "fed-admin@example.com"


def _hub_bin() -> str:
    return str(Path(sys.executable).parent / "blizzard-hub")


def _runner_bin() -> str:
    return str(Path(sys.executable).parent / "blizzard-runner")


@contextlib.contextmanager
def _oauth_hub(hub_dir: Path, idp_port: int, port: int) -> Iterator[httpx.Client]:
    env = {**os.environ, _SECRET_ENV: _SECRET}
    subprocess.run([_hub_bin(), "init", str(hub_dir)], check=True, capture_output=True, text=True, env=env)
    provider = OAuthProviderConfig(
        name=_PROVIDER_NAME,
        type="oidc",
        display_name="Stub SSO",
        client_id="cid",
        client_secret_env=_SECRET_ENV,
        issuer=f"http://127.0.0.1:{idp_port}",
    )
    config = HubConfig.load(hub_dir)
    # `superuser` (issue #94's bootstrap) is the same email the stub IdP profile below
    # asserts — the first login claims it, giving the key-rotation step a session with
    # `user:manage` with no separate role-assignment surface to drive.
    config = dataclasses.replace(
        config, auth=AuthConfig(mode="oauth", oauth_providers=(provider,), superuser=_PROFILE_EMAIL)
    )
    config.config_path.write_text(config.to_toml())
    proc = subprocess.Popen(
        [_hub_bin(), "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
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
def _federated_runner(runner_dir: Path, *, hub_port: int, port: int, runner_id: str) -> Iterator[None]:
    public_url = f"http://127.0.0.1:{port}"
    subprocess.run(
        [_runner_bin(), "init", str(runner_dir)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "BZ_HUB_URL": f"http://127.0.0.1:{hub_port}"},
    )
    config = RunnerConfig.load(runner_dir)
    config = dataclasses.replace(config, runner_id=runner_id, public_url=public_url)
    config.config_path.write_text(config.to_toml())

    reg_client = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=15.0)
    try:
        reg_resp = reg_client.post(
            "/api/fleet/runners",
            json={
                "runner_id": runner_id,
                "workspace_id": f"workspace-{runner_id}",
                "url": public_url,
                "redirect_uris": [f"{public_url}/api/auth/callback"],
            },
        )
        assert reg_resp.status_code == 201, reg_resp.text
    finally:
        reg_client.close()

    proc = subprocess.Popen(
        [_runner_bin(), "host", "--dir", str(runner_dir), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=public_url, timeout=15.0)
    try:
        _await_http(proc, client, "/api/health")
        yield
    finally:
        client.close()
        _terminate(proc)


def test_multi_daemon_sso_bounce(tmp_path: Path) -> None:
    from playwright.sync_api import expect, sync_playwright

    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()
    runner_a_port = _free_port()
    runner_b_port = _free_port()
    runner_a_url = f"http://127.0.0.1:{runner_a_port}"
    runner_b_url = f"http://127.0.0.1:{runner_b_port}"

    with sync_playwright() as pw, stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "5001", "handle": "fed-admin", "email": _PROFILE_EMAIL, "email_verified": True},
        )

        with (
            _oauth_hub(tmp_path / "hub", idp_port, hub_port),
            _federated_runner(tmp_path / "runner-a", hub_port=hub_port, port=runner_a_port, runner_id="runner-e2e-a"),
            _federated_runner(tmp_path / "runner-b", hub_port=hub_port, port=runner_b_port, runner_id="runner-e2e-b"),
        ):
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            expect.set_options(timeout=20_000)

            captured_callback_bodies: list[str] = []
            captured_urls: list[str] = []

            def _on_request(request):  # type: ignore[no-untyped-def]
                captured_urls.append(request.url)
                if request.url == f"{runner_a_url}/api/auth/callback" and request.method == "POST":
                    body = request.post_data
                    if body:
                        captured_callback_bodies.append(body)

            page.on("request", _on_request)

            try:
                # --- 1. Runner A, no session: bounces through the hub, which itself
                # bounces through the stub IdP dance (no hub session yet either),
                # and lands back on runner A's own served page authenticated.
                page.goto(f"{runner_a_url}/", wait_until="load")
                expect(page).to_have_title(re.compile("blizzard runner"))
                assert any(c.get("name") == "bz_runner_session" for c in context.cookies(runner_a_url))

                # AC: the token never appears in a query string, across every request
                # Chromium made during the whole dance.
                assert not any("token=" in url for url in captured_urls)

                # The real POST body Chromium's own auto-submitted form_post carried —
                # the token this scenario replays below.
                assert captured_callback_bodies, "runner A's callback was never POSTed to"
                first_body = captured_callback_bodies[0]
                token_match = re.search(r"token=([^&]+)", first_body)
                assert token_match is not None
                captured_token = token_match.group(1)

                # --- 2. Audience-binding: the same token, presented to runner B (a
                # different `aud`), is rejected even with a state B itself minted.
                login_b = page.request.get(f"{runner_b_url}/api/auth/login?return_to=/", max_redirects=0)
                assert login_b.status in (302, 307)
                state_b = next(
                    c.get("value") for c in context.cookies(runner_b_url) if c.get("name") == "bz_runner_bounce_state"
                )
                cross_resp = page.request.post(
                    f"{runner_b_url}/api/auth/callback",
                    headers={"content-type": "application/x-www-form-urlencoded"},
                    data=f"token={captured_token}&state={state_b}",
                )
                assert cross_resp.status == 400

                # --- 3. Replay: the same token, presented to runner A again (a fresh,
                # A-own state so only the jti check can fail it), is rejected —
                # single-use.
                login_a_again = page.request.get(f"{runner_a_url}/api/auth/login?return_to=/", max_redirects=0)
                assert login_a_again.status in (302, 307)
                state_a2 = next(
                    c.get("value") for c in context.cookies(runner_a_url) if c.get("name") == "bz_runner_bounce_state"
                )
                replay_resp = page.request.post(
                    f"{runner_a_url}/api/auth/callback",
                    headers={"content-type": "application/x-www-form-urlencoded"},
                    data=f"token={captured_token}&state={state_a2}",
                )
                assert replay_resp.status == 400

                # --- 4. A mismatched `state` is rejected outright.
                login_a_3 = page.request.get(f"{runner_a_url}/api/auth/login?return_to=/", max_redirects=0)
                assert login_a_3.status in (302, 307)
                mismatch_resp = page.request.post(
                    f"{runner_a_url}/api/auth/callback",
                    headers={"content-type": "application/x-www-form-urlencoded"},
                    data=f"token={captured_token}&state=not-the-real-state",
                )
                assert mismatch_resp.status == 400

                # --- 5. Key rotation, mid-run, picked up with no restart of either
                # daemon: rotate, then drive a fresh browser bounce into runner B —
                # the JWKS the still-running runner B fetches must name the new `kid`.
                # The browser context (not the plain `hub` client above) holds the
                # hub session cookie the provider dance minted, so the rotation call
                # rides `page.request` to carry it.
                rotate_resp = page.request.post(f"http://127.0.0.1:{hub_port}/api/auth/rotate-signing-key")
                assert rotate_resp.status == 204, rotate_resp.text()

                page.goto(f"{runner_b_url}/", wait_until="load")
                expect(page).to_have_title(re.compile("blizzard runner"))
                assert any(c.get("name") == "bz_runner_session" for c in context.cookies(runner_b_url))
            finally:
                browser.close()
