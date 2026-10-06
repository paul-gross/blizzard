"""The multi-daemon SSO bounce, driven by a real browser — the `test_runner_federation_e2e` scenario.

A real Chromium bounces through a real hub + stub IdP into runner A, then the captured
token is replayed against runner B (rejected, audience-bound) and against runner A again
(rejected, single-use ``jti``); a hub key rotation mid-run is picked up by a second
bounce into runner B with no restart. Needs ``uv run playwright install chromium`` once."""

from __future__ import annotations

import contextlib
import dataclasses
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

import httpx
import pytest

from blizzard.hub.config import AuthConfig, HubConfig, OAuthProviderConfig
from blizzard.runner.auth.session import CookieNames
from blizzard.runner.config import RunnerConfig
from tests.e2e.test_acceptance_loop import _await_http, _free_port, _terminate
from tests.runner_join import add_runner, adopt_runner, runner_spawn_env
from tests.service.support import poll_until, require_stub_idp, stub_idp
from tests.support import daemon_log_sink

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
# Both A/B runners hold it: runner names are not unique, so only their hub-minted ids keep their cookies apart.
_SHARED_NAME = "runner-e2e"


class _FederatedRunner(NamedTuple):
    """A joined runner's daemon and the cookie names its hub-minted id keys."""

    proc: subprocess.Popen[str]
    names: CookieNames


def _bounce_state_cookie(response: Any, names: CookieNames) -> str:
    """Read the runner's bounce-state value straight off a login redirect's
    ``Set-Cookie`` header, rather than through ``BrowserContext.cookies()`` — Playwright's
    loopback handling never attributes this ``Secure`` cookie to a plain-``http`` URL, so
    callers must pass the value back explicitly via a ``Cookie`` header."""
    for header in response.headers_array:
        if header["name"].lower() != "set-cookie":
            continue
        match = re.match(rf"{re.escape(names.bounce_state)}=([^;]+)", header["value"])
        if match:
            return match.group(1)
    raise AssertionError(f"login response carried no {names.bounce_state} cookie: {response.headers_array}")


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
    # `superuser` (bootstrap) matches the stub IdP profile below, so the
    # first login claims `user:manage` with no separate role-assignment surface.
    config = dataclasses.replace(
        config, auth=AuthConfig(mode="oauth", oauth_providers=(provider,), superuser=_PROFILE_EMAIL)
    )
    config.config_path.write_text(config.to_toml())
    log = hub_dir / "daemon.log"
    proc = subprocess.Popen(
        [_hub_bin(), "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    try:
        _await_http(proc, client, "/api/health", log=log)
        yield client
    finally:
        client.close()
        _terminate(proc)


def _spawn_runner(runner_dir: Path, *, port: int) -> subprocess.Popen[str]:
    """Launch `blizzard-runner host` against an already-`init`ed/registered directory —
    the restart half of this scenario, which relaunches on the same port with no
    re-`init` and no re-registration, exactly as a redeploy would."""
    log = runner_dir / "daemon.log"
    proc = subprocess.Popen(
        [_runner_bin(), "host", "--dir", str(runner_dir), "--host", "127.0.0.1", "--port", str(port)],
        env=runner_spawn_env(RunnerConfig.load(runner_dir).hub_url),
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0)
    try:
        _await_http(proc, client, "/api/health", log=log)
        # Sign-in names the runner's hub-minted id as the authorize client, so it waits for the
        # daemon's registration — the one that declares this federation identity.
        assert poll_until(lambda: client.get("/api/auth/login", follow_redirects=False).status_code != 503), (
            "the runner never registered at its hub"
        )
    finally:
        client.close()
    return proc


@contextlib.contextmanager
def _federated_runner(runner_dir: Path, *, hub_port: int, port: int, name: str) -> Iterator[_FederatedRunner]:
    """A runner joined to the hub with its own federation identity. A hub with sign-in adds a runner
    only for a signed-in operator, so the bootstrap superuser signs in and adds it; the token lands in
    the runner's ``.env``."""
    public_url = f"http://127.0.0.1:{port}"
    hub_url = f"http://127.0.0.1:{hub_port}"
    with httpx.Client(base_url=hub_url, timeout=15.0) as operator:
        operator.get(f"/api/auth/{_PROVIDER_NAME}/authorize", follow_redirects=True)
        runner_id, token = add_runner(operator, name)
    adopt_runner(runner_dir, hub_url, token)
    config = RunnerConfig.load(runner_dir)
    config = dataclasses.replace(
        config,
        name=name,
        public_urls=(public_url,),
        # A path that is never created — the sampler's missing-credentials soft failure
        # trips before any request is built.
        external_usage_credentials_path=str(runner_dir / "no-such-credentials.json"),
    )
    config.config_path.write_text(config.to_toml())

    proc = _spawn_runner(runner_dir, port=port)
    try:
        yield _FederatedRunner(proc, CookieNames(runner_id))
    finally:
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
            _federated_runner(
                tmp_path / "runner-a", hub_port=hub_port, port=runner_a_port, name=_SHARED_NAME
            ) as runner_a,
            _federated_runner(
                tmp_path / "runner-b", hub_port=hub_port, port=runner_b_port, name=_SHARED_NAME
            ) as runner_b,
        ):
            names_a, names_b = runner_a.names, runner_b.names
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
                # 1. Runner A, no session: bounces through the hub and the stub IdP
                # dance, lands back on runner A's own served page authenticated.
                page.goto(f"{runner_a_url}/", wait_until="load")
                expect(page).to_have_title(re.compile("blizzard runner"))
                assert any(c.get("name") == names_a.session for c in context.cookies(runner_a_url))

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
                state_b = _bounce_state_cookie(login_b, names_b)
                cross_resp = page.request.post(
                    f"{runner_b_url}/api/auth/callback",
                    headers={
                        "content-type": "application/x-www-form-urlencoded",
                        "cookie": f"{names_b.bounce_state}={state_b}",
                    },
                    data=f"token={captured_token}&state={state_b}",
                )
                assert cross_resp.status == 400

                # 3. Replay: the same token, presented to runner A again (a fresh,
                # A-own state so only the jti check can fail it), is rejected.
                login_a_again = page.request.get(f"{runner_a_url}/api/auth/login?return_to=/", max_redirects=0)
                assert login_a_again.status in (302, 307)
                state_a2 = _bounce_state_cookie(login_a_again, names_a)
                replay_resp = page.request.post(
                    f"{runner_a_url}/api/auth/callback",
                    headers={
                        "content-type": "application/x-www-form-urlencoded",
                        "cookie": f"{names_a.bounce_state}={state_a2}",
                    },
                    data=f"token={captured_token}&state={state_a2}",
                )
                assert replay_resp.status == 400

                # --- 4. A mismatched `state` is rejected outright.
                login_a_3 = page.request.get(f"{runner_a_url}/api/auth/login?return_to=/", max_redirects=0)
                assert login_a_3.status in (302, 307)
                state_a3 = _bounce_state_cookie(login_a_3, names_a)
                mismatch_resp = page.request.post(
                    f"{runner_a_url}/api/auth/callback",
                    headers={
                        "content-type": "application/x-www-form-urlencoded",
                        "cookie": f"{names_a.bounce_state}={state_a3}",
                    },
                    data=f"token={captured_token}&state=not-the-real-state",
                )
                assert mismatch_resp.status == 400

                # 5. Key rotation, mid-run, picked up with no restart: rotate, then bounce
                # into runner B, whose JWKS fetch must name the new `kid`.
                rotate_resp = page.request.post(f"http://127.0.0.1:{hub_port}/api/auth/rotate-signing-key")
                assert rotate_resp.status == 204, rotate_resp.text()

                page.goto(f"{runner_b_url}/", wait_until="load")
                expect(page).to_have_title(re.compile("blizzard runner"))
                assert any(c.get("name") == names_b.session for c in context.cookies(runner_b_url))
                # Same host, same name: B's sign-in left A's session cookie in the jar.
                assert any(c.get("name") == names_a.session for c in context.cookies(runner_a_url))
            finally:
                browser.close()


def test_runner_session_reacquisition_e2e(tmp_path: Path) -> None:
    """The runner's session-recovery seam: restarting the runner (its
    session secret is minted per start) invalidates an open tab's session with no
    reload and no touch to the hub's own session; the SPA must re-federate on its own."""
    from playwright.sync_api import expect, sync_playwright

    bin_dir = require_stub_idp()
    idp_port = _free_port()
    hub_port = _free_port()
    runner_port = _free_port()
    runner_url = f"http://127.0.0.1:{runner_port}"
    runner_dir = tmp_path / "runner"

    with sync_playwright() as pw, stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "6001", "handle": "session-op", "email": _PROFILE_EMAIL, "email_verified": True},
        )

        with (
            _oauth_hub(tmp_path / "hub", idp_port, hub_port),
            _federated_runner(runner_dir, hub_port=hub_port, port=runner_port, name=_SHARED_NAME) as runner,
        ):
            proc, names = runner
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            expect.set_options(timeout=20_000)
            new_proc: subprocess.Popen[str] | None = None

            try:
                # 1. Authenticate into the panel, same dance as scenario 1 above.
                page.goto(f"{runner_url}/", wait_until="load")
                expect(page).to_have_title(re.compile("blizzard runner"))
                expect(page.locator('[data-testid="identity-username"]')).to_be_visible()
                before_cookie = next(
                    c.get("value") for c in context.cookies(runner_url) if c.get("name") == names.session
                )

                # 2. Restart in place (same dir/port, no re-`init`) — a redeploy.
                _terminate(proc)
                new_proc = _spawn_runner(runner_dir, port=runner_port)

                # 3. No goto/reload here: wait for the seam's own bounce request, triggered by
                # the SSE reconnect; timeout clears SseService's own backoff ladder — see e2e-scenarios.md.
                page.wait_for_event(
                    "request", predicate=lambda r: "/api/auth/login?return_to=" in r.url, timeout=40_000
                )
                page.wait_for_load_state("load")
                expect(page.locator('[data-testid="identity-username"]')).to_be_visible()
                after_cookie = next(
                    c.get("value") for c in context.cookies(runner_url) if c.get("name") == names.session
                )
                assert after_cookie != before_cookie, "the session cookie never changed — no fresh bounce happened"
            finally:
                browser.close()
                if new_proc is not None:
                    _terminate(new_proc)
