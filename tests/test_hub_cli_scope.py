"""``blizzard hub scope create|list|edit|retire|enable`` (unit tier) — pure clients of
the scope routes, driven here with ``httpx`` stubbed (blizzard#389), the
``tests/test_hub_cli_graph.py`` shape."""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group


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


@pytest.mark.unit
def test_scope_create_posts_slug_and_description(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"slug": "blizzard", "description": "the repo", "retired": False})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["scope", "create", "blizzard", "--description", "the repo"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/scopes", {"slug": "blizzard", "description": "the repo"})]
    assert "blizzard" in result.output


@pytest.mark.unit
def test_scope_list_prints_each_row(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            [
                {"slug": "alpha", "description": "d", "retired": False, "created_at": "t0"},
                {"slug": "beta", "description": "", "retired": True, "created_at": "t1"},
            ],
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["scope", "list"])

    assert result.exit_code == 0, result.output
    assert "alpha" in result.output
    assert "beta" in result.output
    assert "retired" in result.output


@pytest.mark.unit
def test_scope_list_on_no_scopes_prints_a_friendly_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["scope", "list"])

    assert result.exit_code == 0, result.output
    assert "no scopes yet" in result.output


@pytest.mark.unit
def test_scope_edit_patches_the_description(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, {"slug": "blizzard", "description": "new", "retired": False})

    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(
        hub_group,
        ["scope", "edit", "blizzard", "--description", "new"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/scopes/blizzard", {"description": "new"})]


@pytest.mark.unit
def test_scope_retire_posts_to_the_retire_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"slug": "blizzard", "retired": True})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["scope", "retire", "blizzard", "--by", "paul"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/scopes/blizzard/retire", {"by": "paul"})]
    assert "retired" in result.output


@pytest.mark.unit
def test_scope_enable_posts_to_the_enable_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"slug": "blizzard", "retired": False})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["scope", "enable", "blizzard"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/scopes/blizzard/enable", {"by": "operator"})]
    assert "enabled" in result.output


@pytest.mark.unit
def test_scope_retire_maps_an_unknown_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["scope", "retire", "ghost"])

    assert result.exit_code != 0
    assert "ghost" in result.output
