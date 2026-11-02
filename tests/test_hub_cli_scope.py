"""``blizzard hub scope create|list|edit|retire|enable`` (unit tier) — pure clients of
the scope routes, driven here with the client factory stubbed, the
``tests/test_hub_cli_graph.py`` shape."""

from __future__ import annotations

import httpx2
import pytest

from blizzard.hub.cli import hub as hub_group
from tests.worker_http import bind_stubs


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
            request = httpx2.Request("GET", "http://hub.test")
            response = httpx2.Response(self.status_code, request=request)
            raise httpx2.HTTPStatusError("error", request=request, response=response)


@pytest.mark.unit
def test_scope_create_posts_slug_and_description() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float, headers: dict[str, str] | None = None) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"slug": "blizzard", "description": "the repo", "retired": False})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group,
        ["scope", "create", "blizzard", "--description", "the repo"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/scopes", {"slug": "blizzard", "description": "the repo"})]
    assert "blizzard" in result.output


@pytest.mark.unit
def test_scope_list_prints_each_row() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            [
                {"slug": "alpha", "description": "d", "retired": False, "created_at": "t0"},
                {"slug": "beta", "description": "", "retired": True, "created_at": "t1"},
            ],
        )

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["scope", "list"])

    assert result.exit_code == 0, result.output
    assert "alpha" in result.output
    assert "beta" in result.output
    assert "retired" in result.output


@pytest.mark.unit
def test_scope_list_on_no_scopes_prints_a_friendly_message() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [])

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["scope", "list"])

    assert result.exit_code == 0, result.output
    assert "no scopes yet" in result.output


@pytest.mark.unit
def test_scope_edit_patches_the_description() -> None:
    calls: list[tuple[str, object]] = []

    def fake_patch(url: str, *, json: object, timeout: float, headers: dict[str, str] | None = None) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(200, {"slug": "blizzard", "description": "new", "retired": False})

    bound = bind_stubs(patch=fake_patch)
    result = bound.runner.invoke(
        hub_group,
        ["scope", "edit", "blizzard", "--description", "new"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/scopes/blizzard", {"description": "new"})]


@pytest.mark.unit
def test_scope_retire_posts_to_the_retire_endpoint() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float, headers: dict[str, str] | None = None) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"slug": "blizzard", "retired": True})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group, ["scope", "retire", "blizzard", "--by", "paul"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/scopes/blizzard/retire", {"by": "paul"})]
    assert "retired" in result.output


@pytest.mark.unit
def test_scope_enable_posts_to_the_enable_endpoint() -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float, headers: dict[str, str] | None = None) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"slug": "blizzard", "retired": False})

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(
        hub_group, ["scope", "enable", "blizzard"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/scopes/blizzard/enable", {"by": "operator"})]
    assert "enabled" in result.output


@pytest.mark.unit
def test_scope_retire_maps_an_unknown_scope() -> None:
    def fake_post(url: str, *, json: object, timeout: float, headers: dict[str, str] | None = None) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(post=fake_post)
    result = bound.runner.invoke(hub_group, ["scope", "retire", "ghost"])

    assert result.exit_code != 0
    assert "ghost" in result.output


@pytest.mark.unit
def test_scope_show_prints_the_whole_record() -> None:
    urls: list[str] = []

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        urls.append(url)
        return _FakeResponse(
            200,
            {"slug": "blizzard", "description": "the repo", "created_at": "t0", "retired": False, "revision": 3},
        )

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["scope", "get", "blizzard"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert urls == ["http://hub.local:8421/api/scopes/blizzard"]
    assert "blizzard  enabled" in result.output
    assert "description the repo" in result.output
    assert "revision 3" in result.output
    assert "created t0" in result.output


@pytest.mark.unit
def test_scope_show_maps_an_unknown_scope() -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["scope", "get", "no-such"])

    assert result.exit_code != 0
    assert "unknown scope no-such" in result.output
