"""``blizzard hub runner retire|reinstate|revoke-token|list --all`` (unit tier) — pure
clients of the retirement endpoints, driven here with ``httpx`` stubbed."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from click.testing import CliRunner

from blizzard.hub.cli import hub as hub_group

_ENV = {"BZ_HUB_URL": "http://hub.local:8421"}


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


def _stub(monkeypatch: pytest.MonkeyPatch, verb: str, response: _FakeResponse) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def fake(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(httpx, verb, fake)
    return calls


_RETIRED_VIEW = {"runner_id": "runner-a", "retired": True, "retired_at": "2026-09-28T00:00:00Z", "retired_by": "op"}


@pytest.mark.unit
def test_retire_posts_force_and_by_and_names_what_it_released(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(
        monkeypatch, "post", _FakeResponse(200, {"runner": _RETIRED_VIEW, "released_chunk_ids": ["ch_1", "ch_2"]})
    )

    result = CliRunner().invoke(hub_group, ["runner", "retire", "runner-a", "--force", "--by", "op"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["url"] == "http://hub.local:8421/api/runners/runner-a/retire"
    assert calls[0]["json"] == {"by": "op", "force": True}
    assert "ch_1, ch_2" in result.output


@pytest.mark.unit
def test_retire_surfaces_the_holdings_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, "post", _FakeResponse(409, {"detail": "runner runner-a holds 1 chunk(s): ch_1"}))

    result = CliRunner().invoke(hub_group, ["runner", "retire", "runner-a"], env=_ENV)

    assert result.exit_code != 0
    assert "ch_1" in result.output


@pytest.mark.unit
def test_reinstate_posts_by_and_reports_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, "post", _FakeResponse(200, {"runner_id": "runner-a", "retired": False}))

    result = CliRunner().invoke(hub_group, ["runner", "reinstate", "runner-a"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["url"].endswith("/api/runners/runner-a/reinstate")
    assert calls[0]["json"] == {"by": "operator"}
    assert "reinstated" in result.output


@pytest.mark.unit
def test_revoke_token_posts_a_revocation_and_maps_an_unenrolled_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, "post", _FakeResponse(201, {"runner": {"runner_id": "runner-a", "retired": False}}))
    ok = CliRunner().invoke(hub_group, ["runner", "revoke-token", "runner-a"], env=_ENV)
    assert ok.exit_code == 0, ok.output
    assert calls[0]["url"].endswith("/api/runners/runner-a/token-revocations")
    assert "revoked" in ok.output

    _stub(monkeypatch, "post", _FakeResponse(409))
    refused = CliRunner().invoke(hub_group, ["runner", "revoke-token", "runner-a"], env=_ENV)
    assert refused.exit_code != 0
    assert "no enrolled token" in refused.output


@pytest.mark.unit
def test_list_all_asks_for_retired_runners_and_marks_them(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, "get", _FakeResponse(200, {"runners": [{**_RETIRED_VIEW, "workspace_id": "ws-a"}]}))

    result = CliRunner().invoke(hub_group, ["runner", "list", "--all"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["params"] == {"include_retired": "true"}
    assert "[retired 2026-09-28T00:00:00Z by op]" in result.output


@pytest.mark.unit
def test_list_defaults_to_hiding_retired_runners(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, "get", _FakeResponse(200, {"runners": []}))

    result = CliRunner().invoke(hub_group, ["runner", "list"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0].get("params") is None
