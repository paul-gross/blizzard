"""Browser login dance + mid-stream session-expiry redirect (issue #93; ladder from #210).

Real Chromium over the served board under oauth: login lands `pending`; `guest` reads
read-only; `contributor` gets the write control; a session expiring mid-SSE redirects to
login within one reconnect. Skipped unless ``BLIZZARD_E2E=1``.
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
from tests.support import daemon_log_sink

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
    finally:
        client.close()
    return proc


def _db_path(hub_dir: Path) -> Path:
    return hub_dir / "data" / "hub.db"


def _set_role(hub_dir: Path, username: str, role: str) -> None:
    """The stand-in for #94's not-yet-landed role-assignment API — direct store access,
    the same "mint what the API cannot yet" pattern this suite uses for fixture state."""
    con = sqlite3.connect(_db_path(hub_dir))
    try:
        con.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
        con.commit()
    finally:
        con.close()


def _seed_not_ready_chunk(hub_dir: Path, *, chunk_id: str, graph_id: str) -> None:
    """One bare fixture chunk — no route, no facts — so the read-only board claim
    (Phase 5's Promote control) has a concrete card to assert against. Derives
    ``not_ready`` by construction: a chunk with no promote fact rests there."""
    con = sqlite3.connect(_db_path(hub_dir))
    try:
        con.execute(
            "INSERT INTO graphs (graph_id, name, entry_node_id, definition_yaml, created_at) VALUES (?, ?, ?, ?, ?)",
            (graph_id, "g", "nd_1", "", "2026-07-13 00:00:00.000000"),
        )
        con.execute(
            "INSERT INTO chunks (chunk_id, graph_id, minted_at) VALUES (?, ?, ?)",
            (chunk_id, graph_id, "2026-07-13 00:00:00.000000"),
        )
        con.commit()
    finally:
        con.close()


def _expire_session(hub_dir: Path) -> None:
    """Delete every session row — an unambiguous stand-in for "expired", since the
    resolve path treats a missing and an expired session identically."""
    con = sqlite3.connect(_db_path(hub_dir))
    try:
        con.execute("DELETE FROM sessions")
        con.commit()
    finally:
        con.close()


def test_browser_login_dance_and_mid_stream_session_expiry(tmp_path: Path) -> None:
    """Scenario 12: login lands `pending`; `guest` reaches the board read-only;
    `contributor` gets the write control; a session expiring mid-SSE surfaces as a
    login redirect within one reconnect cycle."""
    from playwright.sync_api import expect, sync_playwright

    bin_dir = require_stub_idp()

    idp_port = _free_port()
    hub_port = _free_port()
    hub_dir = tmp_path / "hub"
    chunk_id = "ch_01e2eloginchunk00000000000"
    graph_id = "gr_01e2eloginchunk00000000000"

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

            # A fresh identity mints as `pending` — the bottom, no-access role (#210)
            # — the lobby is the authenticated proof the dance worked.
            expect(page.get_by_test_id("pending-lobby")).to_be_visible()
            expect(page.get_by_test_id("pending-lobby-username")).to_contain_text("octocat")

            # --- 2. Promoted to `guest`, the board is reachable read-only (#210) ---
            _terminate(proc)
            _set_role(hub_dir, "octocat", "guest")
            _seed_not_ready_chunk(hub_dir, chunk_id=chunk_id, graph_id=graph_id)
            proc = _start_hub(hub_dir, hub_port)

            page.reload(wait_until="load")
            expect(page.get_by_test_id("board-header")).to_be_visible()
            expect(page.get_by_test_id("board-shell")).to_be_visible()
            expect(page.get_by_test_id("pending-lobby")).to_have_count(0)
            # The seeded chunk's card is visible — a guest reads everything — but its
            # Promote control is not.
            expect(page.locator(f'[data-chunk="{chunk_id}"]')).to_be_visible()
            expect(page.get_by_test_id("promote-chunk")).to_have_count(0)

            # --- 3. Promoted to `contributor`, the write control appears -----------
            _terminate(proc)
            _set_role(hub_dir, "octocat", "contributor")
            proc = _start_hub(hub_dir, hub_port)

            page.reload(wait_until="load")
            expect(page.get_by_test_id("board-shell")).to_be_visible()
            expect(page.get_by_test_id("promote-chunk")).to_be_visible()

            # --- 4. Expire the session mid-stream; the hub restart force-drops the
            # SSE connection and the client's reconnect discovers the invalid session.
            _terminate(proc)
            _expire_session(hub_dir)
            proc = _start_hub(hub_dir, hub_port)

            expect(page.get_by_test_id("login-page")).to_be_visible()
            expect(page.get_by_test_id(f"login-provider-{_PROVIDER_NAME}")).to_be_visible()
        finally:
            browser.close()
            _terminate(proc)
