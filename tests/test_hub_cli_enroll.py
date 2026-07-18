"""``blizzard hub runner enroll`` (unit tier) — a pure client of the enroll endpoint,
driven here with ``httpx.post`` stubbed (issue #86a).
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
def test_enroll_posts_to_the_enrollments_endpoint_and_prints_the_token(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(201, {"runner_id": "runner-a", "token": "sekrit-token"})

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(
        hub_group, ["runner", "enroll", "runner-a"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/runners/runner-a/enrollments"]
    assert "sekrit-token" in result.output


@pytest.mark.unit
def test_enroll_maps_an_unknown_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        return _FakeResponse(404)

    monkeypatch.setattr(hub_cli.httpx, "post", fake_post)
    result = CliRunner().invoke(hub_group, ["runner", "enroll", "ghost"])

    assert result.exit_code != 0
    assert "ghost" in result.output
