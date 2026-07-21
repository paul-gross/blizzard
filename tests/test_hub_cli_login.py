"""``blizzard hub login`` / ``logout`` + the actionable-401 mapping (unit tier, issue
#96) — driven with ``cli_login``'s own mechanics stubbed (proven for real in
``tests/test_cli_login_mechanics.py``) and ``httpx``/``session_store`` stubbed the
same way every other CLI unit test stubs the hub, so no real hub or browser is
needed here.
"""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

import blizzard.hub.cli as hub_cli
from blizzard.hub.cli import hub as hub_group

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, status_code: int, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)  # type: ignore[arg-type]


def test_login_stores_the_loopback_token(monkeypatch: pytest.MonkeyPatch) -> None:
    saved: dict[str, str] = {}
    monkeypatch.setattr(hub_cli.cli_login, "loopback_login", lambda base, *, open_browser: "the-token")
    monkeypatch.setattr(hub_cli.session_store, "save_session", lambda base, token: saved.update({base: token}))

    result = CliRunner().invoke(hub_group, ["login"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert saved == {"http://hub.local:8421": "the-token"}
    assert "logged in" in result.output


def test_login_paste_flag_uses_the_paste_code_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_paste(base: str, *, prompt_for_code) -> str:  # type: ignore[no-untyped-def]
        calls["base"] = base
        calls["code"] = prompt_for_code()
        return "pasted-token"

    monkeypatch.setattr(hub_cli.cli_login, "paste_code_login", fake_paste)
    monkeypatch.setattr(hub_cli.click, "prompt", lambda *a, **k: "the-pasted-code")
    saved: dict[str, str] = {}
    monkeypatch.setattr(hub_cli.session_store, "save_session", lambda base, token: saved.update({base: token}))

    result = CliRunner().invoke(hub_group, ["login", "--paste"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls["code"] == "the-pasted-code"
    assert saved == {"http://hub.local:8421": "pasted-token"}


def test_login_reports_a_login_error_as_a_clean_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_loopback(base: str, *, open_browser: bool) -> str:
        raise hub_cli.cli_login.LoginError("timed out waiting for the browser login to complete")

    monkeypatch.setattr(hub_cli.cli_login, "loopback_login", fake_loopback)

    result = CliRunner().invoke(hub_group, ["login"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code != 0
    assert "login failed" in result.output


def test_logout_deletes_the_local_session_and_calls_the_revoke_route(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(204)

    deleted: list[str] = []
    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    monkeypatch.setattr(hub_cli.session_store, "delete_session", lambda base: deleted.append(base))

    result = CliRunner().invoke(hub_group, ["logout"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/auth/logout"]
    assert deleted == ["http://hub.local:8421"]


def test_logout_still_cleans_up_locally_when_the_hub_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> _FakeResponse:
        raise httpx.ConnectError("connection refused")

    deleted: list[str] = []
    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    monkeypatch.setattr(hub_cli.session_store, "delete_session", lambda base: deleted.append(base))

    result = CliRunner().invoke(hub_group, ["logout"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert deleted == ["http://hub.local:8421"]


def test_a_bare_401_maps_to_the_actionable_login_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float, params: dict[str, str] | None = None) -> _FakeResponse:
        return _FakeResponse(401, {"detail": "authentication required"})

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)

    result = CliRunner().invoke(hub_group, ["chunk", "list"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code != 0
    assert "blizzard hub login" in result.output


def test_request_attaches_the_stored_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_get(url: str, *, timeout: float, headers: dict[str, str] | None = None, params=None) -> _FakeResponse:
        captured["headers"] = headers
        return _FakeResponse(200, [])

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    monkeypatch.setattr(hub_cli.session_store, "load_session", lambda base: "stored-token")

    result = CliRunner().invoke(hub_group, ["chunk", "list"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert captured["headers"] == {"Authorization": "Bearer stored-token"}


def test_request_omits_the_headers_kwarg_when_no_session_is_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float, params=None) -> _FakeResponse:
        # No `headers` kwarg accepted at all — proves `_request` doesn't pass one when
        # no session is stored (every existing CLI unit test's fake relies on this).
        return _FakeResponse(200, [])

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    monkeypatch.setattr(hub_cli.session_store, "load_session", lambda base: None)

    result = CliRunner().invoke(hub_group, ["chunk", "list"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
