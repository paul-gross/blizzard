"""``blizzard hub record-marker`` — the mid-run marker callback CLI (issue #65/#230).

A pure client of the injected ``BZ_HUB_MARKER_CALLBACK_URL`` — this file stubs
``httpx.post`` (the same monkeypatch seam every other CLI unit test uses) to prove the
command authorizes its write with the run's marker capability token
(``BZ_HUB_MARKER_TOKEN``) via ``X-Blizzard-Marker-Token``, and refuses to post at all
when either the callback URL or the token is missing from the environment.
"""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

import blizzard.hub.cli as hub_cli
from blizzard.hub.cli import hub as hub_group

pytestmark = pytest.mark.unit

_CALLBACK_URL = "http://callback/hub-markers"
_MARKER_TOKEN = "test-marker-token"


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        pass


def _set_env(monkeypatch: pytest.MonkeyPatch, *, callback_url: str | None, token: str | None) -> None:
    if callback_url is None:
        monkeypatch.delenv("BZ_HUB_MARKER_CALLBACK_URL", raising=False)
    else:
        monkeypatch.setenv("BZ_HUB_MARKER_CALLBACK_URL", callback_url)
    if token is None:
        monkeypatch.delenv("BZ_HUB_MARKER_TOKEN", raising=False)
    else:
        monkeypatch.setenv("BZ_HUB_MARKER_TOKEN", token)


def test_record_marker_sends_the_token_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, callback_url=_CALLBACK_URL, token=_MARKER_TOKEN)
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> _FakeResponse:
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse()

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)

    result = CliRunner().invoke(hub_group, ["record-marker", "merged/acme/widget", "sha1"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["url"] == _CALLBACK_URL
    assert calls[0]["json"] == {"name": "merged/acme/widget", "content": "sha1"}
    assert calls[0]["headers"]["X-Blizzard-Marker-Token"] == _MARKER_TOKEN


def test_record_marker_refuses_without_a_callback_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, callback_url=None, token=_MARKER_TOKEN)
    monkeypatch.setattr(hub_cli.httpx, "post", lambda *a, **k: pytest.fail("must not post without a callback URL"))

    result = CliRunner().invoke(hub_group, ["record-marker", "merged/acme/widget", "sha1"])

    assert result.exit_code != 0
    assert "BZ_HUB_MARKER_CALLBACK_URL" in result.output


def test_record_marker_refuses_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, callback_url=_CALLBACK_URL, token=None)
    monkeypatch.setattr(hub_cli.httpx, "post", lambda *a, **k: pytest.fail("must not post without a token"))

    result = CliRunner().invoke(hub_group, ["record-marker", "merged/acme/widget", "sha1"])

    assert result.exit_code != 0
    assert "BZ_HUB_MARKER_TOKEN" in result.output
