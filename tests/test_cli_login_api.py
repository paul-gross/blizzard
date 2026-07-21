"""``client=cli``'s own authorize branch + ``POST /api/auth/cli/token`` — the CLI's
PKCE loopback/paste-code login (component tier, issue #96).

Drives the hub-as-IdP surface (``hub/api/idp.py``) directly with a fake browser
(``hub.client``, which already carries the ``bz_session`` cookie a real browser would):
``authorize`` delivers a *code* rather than a runner-style JWT for ``client=cli``
(decision D6), and the code is redeemed at ``POST /api/auth/cli/token`` for a hub
session token — the exact bearer ``blizzard hub cli.py``'s ``_request`` attaches on
every later verb (proven here against ``GET /api/me``).
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from blizzard.auth_core import Role
from blizzard.hub.api.idp import CLI_OOB_REDIRECT_URI
from blizzard.hub.auth.pkce import challenge_from_verifier
from blizzard.hub.config import AUTH_MODE_NONE, AUTH_MODE_OAUTH
from tests.support import HubHarness, build_hub, seed_session, seed_user

pytestmark = pytest.mark.component

_LOOPBACK_REDIRECT = "http://127.0.0.1:54321/callback"
_VERIFIER = "a-fixed-test-verifier-with-enough-entropy-1234567890"
_CHALLENGE = challenge_from_verifier(_VERIFIER)


def _login_as(hub: HubHarness, *, username: str = "alice", role: Role = Role.CONTRIBUTOR) -> None:
    user = seed_user(hub, username=username, role=role, email=f"{username}@example.com")
    session = seed_session(hub, user)
    hub.client.cookies.set("bz_session", session)


def _authorize_cli(
    hub: HubHarness,
    *,
    redirect_uri: str = _LOOPBACK_REDIRECT,
    code_challenge: str | None = _CHALLENGE,
    code_challenge_method: str | None = "S256",
    state: str = "outer-state",
):
    params = {"client": "cli", "redirect_uri": redirect_uri, "state": state}
    if code_challenge is not None:
        params["code_challenge"] = code_challenge
    if code_challenge_method is not None:
        params["code_challenge_method"] = code_challenge_method
    return hub.client.get("/api/auth/authorize", params=params, follow_redirects=False)


def test_authorize_404s_under_none_mode_for_cli(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_NONE)
    resp = _authorize_cli(hub)
    assert resp.status_code == 404


def test_authorize_rejects_missing_code_challenge(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _login_as(hub)
    resp = _authorize_cli(hub, code_challenge=None)
    assert resp.status_code == 400


def test_authorize_rejects_a_non_s256_method(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _login_as(hub)
    resp = _authorize_cli(hub, code_challenge_method="plain")
    assert resp.status_code == 400


def test_authorize_rejects_a_non_loopback_non_oob_redirect_uri(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _login_as(hub)
    resp = _authorize_cli(hub, redirect_uri="https://evil.example/callback")
    assert resp.status_code == 400


def test_authorize_cli_delivers_a_code_via_loopback_redirect(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _login_as(hub)
    resp = _authorize_cli(hub)
    assert resp.status_code == 302
    location = urlparse(resp.headers["location"])
    query = parse_qs(location.query)
    assert query["state"] == ["outer-state"]
    assert "code" in query
    # Never a query-string *token* (only a short-lived code) — this is exactly what
    # AC's "never a query string" concerns the token, not the code.


def test_authorize_cli_paste_code_renders_a_page(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _login_as(hub)
    resp = _authorize_cli(hub, redirect_uri=CLI_OOB_REDIRECT_URI)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<pre" in resp.text


def _mint_code(hub: HubHarness, *, redirect_uri: str = _LOOPBACK_REDIRECT) -> str:
    resp = _authorize_cli(hub, redirect_uri=redirect_uri)
    if redirect_uri == CLI_OOB_REDIRECT_URI:
        return resp.text.split("<pre")[1].split(">")[1].split("<")[0]
    query = parse_qs(urlparse(resp.headers["location"]).query)
    return query["code"][0]


def test_cli_token_exchange_yields_a_working_bearer_session(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _login_as(hub, username="alice", role=Role.CONTRIBUTOR)
    code = _mint_code(hub)

    resp = hub.client.post(
        "/api/auth/cli/token",
        json={"code": code, "code_verifier": _VERIFIER, "redirect_uri": _LOOPBACK_REDIRECT},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    assert token

    me = hub.client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


def test_cli_token_exchange_rejects_a_wrong_verifier(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _login_as(hub)
    code = _mint_code(hub)

    resp = hub.client.post(
        "/api/auth/cli/token",
        json={"code": code, "code_verifier": "not-the-right-verifier", "redirect_uri": _LOOPBACK_REDIRECT},
    )
    assert resp.status_code == 400


def test_cli_token_exchange_rejects_a_mismatched_redirect_uri(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _login_as(hub)
    code = _mint_code(hub)

    resp = hub.client.post(
        "/api/auth/cli/token",
        json={"code": code, "code_verifier": _VERIFIER, "redirect_uri": "http://127.0.0.1:9999/callback"},
    )
    assert resp.status_code == 400


def test_cli_token_exchange_is_single_use(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _login_as(hub)
    code = _mint_code(hub)
    body = {"code": code, "code_verifier": _VERIFIER, "redirect_uri": _LOOPBACK_REDIRECT}

    first = hub.client.post("/api/auth/cli/token", json=body)
    assert first.status_code == 200

    second = hub.client.post("/api/auth/cli/token", json=body)
    assert second.status_code == 400


def test_cli_token_exchange_rejects_an_unknown_code(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    resp = hub.client.post(
        "/api/auth/cli/token",
        json={"code": "no-such-code", "code_verifier": _VERIFIER, "redirect_uri": _LOOPBACK_REDIRECT},
    )
    assert resp.status_code == 400


def test_cli_token_404s_under_none_mode(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_NONE)
    resp = hub.client.post(
        "/api/auth/cli/token",
        json={"code": "x", "code_verifier": "y", "redirect_uri": _LOOPBACK_REDIRECT},
    )
    assert resp.status_code == 404


def test_paste_code_flow_completes_the_same_login(tmp_path: Path) -> None:
    """The paste-code fallback end to end (no loopback listener involved at all): the
    OOB redirect form renders a code, and it exchanges exactly like the loopback one."""
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    _login_as(hub, username="bob", role=Role.CONTRIBUTOR)
    code = _mint_code(hub, redirect_uri=CLI_OOB_REDIRECT_URI)

    resp = hub.client.post(
        "/api/auth/cli/token",
        json={"code": code, "code_verifier": _VERIFIER, "redirect_uri": CLI_OOB_REDIRECT_URI},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]

    me = hub.client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "bob"
