"""``blizzard hub runner retire|reinstate|revoke-token|list --all`` (unit tier) — pure
clients of the retirement endpoints, driven here with the client stubbed."""

from __future__ import annotations

from typing import Any

import httpx2

from blizzard.hub.cli import hub as hub_group
from blizzard.wire.runner import RunnerRegistryView
from tests.support.worker_http import Bound, bind_stubs

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
            request = httpx2.Request("GET", "http://hub.test")
            raise httpx2.HTTPStatusError(
                "error", request=request, response=httpx2.Response(self.status_code, request=request)
            )


def _stub(verb: str, response: _FakeResponse) -> tuple[Bound, list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def fake(url: str, **kwargs: Any) -> _FakeResponse:
        calls.append({"url": url, **kwargs})
        return response

    return bind_stubs(**{verb: fake}), calls


def _runner(**fields: Any) -> dict[str, Any]:
    """A ``RunnerRegistryView`` body for runner-a, ``fields`` over its defaults."""
    base: dict[str, Any] = {
        "runner_id": "runner-a",
        "runner_name": "alpha",
        "connection": "online",
        "online": False,
        "added_at": "2026-09-01T00:00:00Z",
        "hub_paused": False,
    }
    return RunnerRegistryView.model_validate({**base, **fields}).model_dump(mode="json")


_RETIRED_VIEW = _runner(retired=True, retired_at="2026-09-28T00:00:00Z", retired_by="op")


def test_retire_posts_force_and_by_and_names_what_it_released() -> None:
    bound, calls = _stub("post", _FakeResponse(200, {"runner": _RETIRED_VIEW, "released_chunk_ids": ["ch_1", "ch_2"]}))

    result = bound.runner.invoke(hub_group, ["runner", "retire", "runner-a", "--force", "--by", "op"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["url"] == "http://hub.local:8421/api/runners/runner-a/retire"
    assert calls[0]["json"] == {"by": "op", "force": True}
    assert "ch_1, ch_2" in result.output


def test_retire_surfaces_the_holdings_refusal() -> None:
    bound, _ = _stub("post", _FakeResponse(409, {"detail": "runner runner-a holds 1 chunk(s): ch_1"}))

    result = bound.runner.invoke(hub_group, ["runner", "retire", "runner-a"], env=_ENV)

    assert result.exit_code != 0
    assert "ch_1" in result.output


def test_reinstate_posts_by_and_reports_it() -> None:
    bound, calls = _stub("post", _FakeResponse(200, _runner()))

    result = bound.runner.invoke(hub_group, ["runner", "reinstate", "runner-a"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["url"].endswith("/api/runners/runner-a/reinstate")
    assert calls[0]["json"] == {"by": "operator"}
    assert "reinstated" in result.output


def test_revoke_token_posts_a_revocation_and_shows_the_hubs_unenrolled_refusal() -> None:
    bound, calls = _stub("post", _FakeResponse(201, {"runner": _runner()}))
    ok = bound.runner.invoke(hub_group, ["runner", "revoke-token", "runner-a"], env=_ENV)
    assert ok.exit_code == 0, ok.output
    assert calls[0]["url"].endswith("/api/runners/runner-a/token-revocations")
    assert "revoked" in ok.output

    bound, _ = _stub("post", _FakeResponse(409, {"detail": "runner runner-a has no enrolled token"}))
    refused = bound.runner.invoke(hub_group, ["runner", "revoke-token", "runner-a"], env=_ENV)
    assert refused.exit_code != 0
    assert "no enrolled token" in refused.output


def test_list_all_asks_for_retired_runners_and_marks_them() -> None:
    bound, calls = _stub("get", _FakeResponse(200, {"runners": [{**_RETIRED_VIEW, "workspace_id": "ws-a"}]}))

    result = bound.runner.invoke(hub_group, ["runner", "list", "--all"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0]["params"] == {"include_retired": "true"}
    assert "[retired 2026-09-28T00:00:00Z by op]" in result.output


def test_list_defaults_to_hiding_retired_runners() -> None:
    bound, calls = _stub("get", _FakeResponse(200, {"runners": []}))

    result = bound.runner.invoke(hub_group, ["runner", "list"], env=_ENV)

    assert result.exit_code == 0, result.output
    assert calls[0].get("params") is None
