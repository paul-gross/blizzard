"""``blizzard hub finding`` exit verbs against a refusing server (unit tier, ``httpx``
stubbed): a 409 from a state the verb is illegal from surfaces the server's detail."""

from __future__ import annotations

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group

pytestmark = pytest.mark.unit


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


def test_a_409_surfaces_the_servers_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409, {"detail": "finding 'fin_1' is already 'resolved'; reopen it before 'wont-fix'"})

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["finding", "wont-fix", "fin_1", "--note", "n"])

    assert result.exit_code != 0
    assert "reopen it before" in result.output


def test_a_409_without_a_detail_falls_back_to_the_verb(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, json: object, timeout: float) -> _FakeResponse:
        return _FakeResponse(409)

    monkeypatch.setattr(httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["finding", "reopen", "fin_1", "--note", "n"])

    assert result.exit_code != 0
    assert "'reopen' is not legal" in result.output
