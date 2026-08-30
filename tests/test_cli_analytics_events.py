"""``blizzard hub analytics events`` (blizzard#257 Phase 2) — a pure client of
``GET /api/analytics/events``/``/events/ndjson`` driven with ``httpx`` stubbed (unit
tier): the three output modes (D3), the incompatible-flag guards, D6's local→UTC
``--since``/``--until`` conversion, and the bare 401/403 messages."""

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

pytestmark = pytest.mark.unit

_EVENT = {
    "id": 1,
    "kind": "file_read",
    "subject": "src/a.py",
    "tool": "Read",
    "payload": {},
    "chunk_id": "ch_1",
    "node_id": "nd_build",
    "epoch": 1,
    "spawn_generation": 1,
    "graph_id": "gr_1",
    "depth": 0,
    "agent_type": None,
    "occurred_at": "2026-08-12T09:00:00+00:00",
}


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


class _FakeStreamResponse(_FakeResponse):
    def __init__(self, status_code: int, lines: list[str] | None = None, payload: object | None = None) -> None:
        super().__init__(status_code, payload)
        self._lines = lines or []

    def read(self) -> None:
        pass

    def iter_lines(self) -> Iterator[str]:
        yield from self._lines


def _stream_returning(resp: _FakeStreamResponse):
    @contextlib.contextmanager
    def fake_stream(method: str, url: str, **kwargs: object) -> Iterator[_FakeStreamResponse]:
        yield resp

    return fake_stream


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


# --- D3: the three output modes -----------------------------------------------------


def test_default_output_is_a_human_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, {"events": [_EVENT], "next_cursor": None}))

    result = CliRunner().invoke(hub_group, ["analytics", "events"])

    assert result.exit_code == 0, result.output
    assert "src/a.py" in result.output
    assert "tool=Read" in result.output
    assert "{" not in result.output  # not raw JSON


def test_json_prints_the_raw_envelope_including_next_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    body = {"events": [_EVENT], "next_cursor": "cur_2"}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(200, body))

    result = CliRunner().invoke(hub_group, ["analytics", "events", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == body


def test_ndjson_streams_every_line_to_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _FakeStreamResponse(200, lines=['{"id": 1}', '{"id": 2}'])
    monkeypatch.setattr(httpx, "stream", _stream_returning(resp))
    monkeypatch.setattr(httpx, "get", lambda *a, **k: pytest.fail("--ndjson must not hit the paged route"))

    result = CliRunner().invoke(hub_group, ["analytics", "events", "--ndjson"])

    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == ['{"id": 1}', '{"id": 2}']


# --- D3: the incompatible-flag guards ------------------------------------------------


def test_ndjson_rejects_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: pytest.fail("must not reach the hub"))

    result = CliRunner().invoke(hub_group, ["analytics", "events", "--ndjson", "--json"])

    assert result.exit_code != 0
    assert "--ndjson is incompatible with --json" in result.output


def test_ndjson_rejects_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: pytest.fail("must not reach the hub"))

    result = CliRunner().invoke(hub_group, ["analytics", "events", "--ndjson", "--cursor", "cur_1"])

    assert result.exit_code != 0
    assert "--ndjson is incompatible with --cursor" in result.output


def test_ndjson_rejects_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: pytest.fail("must not reach the hub"))

    result = CliRunner().invoke(hub_group, ["analytics", "events", "--ndjson", "--limit", "10"])

    assert result.exit_code != 0
    assert "--ndjson is incompatible with --limit" in result.output


# --- filter round-trip (params reach the client call) ---------------------------------


def test_every_filter_flag_becomes_a_query_param(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object) -> _FakeResponse:
        calls.append(params or {})
        return _FakeResponse(200, {"events": [], "next_cursor": None})

    monkeypatch.setattr(httpx, "get", fake_get)

    result = CliRunner().invoke(
        hub_group,
        [
            "analytics",
            "events",
            "--graph",
            "gr_1",
            "--source",
            "default",
            "--extractor-version",
            "v2",
            "--kind",
            "file_read",
            "--tool",
            "Read",
            "--subject-prefix",
            "src/",
            "--node",
            "nd_build",
            "--cursor",
            "cur_1",
            "--limit",
            "10",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[0] == {
        "graph_id": "gr_1",
        "source": "default",
        "extractor_version": "v2",
        "kind": "file_read",
        "tool": "Read",
        "subject_prefix": "src/",
        "node_id": "nd_build",
        "cursor": "cur_1",
        "limit": "10",
    }


def test_the_default_limit_is_200_when_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object) -> _FakeResponse:
        calls.append(params or {})
        return _FakeResponse(200, {"events": [], "next_cursor": None})

    monkeypatch.setattr(httpx, "get", fake_get)

    result = CliRunner().invoke(hub_group, ["analytics", "events"])

    assert result.exit_code == 0, result.output
    assert calls[0]["limit"] == "200"


# --- D6: --since/--until cross the boundary UTC-aware --------------------------------


def test_since_and_until_convert_the_operators_local_time_to_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []

    def fake_get(url: str, *, params: dict[str, str] | None = None, timeout: float, **_: object) -> _FakeResponse:
        calls.append(params or {})
        return _FakeResponse(200, {"events": [], "next_cursor": None})

    monkeypatch.setattr(httpx, "get", fake_get)

    with _local_timezone("America/New_York"):  # UTC-5 in January, no DST
        result = CliRunner().invoke(
            hub_group,
            ["analytics", "events", "--since", "2026-01-01T10:00:00", "--until", "2026-01-01T12:00:00"],
        )

    assert result.exit_code == 0, result.output
    assert calls[0]["since"] == "2026-01-01T15:00:00+00:00"
    assert calls[0]["until"] == "2026-01-01T17:00:00+00:00"


# --- the bare 401/403 messages --------------------------------------------------------


def test_a_bare_401_gets_the_login_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(401))

    result = CliRunner().invoke(hub_group, ["analytics", "events"])

    assert result.exit_code != 0
    assert "blizzard hub login" in result.output


def test_a_bare_403_surfaces_the_servers_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: _FakeResponse(403, {"detail": "missing permission 'transcript:read'"})
    )

    result = CliRunner().invoke(hub_group, ["analytics", "events"])

    assert result.exit_code != 0
    assert "missing permission 'transcript:read'" in result.output
