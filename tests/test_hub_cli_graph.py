"""``blizzard hub graph list|retire|enable`` (unit tier) — pure clients of the graph
lifecycle endpoints, driven here with ``httpx`` stubbed (issue #101).
"""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

import blizzard.hub.cli as hub_cli
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
def test_graph_list_prints_each_row(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(
            200,
            [
                {"graph_id": "gr_new", "name": "alpha", "effective": True, "retired": False, "created_at": "t1"},
                {"graph_id": "gr_old", "name": "alpha", "effective": False, "retired": True, "created_at": "t0"},
            ],
        )

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["graph", "list"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/graphs"]
    assert "gr_new" in result.output
    assert "effective" in result.output
    assert "gr_old" in result.output
    assert "retired" in result.output


@pytest.mark.unit
def test_graph_list_on_no_graphs_prints_a_friendly_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [])

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["graph", "list"])

    assert result.exit_code == 0, result.output
    assert "no graphs minted yet" in result.output


@pytest.mark.unit
def test_graph_retire_posts_to_the_retire_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"graph_id": "gr_1", "retired": True})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["graph", "retire", "gr_1", "--by", "paul"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/graphs/gr_1/retire", {"by": "paul"})]
    assert "retired" in result.output


@pytest.mark.unit
def test_graph_enable_posts_to_the_enable_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(202, {"graph_id": "gr_1", "retired": False})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "enable", "gr_1"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert calls == [("http://hub.local:8421/api/graphs/gr_1/enable", {"by": "operator"})]
    assert "enabled" in result.output


@pytest.mark.unit
def test_graph_retire_maps_an_unknown_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["graph", "retire", "gr_ghost"])

    assert result.exit_code != 0
    assert "gr_ghost" in result.output
