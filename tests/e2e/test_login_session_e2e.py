"""Browser login dance + mid-stream session-expiry redirect.

Real Chromium over the served board under oauth: an uninvited login lands back on the login
page with the invite-only refusal; invited as `guest` — through the board's invitation page — the
person reads read-only; `contributor` gets the write control; a session expiring mid-SSE
redirects to login within one reconnect.
Skipped unless ``BLIZZARD_E2E=1``.
"""

from __future__ import annotations

import dataclasses
import os
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from blizzard.auth_core import Role
from blizzard.hub.config import AuthConfig, OAuthProviderConfig
from tests.config_inputs import load_hub_config
from tests.fleet_launchers import await_http, terminate
from tests.service.support import require_stub_idp, stub_idp
from tests.support import daemon_log_sink, free_port, invite

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
#: Deadline for a redirect driven by an SSE reconnect's own ``401`` — clears ``SseService``'s
#: backoff ladder (1s/2s/4s/8s/16s/30s, ~30s cumulative) plus a restarted daemon's startup.
_RECONNECT_401_TIMEOUT_MS = 45_000


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
        emails_authoritative=True,
    )
    config = load_hub_config(hub_dir)
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
    client = httpx2.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    try:
        await_http(proc, client, "/api/health", log=log)
    finally:
        client.close()
    return proc


def _db_path(hub_dir: Path) -> Path:
    return hub_dir / "data" / "hub.db"


def _set_role(hub_dir: Path, username: str, role: str) -> None:
    """Grant ``username`` ``role`` in the store's only tenant by direct store access, the same
    "mint what the suite cannot reach through a route" pattern this suite uses for fixture state."""
    con = sqlite3.connect(_db_path(hub_dir))
    try:
        con.execute(
            "INSERT INTO membership_facts (tenant_id, user_id, role, set_at, set_by)"
            " SELECT tenants.tenant_id, users.id, ?, ?, 'operator' FROM tenants, users WHERE users.username = ?",
            (role, datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f"), username),
        )
        con.commit()
    finally:
        con.close()


def _seed_not_ready_chunk(hub_dir: Path, *, chunk_id: str, graph_id: str) -> None:
    """One bare fixture chunk — no route, no facts — so the read-only board claim
    that a guest's Promote control stays hidden has a concrete card to assert against. Derives
    ``not_ready`` by construction: a chunk with no promote fact rests there. Both rows belong to
    the store's only tenant (the chunk to its only project), stamped the way ``_set_role`` stamps a
    membership."""
    con = sqlite3.connect(_db_path(hub_dir))
    try:
        con.execute(
            "INSERT INTO graphs (tenant_id, graph_id, name, entry_node_id, definition_yaml, created_at)"
            " SELECT tenants.tenant_id, ?, ?, ?, ?, ? FROM tenants",
            (graph_id, "g", "nd_1", "", "2026-07-13 00:00:00.000000"),
        )
        con.execute(
            "INSERT INTO chunks (tenant_id, project_id, chunk_id, graph_id, minted_at)"
            " SELECT projects.tenant_id, projects.project_id, ?, ?, ? FROM projects",
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
    """The `test_login_session_e2e` scenario: an uninvited login is refused; invited as `guest` the person reaches
    the board read-only;
    `contributor` gets the write control; a session expiring mid-SSE surfaces as a
    login redirect within one reconnect cycle."""
    from playwright.sync_api import expect, sync_playwright

    bin_dir = require_stub_idp()

    idp_port = free_port()
    hub_port = free_port()
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

            # The hub is invite-only: a person it has never seen is handed back to the login page
            # with the refusal, holding no session.
            page.wait_for_url("**/login?refusal=invite_only")
            expect(page.get_by_test_id("login-page")).to_be_visible()
            expect(page.get_by_test_id("login-refusal")).to_have_text(
                "You don't have access to anything here yet. Reach out to your administrator for access."
            )
            expect(page.get_by_test_id("board-header")).to_have_count(0)
            assert not [cookie for cookie in page.context.cookies() if cookie.get("name") == "bz_session"]

            # --- 2. Invited as `guest`, the board is reachable read-only -------------
            terminate(proc)
            _seed_not_ready_chunk(hub_dir, chunk_id=chunk_id, graph_id=graph_id)
            proc = _start_hub(hub_dir, hub_port)
            token = invite(hub_dir, "octocat@example.com", role=Role.GUEST)
            # The invitation's link opens the board's landing page; its provider button carries the token
            # through the dance, and accepting it signs the person in to the board.
            page.goto(f"http://127.0.0.1:{hub_port}/invite/{token}", wait_until="load")
            expect(page.get_by_test_id("invite-page")).to_be_visible()
            invite_button = page.get_by_test_id(f"login-provider-{_PROVIDER_NAME}")
            expect(invite_button).to_have_attribute("href", re.compile(rf"&invite={re.escape(token)}$"))
            invite_button.click()

            expect(page.get_by_test_id("board-header")).to_be_visible()
            expect(page.get_by_test_id("board-shell")).to_be_visible()
            expect(page.get_by_test_id("no-access")).to_have_count(0)
            assert [cookie for cookie in page.context.cookies() if cookie.get("name") == "bz_session"]
            # The seeded chunk's card is visible — a guest reads everything — but its
            # Promote control is not.
            expect(page.locator(f'[data-chunk="{chunk_id}"]')).to_be_visible()
            expect(page.get_by_test_id("promote-chunk")).to_have_count(0)

            # --- 3. Promoted to `contributor`, the write control appears -----------
            terminate(proc)
            _set_role(hub_dir, "octocat", "contributor")
            proc = _start_hub(hub_dir, hub_port)

            page.reload(wait_until="load")
            expect(page.get_by_test_id("board-shell")).to_be_visible()
            expect(page.get_by_test_id("promote-chunk")).to_be_visible()

            # --- 4. Expire the session mid-stream; the hub restart force-drops the
            # SSE connection and the client's reconnect discovers the invalid session.
            terminate(proc)
            _expire_session(hub_dir)
            proc = _start_hub(hub_dir, hub_port)

            # Which reconnect attempt carries the 401 is not fixed, so this waits the
            # ladder out (_RECONNECT_401_TIMEOUT_MS); the file's 20s default sits under it.
            expect(page.get_by_test_id("login-page")).to_be_visible(timeout=_RECONNECT_401_TIMEOUT_MS)
            expect(page.get_by_test_id(f"login-provider-{_PROVIDER_NAME}")).to_be_visible()
        finally:
            browser.close()
            terminate(proc)
