"""``blizzard.hub.cli_login`` — the loopback listener + paste-code mechanics (unit
tier, issue #96).

No real hub: ``webbrowser.open``/``httpx.post`` are stubbed. The loopback case drives a
*real* HTTP GET at the ephemeral port ``loopback_login`` binds (from a background
thread, simulating "the browser completed the hub login and was redirected here") —
proving the listener itself, not just the seam around it.
"""

from __future__ import annotations

import threading
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from blizzard.hub import cli_login

pytestmark = pytest.mark.unit


class _FakeTokenResponse:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


def _hit_loopback_in_background(redirect_uri: str, *, code: str | None, state: str, error: str | None = None) -> None:
    params = {"state": state}
    if code is not None:
        params["code"] = code
    if error is not None:
        params["error"] = error

    def _hit() -> None:
        httpx.get(redirect_uri, params=params, timeout=5.0)

    threading.Thread(target=_hit, daemon=True).start()


def test_loopback_login_completes_and_exchanges(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_exchange = {}

    def fake_open(url: str) -> bool:
        query = parse_qs(urlparse(url).query)
        assert query["client"] == ["cli"]
        assert query["code_challenge_method"] == ["S256"]
        _hit_loopback_in_background(query["redirect_uri"][0], code="the-code", state=query["state"][0])
        return True

    def fake_post(url: str, *, json: dict, timeout: float) -> _FakeTokenResponse:
        captured_exchange.update(json)
        assert url == "http://hub.example/api/auth/cli/token"
        return _FakeTokenResponse(200, {"token": "the-session-token"})

    monkeypatch.setattr(cli_login.webbrowser, "open", fake_open)
    monkeypatch.setattr(cli_login.httpx, "post", fake_post)

    token = cli_login.loopback_login("http://hub.example")

    assert token == "the-session-token"
    assert captured_exchange["code"] == "the-code"
    assert captured_exchange["redirect_uri"].startswith("http://127.0.0.1:")
    # The verifier the exchange sends must hash to the challenge the authorize URL carried.
    assert cli_login.challenge_from_verifier(captured_exchange["code_verifier"])


def test_loopback_login_rejects_a_state_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_open(url: str) -> bool:
        query = parse_qs(urlparse(url).query)
        _hit_loopback_in_background(query["redirect_uri"][0], code="the-code", state="not-the-real-state")
        return True

    monkeypatch.setattr(cli_login.webbrowser, "open", fake_open)

    with pytest.raises(cli_login.LoginError):
        cli_login.loopback_login("http://hub.example")


def test_loopback_login_surfaces_a_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_open(url: str) -> bool:
        query = parse_qs(urlparse(url).query)
        _hit_loopback_in_background(query["redirect_uri"][0], code=None, state=query["state"][0], error="access_denied")
        return True

    monkeypatch.setattr(cli_login.webbrowser, "open", fake_open)

    with pytest.raises(cli_login.LoginError, match="access_denied"):
        cli_login.loopback_login("http://hub.example")


def test_loopback_login_times_out_when_no_browser_shows_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_login.webbrowser, "open", lambda url: True)

    with pytest.raises(cli_login.LoginError, match="timed out"):
        cli_login.loopback_login("http://hub.example", timeout=0.2)


def test_loopback_login_rejects_the_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_open(url: str) -> bool:
        query = parse_qs(urlparse(url).query)
        _hit_loopback_in_background(query["redirect_uri"][0], code="the-code", state=query["state"][0])
        return True

    def fake_post(url: str, *, json: dict, timeout: float) -> _FakeTokenResponse:
        return _FakeTokenResponse(400, {"detail": "invalid or expired code"})

    monkeypatch.setattr(cli_login.webbrowser, "open", fake_open)
    monkeypatch.setattr(cli_login.httpx, "post", fake_post)

    with pytest.raises(cli_login.LoginError, match="rejected the login exchange"):
        cli_login.loopback_login("http://hub.example")


def test_paste_code_login_exchanges_the_pasted_code(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_exchange = {}

    def fake_post(url: str, *, json: dict, timeout: float) -> _FakeTokenResponse:
        captured_exchange.update(json)
        return _FakeTokenResponse(200, {"token": "the-session-token"})

    monkeypatch.setattr(cli_login.httpx, "post", fake_post)

    token = cli_login.paste_code_login("http://hub.example", prompt_for_code=lambda: "pasted-code")

    assert token == "the-session-token"
    assert captured_exchange["code"] == "pasted-code"
    assert captured_exchange["redirect_uri"] == cli_login.OOB_REDIRECT_URI
