"""Browser login dance + mid-stream session-expiry redirect — e2e scenario 12
(issue #93, the plan's phase-3 matrix gap).

The browser tier's login coverage: a real Chromium, driven by Playwright, over the
**served** board (``blizzard hub host`` mounts the built Angular app at ``/``,
exactly as scenario 6, ``test_board_browser_e2e.py``), under ``auth.mode = "oauth"``
against the real ``blizzard-mock`` stub IdP — every seam real, no tokens, no network
beyond the two local subprocesses:

1. **Unauthenticated -> login -> provider dance -> authenticated.** The board is hit
   with no session; the app's own 401 gate lands it on ``/login``, rendering the
   configured provider's button (never an auto-redirect). Clicking it drives the real
   OAuth dance against the stub IdP and lands back on the hub authenticated — as a
   freshly-minted ``guest`` (role assignment is #94; no admin surface exists yet to
   grant more), it lands on the **guest lobby** ("signed in, awaiting access"), not a
   broken board — itself proof the dance produced a real, working session cookie.
2. **A session expiring mid-SSE-stream surfaces as a login redirect within one
   reconnect cycle.** Reaching the live board (not just the lobby) needs a
   permission-bearing role, and #93 lands no role-assignment surface (#94's own
   slice) — so the guest's role is promoted directly in the hub's sqlite store, the
   one seam available before #94 exists, exactly as this suite already mints fixture
   state no API yet exposes. The hub is stopped, ``users.role`` is set to
   ``contributor`` for the logged-in user, and the hub is restarted on the same
   store/port; the browser (still holding its session cookie) reloads and reaches the
   real board with its SSE stream open. The hub is then stopped again, the session
   row is deleted (an unambiguous stand-in for "expired" — the resolve path treats a
   missing/expired session identically, ``hub/api/auth_session.py``), and restarted.
   Killing the hub process force-drops the open stream; the client's own reconnect
   (the fetch-based transport's one seam that can see a status code at all,
   ``sse.service.ts``) lands on the new hub with an invalid session, receives ``401``
   on that **one reconnect attempt**, and the app routes to ``/login`` — proving the
   auth-failure channel end to end rather than an unbounded retry loop.

It is the **e2e tier**: it needs the full live stack, the sibling ``blizzard-mock``
worktree, and an installed Chromium, so it is **skipped unless ``BLIZZARD_E2E=1``**
and those are present. Reproduce it — from the ``blizzard`` worktree in a provisioned
feature env — with::

    uv run playwright install chromium   # once, out of band
    BLIZZARD_E2E=1 uv run pytest tests/e2e/test_login_session_e2e.py

(The workspace runs it under ``mise run e2e`` with the sibling scenarios.)
"""

from __future__ import annotations

import dataclasses
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from blizzard.hub.config import AuthConfig, HubConfig, OAuthProviderConfig
from tests.e2e.test_acceptance_loop import _await_http, _free_port, _terminate
from tests.service.support import require_stub_idp, stub_idp

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e login session needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_SECRET_ENV = "BZ_OAUTH_E2E_SECRET"
_SECRET = "e2e-oauth-secret"
_PROVIDER_NAME = "oidc-co"


def _hub_bin() -> str:
    return str(Path(sys.executable).parent / "blizzard-hub")


def _init_oauth_hub(hub_dir: Path, idp_port: int) -> None:
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
    config = dataclasses.replace(config, auth=AuthConfig(mode="oauth", oauth_providers=(provider,)))
    config.config_path.write_text(config.to_toml())


def _start_hub(hub_dir: Path, port: int) -> subprocess.Popen[str]:
    env = {**os.environ, _SECRET_ENV: _SECRET}
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
    finally:
        client.close()
    return proc


def _db_path(hub_dir: Path) -> Path:
    return hub_dir / "data" / "hub.db"


def _promote_to_contributor(hub_dir: Path, username: str) -> None:
    """The stand-in for #94's not-yet-landed role-assignment API — direct store
    access, the same "mint what the API cannot yet" pattern this suite already uses
    for fixture state (``mint_fixture``, forge seeding)."""
    con = sqlite3.connect(_db_path(hub_dir))
    try:
        con.execute("UPDATE users SET role = 'contributor' WHERE username = ?", (username,))
        con.commit()
    finally:
        con.close()


def _expire_session(hub_dir: Path) -> None:
    """Delete every session row — an unambiguous stand-in for "expired": the resolve
    path (``hub/api/auth_session.py``) treats a missing and an expired session
    identically, so this exercises exactly the branch a real expiry would."""
    con = sqlite3.connect(_db_path(hub_dir))
    try:
        con.execute("DELETE FROM sessions")
        con.commit()
    finally:
        con.close()


def test_browser_login_dance_and_mid_stream_session_expiry(tmp_path: Path) -> None:
    """Scenario 12: the browser login dance to a working session, then a session
    expiring mid-SSE-stream surfacing as a login redirect within one reconnect cycle."""
    from playwright.sync_api import expect, sync_playwright

    bin_dir = require_stub_idp()

    idp_port = _free_port()
    hub_port = _free_port()
    hub_dir = tmp_path / "hub"

    with sync_playwright() as pw, stub_idp(bin_dir, idp_port) as idp:
        idp.put(
            "/_levers/profile",
            json={"subject": "1001", "handle": "octocat", "email": "octocat@example.com", "email_verified": True},
        )

        _init_oauth_hub(hub_dir, idp_port)
        proc = _start_hub(hub_dir, hub_port)

        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        expect.set_options(timeout=20_000)
        try:
            # --- 1. Unauthenticated hit lands on /login, one button, no auto-redirect
            page.goto(f"http://127.0.0.1:{hub_port}/", wait_until="load")
            expect(page.get_by_test_id("login-page")).to_be_visible()
            expect(page.get_by_test_id(f"login-provider-{_PROVIDER_NAME}")).to_be_visible()
            expect(page.get_by_test_id("board-header")).to_have_count(0)

            # --- The real OAuth dance against the stub IdP -----------------------
            page.get_by_test_id(f"login-provider-{_PROVIDER_NAME}").click()

            # A fresh identity mints as `guest` (no role-assignment surface exists
            # yet, #94) — the lobby is the authenticated proof the dance worked.
            expect(page.get_by_test_id("guest-lobby")).to_be_visible()
            expect(page.get_by_test_id("guest-lobby-username")).to_contain_text("octocat")

            # --- 2a. Promote to contributor (the #94 stand-in) and reach the board
            _terminate(proc)
            _promote_to_contributor(hub_dir, "octocat")
            proc = _start_hub(hub_dir, hub_port)

            page.reload(wait_until="load")
            expect(page.get_by_test_id("board-header")).to_be_visible()
            expect(page.get_by_test_id("board-shell")).to_be_visible()
            expect(page.get_by_test_id("guest-lobby")).to_have_count(0)

            # --- 2b. Expire the session mid-stream; the hub restart force-drops the
            # open SSE connection, and the client's own reconnect is the "one
            # reconnect cycle" that discovers the now-invalid session.
            _terminate(proc)
            _expire_session(hub_dir)
            proc = _start_hub(hub_dir, hub_port)

            expect(page.get_by_test_id("login-page")).to_be_visible()
            expect(page.get_by_test_id(f"login-provider-{_PROVIDER_NAME}")).to_be_visible()
        finally:
            browser.close()
            _terminate(proc)
