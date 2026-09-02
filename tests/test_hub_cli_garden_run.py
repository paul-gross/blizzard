"""``blizzard hub run list|show`` (unit tier) — pure clients of the two run-reading
routes, driven here with ``httpx`` stubbed, the ``tests/test_hub_cli_routine.py`` shape.
A component-tier case at the bottom proves the same two verbs against a real, wired app
(``tests/test_cli_analytics_events_component.py``'s relay shape)."""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.routines import Routine, RunMode
from blizzard.hub.domain.scopes import ScopeSlug
from blizzard.hub.domain.work import WorkItemAuthor
from tests.support import HubHarness, build_hub


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


def _run_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "chunk_id": "ch_1",
        "routine_name": "nightly",
        "scope_slug": "blizzard",
        "mode": "full",
        "minted_at": "2026-01-01T00:00:00+00:00",
        "outcome": "ready",
        "escalation": None,
        "delivered": [],
    }
    row.update(overrides)
    return row


def _run_delta(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "chunk_id": "ch_1",
        "routine_name": "nightly",
        "scope_slug": "blizzard",
        "mode": "full",
        "outcome": "ready",
        "escalation": None,
        "sets": [],
    }
    body.update(overrides)
    return body


# --------------------------------------------------------------------------- #
# `blizzard hub run list`


@pytest.mark.unit
def test_run_list_prints_each_row(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [_run_row()])

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["run", "list"])

    assert result.exit_code == 0, result.output
    assert "ch_1" in result.output
    assert "nightly/blizzard" in result.output
    assert "outcome=ready" in result.output


@pytest.mark.unit
def test_run_list_on_no_runs_prints_a_friendly_message(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeResponse:
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["run", "list"])

    assert result.exit_code == 0, result.output
    assert "no runs in this window" in result.output


@pytest.mark.unit
def test_run_list_converts_local_since_until_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeResponse:
        calls.append(params)
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx, "get", fake_get)
    with _local_timezone("America/New_York"):  # UTC-5 in January, no DST
        result = CliRunner().invoke(
            hub_group, ["run", "list", "--since", "2026-01-01T10:00:00", "--until", "2026-01-15T10:00:00"]
        )

    assert result.exit_code == 0, result.output
    assert calls == [{"since": "2026-01-01T15:00:00+00:00", "until": "2026-01-15T15:00:00+00:00"}]


@pytest.mark.unit
def test_run_list_omits_unset_since_and_until(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeResponse:
        calls.append(params)
        return _FakeResponse(200, [])

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["run", "list"])

    assert result.exit_code == 0, result.output
    assert calls == [{}]


@pytest.mark.unit
def test_run_list_maps_a_422_to_a_click_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, params: dict[str, str], timeout: float) -> _FakeResponse:
        return _FakeResponse(422, {"detail": "until must be after since"})

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["run", "list"])

    assert result.exit_code != 0
    assert "until must be after since" in result.output


# --------------------------------------------------------------------------- #
# `blizzard hub run show`


@pytest.mark.unit
def test_run_show_renders_identity_and_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, _run_delta())

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["run", "show", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "ch_1" in result.output
    assert "nightly/blizzard" in result.output
    assert "outcome: ready" in result.output


@pytest.mark.unit
def test_run_show_renders_the_escalation_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            _run_delta(
                outcome="needs_human",
                escalation={
                    "node_name": "build",
                    "takeover_command": "resume-cmd",
                    "wrapped_takeover_command": "wrapped-cmd",
                },
            ),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["run", "show", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "escalated at: build" in result.output
    assert "resume-cmd" in result.output
    assert "wrapped-cmd" in result.output


@pytest.mark.unit
def test_run_show_renders_added_observed_and_gone_per_set(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(
            200,
            _run_delta(
                sets=[
                    {
                        "finding_set_id": "fins_1",
                        "revisions": {"blizzard": "aaa"},
                        "measurement": "score: 4",
                        "added": [
                            {
                                "finding_id": "fin_1",
                                "class": "stale-docstring",
                                "locus": "a.py:1",
                                "summary": "s",
                                "introduced": None,
                            }
                        ],
                        "observed": ["fin_2"],
                        "gone": [{"finding_id": "fin_3", "note": "not found"}],
                    }
                ]
            ),
        )

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["run", "show", "ch_1"])

    assert result.exit_code == 0, result.output
    assert "fins_1" in result.output
    assert "score: 4" in result.output
    assert "[fin_1] stale-docstring" in result.output
    assert "= fin_2" in result.output
    assert "- fin_3: not found" in result.output


@pytest.mark.unit
def test_run_show_maps_a_404_to_a_click_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    monkeypatch.setattr(httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["run", "show", "ch_ghost"])

    assert result.exit_code != 0
    assert "ch_ghost" in result.output


# --------------------------------------------------------------------------- #
# Against a real, wired app (component tier)


def _default_graph(hub: HubHarness) -> Graph:
    return hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )


def _routine(hub: HubHarness) -> Routine:
    graph = _default_graph(hub)
    return hub.services.routine_authoring.create(
        name="gardening",
        graph_name=graph.name,
        default_scope_slug=ScopeSlug.parse("blizzard"),
        default_model=None,
        default_effort=None,
    )


_HUB_URL = "http://hub.local:8421"


def _relay(hub: HubHarness, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float) -> httpx.Response:
        return hub.client.get(url, params=params)

    monkeypatch.setattr(httpx, "get", fake_get)


@pytest.mark.component
def test_run_list_and_show_work_end_to_end_against_a_real_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    routine = _routine(hub)
    result = hub.services.routine_run.run(
        routine, scope_slug=None, mode=RunMode.FULL, note=None, author=WorkItemAuthor.user("usr_1")
    )
    hub.clock.advance(timedelta(hours=1))
    _relay(hub, monkeypatch)

    list_result = CliRunner().invoke(hub_group, ["run", "list"], env={"BZ_HUB_URL": _HUB_URL})

    assert list_result.exit_code == 0, list_result.output
    assert result.chunk_id in list_result.output

    show_result = CliRunner().invoke(hub_group, ["run", "show", result.chunk_id], env={"BZ_HUB_URL": _HUB_URL})

    assert show_result.exit_code == 0, show_result.output
    assert result.chunk_id in show_result.output
    assert "outcome: ready" in show_result.output
