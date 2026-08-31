"""``blizzard hub routine create|list|show|edit|trend`` (unit tier) — pure clients of the
routine routes, driven here with ``httpx`` stubbed (blizzard#389; ``trend`` is
blizzard#394 Phase 4), the ``tests/test_hub_cli_graph.py`` shape."""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group


@contextlib.contextmanager
def _local_timezone(tz: str) -> Iterator[None]:
    original = os.environ.get("TZ")
    os.environ["TZ"] = tz
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


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


@pytest.mark.unit
def test_routine_run_resolves_name_then_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    post_calls: list[tuple[str, object]] = []

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [{"routine_id": "rtn_1", "name": "gardening", "graph_name": "alpha"}])

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        post_calls.append((url, json))
        return _FakeResponse(
            201,
            {
                "chunk_id": "ch_1",
                "source": "hub",
                "ref": "1",
                "title": "gardening run (full)",
                "body": "Routine: gardening (graph: alpha)",
                "routine_name": "gardening",
                "scope_slug": "blizzard",
                "effective_mode": "full",
                "downgraded": False,
                "baseline_finding_set_id": None,
                "baseline_revisions": None,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["routine", "run", "gardening"], env={"BZ_HUB_URL": "http://hub.local:8421"})

    assert result.exit_code == 0, result.output
    assert post_calls == [
        ("http://hub.local:8421/api/routines/rtn_1/run", {"scope_slug": None, "mode": "full", "note": None})
    ]
    assert "ch_1" in result.output


@pytest.mark.unit
def test_routine_run_threads_scope_mode_and_note(monkeypatch: pytest.MonkeyPatch) -> None:
    post_calls: list[tuple[str, object]] = []

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [{"routine_id": "rtn_1", "name": "gardening"}])

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        post_calls.append((url, json))
        return _FakeResponse(
            201,
            {
                "chunk_id": "ch_1",
                "source": "hub",
                "ref": "1",
                "title": "t",
                "body": "b",
                "routine_name": "gardening",
                "scope_slug": "cold",
                "effective_mode": "delta",
                "downgraded": False,
                "baseline_finding_set_id": "fins_1",
                "baseline_revisions": {"blizzard": "a1b2c3d"},
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    CliRunner().invoke(
        hub_group,
        ["routine", "run", "gardening", "--scope", "cold", "--mode", "delta", "--note", "focus on auth"],
    )

    assert post_calls[0][1] == {"scope_slug": "cold", "mode": "delta", "note": "focus on auth"}


@pytest.mark.unit
def test_routine_run_names_a_downgrade_in_its_output(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [{"routine_id": "rtn_1", "name": "gardening"}])

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            201,
            {
                "chunk_id": "ch_1",
                "source": "hub",
                "ref": "1",
                "title": "t",
                "body": "b",
                "routine_name": "gardening",
                "scope_slug": "blizzard",
                "effective_mode": "full",
                "downgraded": True,
                "baseline_finding_set_id": None,
                "baseline_revisions": None,
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["routine", "run", "gardening", "--mode", "delta"])

    assert result.exit_code == 0, result.output
    assert "downgraded" in result.output.lower()


@pytest.mark.unit
def test_routine_run_unknown_name_raises_without_a_run_request(monkeypatch: pytest.MonkeyPatch) -> None:
    post_calls: list[str] = []

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [{"routine_id": "rtn_1", "name": "other"}])

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        post_calls.append(url)
        return _FakeResponse(201, {})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["routine", "run", "ghost"])

    assert result.exit_code != 0
    assert "ghost" in result.output
    assert post_calls == []


@pytest.mark.unit
def test_routine_run_maps_a_409_to_a_click_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [{"routine_id": "rtn_1", "name": "gardening"}])

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"existing_chunk_id": "ch_1", "source": "hub", "ref": "1"})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["routine", "run", "gardening"])

    assert result.exit_code != 0
    assert "conflict" in result.output


@pytest.mark.unit
def test_routine_run_maps_a_503_to_a_click_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A retired effective scope or an unresolvable graph refuses at 503 (D5), with no
    bespoke CLI handling; the generic HTTP-failure path still exits non-zero."""

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [{"routine_id": "rtn_1", "name": "gardening"}])

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(503, {"detail": "scope 'blizzard' is retired"})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["routine", "run", "gardening"])

    assert result.exit_code != 0


@pytest.mark.unit
def test_routine_run_mode_option_is_choice_restricted(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--mode`` never reaches the server with an unknown value — click's own
    ``Choice`` validation refuses it first, before any HTTP call."""
    result = CliRunner().invoke(hub_group, ["routine", "run", "gardening", "--mode", "sideways"])

    assert result.exit_code != 0
    assert "sideways" in result.output


@pytest.mark.unit
def test_routine_run_maps_a_422_to_a_click_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [{"routine_id": "rtn_1", "name": "gardening"}])

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(422, {"detail": "scope slug must match [a-z0-9-]+, got 'Not A Slug'"})

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["routine", "run", "gardening", "--scope", "Not A Slug"])

    assert result.exit_code != 0
    assert "Not A Slug" in result.output


_TREND_BODY = {
    "routine_name": "nightly",
    "since": "2026-01-01T00:00:00+00:00",
    "until": "2026-01-15T00:00:00+00:00",
    "period_days": 7,
    "periods": [
        {
            "period_start": "2026-01-01T00:00:00+00:00",
            "period_end": "2026-01-08T00:00:00+00:00",
            "created": 2,
            "exits": {"resolved": 1},
            "outflow": 1,
            "withdrawn": 0,
        }
    ],
    "age": {"boundary": "2026-01-01T00:00:00+00:00", "recent": 1, "older": 0, "unattributed": 1},
}


@pytest.mark.unit
def test_routine_trend_converts_local_since_until_and_boundary_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeResponse:
        calls.append(params)
        return _FakeResponse(200, _TREND_BODY)

    monkeypatch.setattr(httpx, "get", fake_get)

    with _local_timezone("America/New_York"):  # UTC-5 in January, no DST
        result = CliRunner().invoke(
            hub_group,
            [
                "routine",
                "trend",
                "nightly",
                "--since",
                "2026-01-01T10:00:00",
                "--until",
                "2026-01-15T10:00:00",
                "--introduced-boundary",
                "2026-01-01T10:00:00",
            ],
        )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "routine": "nightly",
            "since": "2026-01-01T15:00:00+00:00",
            "until": "2026-01-15T15:00:00+00:00",
            "introduced_boundary": "2026-01-01T15:00:00+00:00",
            "period_days": "7",
        }
    ]


@pytest.mark.unit
def test_routine_trend_period_days_defaults_to_seven(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeResponse:
        calls.append(params)
        return _FakeResponse(200, _TREND_BODY)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        hub_group,
        [
            "routine",
            "trend",
            "nightly",
            "--since",
            "2026-01-01T00:00:00",
            "--until",
            "2026-01-15T00:00:00",
            "--introduced-boundary",
            "2026-01-01T00:00:00",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["period_days"] == "7"


@pytest.mark.unit
def test_routine_trend_maps_a_422_to_a_click_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeResponse:
        return _FakeResponse(422, {"detail": "since 'garbage' is not a valid ISO-8601 instant"})

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        hub_group,
        [
            "routine",
            "trend",
            "nightly",
            "--since",
            "2026-01-01T00:00:00",
            "--until",
            "2026-01-15T00:00:00",
            "--introduced-boundary",
            "2026-01-01T00:00:00",
        ],
    )

    assert result.exit_code != 0
    assert "garbage" in result.output


@pytest.mark.unit
def test_routine_trend_renders_periods_and_age(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeResponse:
        return _FakeResponse(200, _TREND_BODY)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(
        hub_group,
        [
            "routine",
            "trend",
            "nightly",
            "--since",
            "2026-01-01T00:00:00",
            "--until",
            "2026-01-15T00:00:00",
            "--introduced-boundary",
            "2026-01-01T00:00:00",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "created=2" in result.output
    assert "outflow=1" in result.output
    assert "recent=1" in result.output
    assert "unattributed=1" in result.output
