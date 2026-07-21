"""The ``--json`` output shape on the noun-group operator verbs (issue #104): a read
verb prints the raw response body, and a write verb echoes its typed response the same
way. The noun-group surface is the only operator surface — the pre-#104 flat aliases and
the ``--url`` flag alias were removed in issue #105.
"""

from __future__ import annotations

import json

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


pytestmark = pytest.mark.unit


def test_json_on_a_read_verb_prints_the_raw_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = [{"chunk_id": "ch_1", "status": "ready", "current_node_id": None, "model": "sonnet", "cost": {}}]

    def fake_get(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(200, payload)

    monkeypatch.setattr(hub_cli.httpx, "get", fake_get)
    result = CliRunner().invoke(hub_group, ["chunk", "list", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload


def test_json_on_a_write_verb_prints_the_raw_response_body(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"chunk_id": "ch_42", "status": "ready", "graph_id": "gr_1", "model": "sonnet"}

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(202, payload)

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["chunk", "promote", "ch_42", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == payload
