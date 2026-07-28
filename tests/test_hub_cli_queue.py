"""``blizzard hub queue move`` (unit tier) — a client of the single-chunk fractional
``POST /api/queue/position``, driven here with ``httpx`` stubbed (issue #137).

Re-points what used to compose a whole-order ``PUT /api/queue`` client-side (issue
#105) onto the new single-chunk write; this file exists to prove the CLI's front/
middle/end UX is unchanged while the wire call underneath it is.
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


def _queue_response(chunk_ids: list[str]) -> _FakeResponse:
    entries = [{"chunk_id": cid, "graph_id": "gr_1", "position": i} for i, cid in enumerate(chunk_ids)]
    return _FakeResponse(200, {"entries": entries})


def _stub(monkeypatch: pytest.MonkeyPatch, peek_order: list[str]) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        calls.append((url, None))
        return _queue_response(peek_order)

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        calls.append((url, json))
        return _queue_response(peek_order)

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    return calls


@pytest.mark.unit
def test_queue_move_to_front_sends_null_after_chunk_id(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, ["ch_a", "ch_b", "ch_c"])

    result = CliRunner().invoke(
        hub_group,
        ["queue", "move", "ch_c", "0"],
        env={"BZ_HUB_URL": "http://hub.local:8421"},
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        ("http://hub.local:8421/api/queue", None),
        ("http://hub.local:8421/api/queue/position", {"chunk_id": "ch_c", "after_chunk_id": None}),
    ]


@pytest.mark.unit
def test_queue_move_to_the_middle_sends_the_preceding_chunk_as_after_chunk_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(monkeypatch, ["ch_a", "ch_b", "ch_c"])

    result = CliRunner().invoke(hub_group, ["queue", "move", "ch_c", "1"])

    assert result.exit_code == 0, result.output
    assert calls[-1] == ("http://127.0.0.1:8421/api/queue/position", {"chunk_id": "ch_c", "after_chunk_id": "ch_a"})


@pytest.mark.unit
def test_queue_move_past_the_end_clamps_and_anchors_after_the_last_remaining_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(monkeypatch, ["ch_a", "ch_b", "ch_c"])

    result = CliRunner().invoke(hub_group, ["queue", "move", "ch_a", "99"])

    assert result.exit_code == 0, result.output
    assert calls[-1] == ("http://127.0.0.1:8421/api/queue/position", {"chunk_id": "ch_a", "after_chunk_id": "ch_c"})


@pytest.mark.unit
def test_queue_move_reports_409_when_chunk_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _queue_response(["ch_a", "ch_b"])

    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "chunk ch_a is not a ready chunk"})

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)

    result = CliRunner().invoke(hub_group, ["queue", "move", "ch_a", "0"])

    assert result.exit_code != 0
    assert "not a ready chunk" in result.output
