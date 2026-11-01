"""``blizzard hub runner enroll`` (unit tier) — a pure client of the enroll endpoint,
driven here with the client factory stubbed.
"""

from __future__ import annotations

import httpx2
import pytest

from blizzard.hub.cli import hub as hub_group
from tests.worker_http import bind_stubs


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
            request = httpx2.Request("GET", "http://hub.test")
            raise httpx2.HTTPStatusError(
                "error", request=request, response=httpx2.Response(self.status_code, request=request)
            )


@pytest.mark.unit
def test_enroll_posts_to_the_enrollments_endpoint_and_prints_the_token() -> None:
    calls: list[str] = []

    def fake_post(url: str, *, timeout: float) -> _FakeResponse:
        calls.append(url)
        return _FakeResponse(201, {"runner_id": "runner-a", "token": "sekrit-token"})

    def fake_get(url: str, *, timeout: float, **_: object) -> _FakeResponse:
        return _FakeResponse(200, {"runner_id": "runner-a", "runner_name": "runner-a"})

    bound = bind_stubs(post=fake_post, get=fake_get)
    result = bound.runner.invoke(
        hub_group, ["runner", "enroll", "runner-a"], env={"BZ_HUB_URL": "http://hub.local:8421"}
    )

    assert result.exit_code == 0, result.output
    assert calls == ["http://hub.local:8421/api/runners/runner-a/enrollments"]
    assert "sekrit-token" in result.output


@pytest.mark.unit
def test_enroll_maps_an_unknown_runner() -> None:
    def fake_get(url: str, *, timeout: float, **_: object) -> _FakeResponse:
        return _FakeResponse(404)

    bound = bind_stubs(get=fake_get)
    result = bound.runner.invoke(hub_group, ["runner", "enroll", "ghost"])

    assert result.exit_code != 0
    assert "ghost" in result.output
