"""``blizzard hub decision resolve`` and ``blizzard hub question answer`` against a refusing
server (unit tier, ``httpx`` stubbed): a 409 carrying the winner reads as a lost first-write-wins
race, and any other 409 — a gate or question already closed — surfaces the server's detail."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


def _invoke(monkeypatch: pytest.MonkeyPatch, payload: object, args: list[str]) -> Any:
    def fake_post(url: str, **_: object) -> _FakeResponse:
        return _FakeResponse(409, payload)

    monkeypatch.setattr(httpx, "post", fake_post)
    return CliRunner().invoke(hub_group, args)


def test_a_lost_resolution_race_names_the_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"decision_id": "dec_1", "already_resolved_by": "alice", "choice": "yes"}
    result = _invoke(monkeypatch, payload, ["decision", "resolve", "dec_1", "no"])
    assert result.exit_code != 0
    assert "already resolved by alice" in result.output


def test_a_closed_decision_surfaces_the_servers_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"detail": "decision dec_1 closed: chunk ch_1 is stopped"}
    result = _invoke(monkeypatch, payload, ["decision", "resolve", "dec_1", "no"])
    assert result.exit_code != 0
    assert "decision dec_1 closed: chunk ch_1 is stopped" in result.output
    assert "already resolved by" not in result.output


def test_a_lost_answer_race_names_the_winner(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "won": False,
        "question_id": "qn_1",
        "answer": "use main",
        "answered_by": "alice",
        "answered_at": "2026-01-01T00:00:00Z",
    }
    result = _invoke(monkeypatch, payload, ["question", "answer", "qn_1", "use dev"])
    assert result.exit_code != 0
    assert "already answered by alice: 'use main'" in result.output


def test_a_closed_question_surfaces_the_servers_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"detail": "question qn_1 closed: chunk ch_1 is done"}
    result = _invoke(monkeypatch, payload, ["question", "answer", "qn_1", "use dev"])
    assert result.exit_code != 0
    assert "question qn_1 closed: chunk ch_1 is done" in result.output
    assert "already answered by" not in result.output
