"""``blizzard hub events`` — a pure client of ``GET /api/events`` driven with ``httpx``
stubbed (unit tier): the flag-to-query mapping, the human listing, and ``--json`` passthrough."""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group
from blizzard.hub.cli.events import FeedListing

pytestmark = pytest.mark.unit

_EVENT = {
    "id": "ev_1",
    "recorded_at": "2026-08-12T09:00:00Z",
    "severity": "warning",
    "kind": "attempt-failed",
    "runner_id": "r1",
    "chunk_id": "ch_a",
    "lease_id": "l1",
    "node_name": "build",
    "message": "retried",
    "detail": {"via": "advance"},
}


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        pass


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


def _capture(monkeypatch: pytest.MonkeyPatch, body: object) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append({"url": url, **kwargs})
        return _FakeResponse(200, body)

    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def test_every_flag_maps_onto_its_query_parameter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture(monkeypatch, {"events": []})

    with _local_timezone("America/New_York"):
        result = CliRunner().invoke(
            hub_group,
            [
                "events",
                "--severity",
                "warning",
                "--runner",
                "r1",
                "--chunk",
                "ch_a",
                "--since",
                "2026-08-12T05:00:00",
                "--limit",
                "7",
            ],
        )

    assert result.exit_code == 0, result.output
    assert str(calls[0]["url"]).endswith("/api/events")
    params = calls[0]["params"]
    assert isinstance(params, dict)
    assert params["severity"] == "warning"
    assert params["runner_id"] == "r1"
    assert params["chunk_id"] == "ch_a"
    assert params["since"].startswith("2026-08-12T09:00:00")
    assert params["limit"] == "7"


def test_no_flags_sends_no_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _capture(monkeypatch, {"events": []})

    result = CliRunner().invoke(hub_group, ["events"])

    assert result.exit_code == 0, result.output
    assert not calls[0].get("params")
    assert "no events" in result.output


@pytest.mark.parametrize("args", [["--limit", "0"], ["--limit", "201"], ["--severity", "loud"]])
def test_out_of_range_flags_are_refused_before_the_wire(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("must not reach the hub"))

    result = CliRunner().invoke(hub_group, ["events", *args])

    assert result.exit_code != 0


def test_default_output_is_one_human_line_per_event(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture(monkeypatch, {"events": [_EVENT]})

    result = CliRunner().invoke(hub_group, ["events"])

    assert result.exit_code == 0, result.output
    line = result.output.strip()
    assert "attempt-failed" in line
    assert "runner=r1" in line
    assert "chunk=ch_a" in line
    assert "node=build" in line
    assert "retried" in line
    assert "{" not in result.output


def test_json_prints_the_raw_response_including_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"events": [_EVENT]}
    _capture(monkeypatch, body)

    result = CliRunner().invoke(hub_group, ["events", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == body


@pytest.mark.parametrize(
    ("row", "line"),
    [
        (
            {"runner_id": None, "chunk_id": "ch_a", "node_name": "build"},
            "2026-08-12T09:00:00Z  warning  attempt-failed chunk=ch_a node=build  retried",
        ),
        (
            {"runner_id": "r1", "chunk_id": None, "node_name": "build"},
            "2026-08-12T09:00:00Z  warning  attempt-failed runner=r1 node=build  retried",
        ),
        (
            {"runner_id": "r1", "chunk_id": "ch_a", "node_name": None},
            "2026-08-12T09:00:00Z  warning  attempt-failed runner=r1 chunk=ch_a  retried",
        ),
        (
            {"runner_id": "", "chunk_id": "", "node_name": ""},
            "2026-08-12T09:00:00Z  warning  attempt-failed  retried",
        ),
        (
            {},
            "2026-08-12T09:00:00Z  warning  attempt-failed  retried",
        ),
    ],
)
def test_an_event_lacking_runner_chunk_or_node_omits_just_that_token(row: dict[str, object], line: str) -> None:
    sparse = {k: v for k, v in _EVENT.items() if k not in ("runner_id", "chunk_id", "node_name")} | row

    assert FeedListing([sparse]).line(sparse) == line
