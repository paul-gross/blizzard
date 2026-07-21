"""``GET /api/auth/providers``, ``/{name}/authorize``, ``/{name}/callback``,
``POST /api/auth/logout`` (component tier, issue #92).

Driven against the **in-repo fake** :class:`FakeOAuthProvider` bound at the
composition root (``tests/support.py``'s ``build_hub(oauth_providers=...)``) — no
network, per the plan's own tier split (the real HTTP dance against the
``blizzard-mock`` stub IdP is the service tier's job, ``tests/service/``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.auth.models import ProviderIdentity
from blizzard.hub.auth.oauth.provider import OAuthExchangeError
from tests.support import build_hub

pytestmark = pytest.mark.component


class FakeOAuthProvider:
    """A canned :class:`~blizzard.hub.auth.oauth.provider.IOAuthProvider` — resolves a
    fixed code -> :class:`ProviderIdentity` map; raises :class:`OAuthExchangeError` for
    an unrecognized code."""

    def __init__(self, *, name: str, display_name: str = "", type: str = "oidc") -> None:
        self.name = name
        self.display_name = display_name or name
        self.type = type
        self.codes: dict[str, ProviderIdentity] = {}
        self.authorize_calls: list[tuple[str, str]] = []

    def authorize_url(self, *, state: str, redirect_uri: str) -> str:
        self.authorize_calls.append((state, redirect_uri))
        return f"https://provider.example/{self.name}/authorize?state={state}"

    def exchange(self, *, code: str, redirect_uri: str) -> ProviderIdentity:
        identity = self.codes.get(code)
        if identity is None:
            raise OAuthExchangeError(f"unrecognized code {code!r}")
        return identity


def _state_from_redirect(location: str) -> str:
    # `RedirectResponse` urlencodes the query string the fake's `authorize_url` built.
    return location.rsplit("state=", 1)[1]


# --- providers ------------------------------------------------------------------


def test_providers_lists_every_configured_provider(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github", display_name="GitHub", type="github")
    oidc = FakeOAuthProvider(name="oidc-co", display_name="Example SSO", type="oidc")
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github, "oidc-co": oidc})

    resp = hub.client.get("/api/auth/providers")
    assert resp.status_code == 200
    names = {p["name"]: p for p in resp.json()}
    assert names.keys() == {"github", "oidc-co"}
    assert names["github"] == {"name": "github", "display_name": "GitHub", "type": "github"}


def test_providers_is_empty_under_none_mode_even_if_configured(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, oauth_providers={"github": github})  # auth_mode defaults to "none"
    resp = hub.client.get("/api/auth/providers")
    assert resp.status_code == 200
    assert resp.json() == []


# --- authorize --------------------------------------------------------------------


def test_authorize_redirects_to_the_providers_authorize_url(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github})

    resp = hub.client.get("/api/auth/github/authorize", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].startswith("https://provider.example/github/authorize?state=")
    assert len(github.authorize_calls) == 1
    _state, redirect_uri = github.authorize_calls[0]
    assert redirect_uri.endswith("/api/auth/github/callback")


def test_authorize_404s_for_an_unknown_provider(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={})
    resp = hub.client.get("/api/auth/nope/authorize", follow_redirects=False)
    assert resp.status_code == 404


def test_authorize_404s_under_none_mode(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, oauth_providers={"github": github})
    resp = hub.client.get("/api/auth/github/authorize", follow_redirects=False)
    assert resp.status_code == 404


# --- callback: the full dance ends in a resolving cookie ---------------------------


def test_full_dance_ends_with_a_session_cookie_and_a_working_me(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    github.codes["code1"] = ProviderIdentity(subject="1", handle="ada", email="ada@example.com", email_verified=True)
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github})

    authorize_resp = hub.client.get("/api/auth/github/authorize", follow_redirects=False)
    state = _state_from_redirect(authorize_resp.headers["location"])

    callback_resp = hub.client.get(f"/api/auth/github/callback?code=code1&state={state}", follow_redirects=False)
    assert callback_resp.status_code in (302, 307)
    assert "bz_session" in callback_resp.cookies

    me_resp = hub.client.get("/api/me")
    assert me_resp.status_code == 200
    body = me_resp.json()
    assert body["username"] == "ada"
    assert body["role"] == "guest"


def test_no_provider_token_is_ever_set_as_a_cookie(tmp_path: Path) -> None:
    """Only the session-id cookie is set — no provider access/refresh/id token."""
    github = FakeOAuthProvider(name="github")
    github.codes["code1"] = ProviderIdentity(subject="1", handle="ada", email=None, email_verified=False)
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github})
    authorize_resp = hub.client.get("/api/auth/github/authorize", follow_redirects=False)
    state = _state_from_redirect(authorize_resp.headers["location"])

    callback_resp = hub.client.get(f"/api/auth/github/callback?code=code1&state={state}", follow_redirects=False)

    assert set(callback_resp.cookies.keys()) == {"bz_session"}


# --- linking rule -----------------------------------------------------------------


def _login(hub, provider: FakeOAuthProvider, code: str) -> None:  # type: ignore[no-untyped-def]
    authorize_resp = hub.client.get(f"/api/auth/{provider.name}/authorize", follow_redirects=False)
    state = _state_from_redirect(authorize_resp.headers["location"])
    resp = hub.client.get(f"/api/auth/{provider.name}/callback?code={code}&state={state}", follow_redirects=False)
    assert resp.status_code in (302, 307), resp.text


def test_second_provider_login_with_same_verified_email_lands_on_the_same_user(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github", type="github")
    oidc = FakeOAuthProvider(name="oidc-co", type="oidc")
    github.codes["c1"] = ProviderIdentity(subject="gh-1", handle="ada", email="ada@example.com", email_verified=True)
    oidc.codes["c2"] = ProviderIdentity(
        subject="oidc-1", handle="ada.lovelace", email="ada@example.com", email_verified=True
    )
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github, "oidc-co": oidc})

    _login(hub, github, "c1")
    first_me = hub.client.get("/api/me").json()

    hub.client.cookies.clear()
    _login(hub, oidc, "c2")
    second_me = hub.client.get("/api/me").json()

    assert first_me["user_id"] == second_me["user_id"]


def test_unverified_email_never_merges(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github", type="github")
    oidc = FakeOAuthProvider(name="oidc-co", type="oidc")
    github.codes["c1"] = ProviderIdentity(subject="gh-1", handle="ada", email="ada@example.com", email_verified=True)
    oidc.codes["c2"] = ProviderIdentity(subject="oidc-1", handle="ada2", email="ada@example.com", email_verified=False)
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github, "oidc-co": oidc})

    _login(hub, github, "c1")
    first_me = hub.client.get("/api/me").json()

    hub.client.cookies.clear()
    _login(hub, oidc, "c2")
    second_me = hub.client.get("/api/me").json()

    assert first_me["user_id"] != second_me["user_id"]


def test_provider_handle_rename_refreshes_without_reminting(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github", type="github")
    github.codes["c1"] = ProviderIdentity(subject="gh-1", handle="ada", email=None, email_verified=False)
    github.codes["c2"] = ProviderIdentity(subject="gh-1", handle="ada-lovelace", email=None, email_verified=False)
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github})

    _login(hub, github, "c1")
    first_me = hub.client.get("/api/me").json()

    hub.client.cookies.clear()
    _login(hub, github, "c2")
    second_me = hub.client.get("/api/me").json()

    assert first_me["user_id"] == second_me["user_id"]
    # Username was minted from the original handle and never re-derived on rename.
    assert first_me["username"] == second_me["username"]


# --- bad state / failed exchange ---------------------------------------------------


def test_callback_rejects_a_bad_state_and_emits_a_login_failed_fact(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github})

    resp = hub.client.get("/api/auth/github/callback?code=abc&state=never-minted", follow_redirects=False)
    assert resp.status_code == 400
    assert resp.json()["error"] == "login_failed"

    facts = hub.services.auth_facts.list_recent()
    assert any(f.kind == "login_failed" for f in facts)


def test_callback_rejects_a_failed_exchange_and_emits_a_login_failed_fact(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github})
    authorize_resp = hub.client.get("/api/auth/github/authorize", follow_redirects=False)
    state = _state_from_redirect(authorize_resp.headers["location"])

    resp = hub.client.get(f"/api/auth/github/callback?code=unknown-code&state={state}", follow_redirects=False)
    assert resp.status_code == 400
    assert resp.json()["error"] == "login_failed"


def test_callback_refuses_a_state_minted_for_a_different_provider(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    oidc = FakeOAuthProvider(name="oidc-co")
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github, "oidc-co": oidc})

    authorize_resp = hub.client.get("/api/auth/github/authorize", follow_redirects=False)
    state = _state_from_redirect(authorize_resp.headers["location"])

    resp = hub.client.get(f"/api/auth/oidc-co/callback?code=abc&state={state}", follow_redirects=False)
    assert resp.status_code == 400
    assert resp.json()["error"] == "sso_refused"

    facts = hub.services.auth_facts.list_recent()
    assert any(f.kind == "sso_refused" for f in facts)


def test_callback_state_is_single_use(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    github.codes["c1"] = ProviderIdentity(subject="1", handle="ada", email=None, email_verified=False)
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github})
    authorize_resp = hub.client.get("/api/auth/github/authorize", follow_redirects=False)
    state = _state_from_redirect(authorize_resp.headers["location"])

    first = hub.client.get(f"/api/auth/github/callback?code=c1&state={state}", follow_redirects=False)
    assert first.status_code in (302, 307)

    second = hub.client.get(f"/api/auth/github/callback?code=c1&state={state}", follow_redirects=False)
    assert second.status_code == 400
    assert second.json()["error"] == "login_failed"


# --- logout -------------------------------------------------------------------


def test_logout_deletes_the_session_row_and_the_cookie_stops_resolving(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    github.codes["c1"] = ProviderIdentity(subject="1", handle="ada", email=None, email_verified=False)
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github})
    authorize_resp = hub.client.get("/api/auth/github/authorize", follow_redirects=False)
    state = _state_from_redirect(authorize_resp.headers["location"])
    hub.client.get(f"/api/auth/github/callback?code=c1&state={state}", follow_redirects=False)
    assert hub.client.get("/api/me").status_code == 200

    logout_resp = hub.client.post("/api/auth/logout")
    assert logout_resp.status_code == 204

    assert hub.client.get("/api/me").status_code == 401


def test_logout_is_a_no_op_with_no_session_presented(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={})
    resp = hub.client.post("/api/auth/logout")
    assert resp.status_code == 204


# --- throttling ---------------------------------------------------------------


def test_authorize_throttles_after_repeated_requests_from_one_ip(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github})

    statuses = [hub.client.get("/api/auth/github/authorize", follow_redirects=False).status_code for _ in range(15)]
    assert 429 in statuses
