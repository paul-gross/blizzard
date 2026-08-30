"""``blizzard hub record-marker`` — the mid-run marker callback CLI (issue #65/#230).

A pure client of the injected ``BZ_HUB_MARKER_CALLBACK_URL``: stubs ``httpx.post`` to
prove the command authorizes its write with the run's marker capability token via
:data:`~blizzard.hub.api.marker_auth._MARKER_TOKEN_HEADER` (issue #240), and refuses to
post when either the callback URL or the token is missing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from blizzard.auth_core import Role
from blizzard.hub.api.marker_auth import _MARKER_TOKEN_HEADER
from blizzard.hub.cli import hub as hub_group
from tests.support import build_hub, pointer_token, seed_session, seed_user

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


@pytest.mark.unit
def test_record_marker_sends_the_token_header(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, callback_url=_CALLBACK_URL, token=_MARKER_TOKEN)
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float) -> _FakeResponse:
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)

    result = CliRunner().invoke(hub_group, ["record-marker", "merged/acme/widget", "sha1"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["url"] == _CALLBACK_URL
    assert calls[0]["json"] == {"name": "merged/acme/widget", "content": "sha1"}
    assert calls[0]["headers"][_MARKER_TOKEN_HEADER] == _MARKER_TOKEN


@pytest.mark.unit
def test_record_marker_refuses_without_a_callback_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, callback_url=None, token=_MARKER_TOKEN)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not post without a callback URL"))

    result = CliRunner().invoke(hub_group, ["record-marker", "merged/acme/widget", "sha1"])

    assert result.exit_code != 0
    assert "BZ_HUB_MARKER_CALLBACK_URL" in result.output


@pytest.mark.unit
def test_record_marker_refuses_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_env(monkeypatch, callback_url=_CALLBACK_URL, token=None)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("must not post without a token"))

    result = CliRunner().invoke(hub_group, ["record-marker", "merged/acme/widget", "sha1"])

    assert result.exit_code != 0
    assert "BZ_HUB_MARKER_TOKEN" in result.output


@pytest.mark.component
def test_record_marker_is_accepted_by_a_real_oauth_hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI's own request, header and all, is accepted by a real hub with
    authentication genuinely on, and the marker lands durably. ``httpx.post`` is stubbed
    to forward to the app's own ``TestClient``, so the real route actually runs."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="root", role=Role.SUPERUSER)
    admin_token = seed_session(hub, admin)
    resp = hub.client.post(
        "/api/chunks",
        json={"tokens": [pointer_token({"source": "default", "ref": "1"})]},
        headers={"Cookie": f"bz_session={admin_token}"},
    )
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    node_id, epoch = "nd_merge", 1
    token = hub.services.marker_authority.issue(chunk_id, node_id=node_id, epoch=epoch)
    callback_url = f"/api/chunks/{chunk_id}/hub-markers?node_id={node_id}&epoch={epoch}"
    _set_env(monkeypatch, callback_url=callback_url, token=token)

    def relay_to_the_real_hub(url: str, *, json: dict[str, Any], headers: dict[str, str], timeout: float):
        return hub.client.post(url, json=json, headers=headers)

    monkeypatch.setattr(httpx, "post", relay_to_the_real_hub)

    result = CliRunner().invoke(hub_group, ["record-marker", "merged/acme-widget", "sha:abc123"])

    assert result.exit_code == 0, result.output
    names = {a.name for a in hub.services.chunks.load_artifacts(chunk_id)}
    assert "merged/acme-widget" in names
