"""``blizzard hub routine create|list|show|edit`` (unit tier) — pure clients of the
routine routes, driven here with ``httpx`` stubbed (blizzard#389), the
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
def test_routine_create_posts_name_graph_and_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(
            201,
            {
                "routine_id": "rtn_1",
                "name": "nightly",
                "graph_name": "alpha",
                "default_scope_slug": "blizzard",
                "default_model": [],
                "default_effort": None,
            },
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group,
        ["routine", "create", "nightly", "alpha", "blizzard"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "http://hub.local:8421/api/routines",
            {
                "name": "nightly",
                "graph_name": "alpha",
                "default_scope_slug": "blizzard",
                "default_model": [],
                "default_effort": None,
            },
        )
    ]
    assert "rtn_1" in result.output


@pytest.mark.unit
def test_routine_create_collects_repeated_model_options(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _FakeResponse(201, {"routine_id": "rtn_1", "name": "nightly"})

    monkeypatch.setattr(httpx, "post", fake_post)
    CliRunner().invoke(
        hub_group,
        ["routine", "create", "nightly", "alpha", "blizzard", "--model", "a", "--model", "b", "--effort", "high"],
    )

    assert calls[0][1]["default_model"] == ["a", "b"]  # type: ignore[index]
    assert calls[0][1]["default_effort"] == "high"  # type: ignore[index]


@pytest.mark.unit
def test_routine_create_maps_a_422_to_a_click_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(422, {"detail": "no enabled graph named 'ghost' exists"})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["routine", "create", "nightly", "ghost", "blizzard"])

    assert result.exit_code != 0
    assert "ghost" in result.output


@pytest.mark.unit
def test_routine_list_prints_each_row(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            [{"routine_id": "rtn_1", "name": "nightly", "graph_name": "alpha", "default_scope_slug": "blizzard"}],
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["routine", "list"])

    assert result.exit_code == 0, result.output
    assert "rtn_1" in result.output
    assert "nightly" in result.output


@pytest.mark.unit
def test_routine_list_on_no_routines_prints_a_friendly_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["routine", "list"])

    assert result.exit_code == 0, result.output
    assert "no routines yet" in result.output


@pytest.mark.unit
def test_routine_show_prints_the_whole_record(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "routine_id": "rtn_1",
                "name": "nightly",
                "graph_name": "alpha",
                "default_scope_slug": "blizzard",
                "default_model": ["basic"],
                "default_effort": "low",
            },
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["routine", "show", "rtn_1"])

    assert result.exit_code == 0, result.output
    assert "nightly" in result.output
    assert "blizzard" in result.output
    assert "basic" in result.output


@pytest.mark.unit
def test_routine_show_maps_an_unknown_routine(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["routine", "show", "rtn_ghost"])

    assert result.exit_code != 0
    assert "rtn_ghost" in result.output


@pytest.mark.unit
def test_routine_edit_reads_the_current_name_then_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    get_calls: list[str] = []
    patch_calls: list[tuple[str, object]] = []

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        get_calls.append(url)
        return _FakeResponse(200, {"routine_id": "rtn_1", "name": "nightly", "graph_name": "alpha"})

    def fake_patch(url: str, *, json: object, timeout: float) -> _FakeResponse:
        patch_calls.append((url, json))
        return _FakeResponse(200, {"routine_id": "rtn_1", "name": "nightly", "graph_name": "beta"})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "patch", fake_patch)
    result = CliRunner().invoke(
        hub_group,
        ["routine", "edit", "rtn_1", "--graph", "beta", "--scope", "blizzard"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert get_calls == ["http://hub.local:8421/api/routines/rtn_1"]
    assert patch_calls == [
        (
            "http://hub.local:8421/api/routines/rtn_1",
            {
                "name": "nightly",
                "graph_name": "beta",
                "default_scope_slug": "blizzard",
                "default_model": [],
                "default_effort": None,
            },
        )
    ]
