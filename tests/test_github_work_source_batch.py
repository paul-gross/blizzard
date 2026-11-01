"""The GitHub work source's batch read — one GraphQL request per call, admitted through the gate."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.forge.budget import BudgetKey, ForgeBudgetLow, ForgeBudgets, ForgePriority
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.source import WorkItem, WorkSourceError

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
_RESET = _NOW + timedelta(minutes=10)
_FORGE = ForgeId("frg_example")
_KEY = BudgetKey(_FORGE, "gh")


def _ref(number: int) -> WorkRef:
    return WorkRef(source="widget", ref=str(number))


def _rate(remaining: int) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": "100",
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(int(_RESET.timestamp())),
        "X-RateLimit-Resource": "graphql",
    }


def _answer(request: httpx2.Request) -> httpx2.Response:
    """Issue 1 and 2 exist (2 with one comment); anything else is a null alias with a NOT_FOUND path."""
    query = json.loads(request.content)["query"]
    repository: dict[str, object] = {}
    errors: list[dict[str, object]] = []
    for number in (1, 2, 3):
        alias = f"i{number}"
        if f"{alias}:" not in query:
            continue
        if number == 3:
            repository[alias] = None
            errors.append({"type": "NOT_FOUND", "message": "Could not resolve", "path": ["repository", alias]})
            continue
        node: dict[str, object] = {"title": f"title {number}", "body": f"body {number}"}
        if "comments" in query:
            node["comments"] = {"nodes": [{"body": "c"}] if number == 2 else []}
        repository[alias] = node
    return httpx2.Response(200, json={"data": {"repository": repository}, "errors": errors}, headers=_rate(90))


class _World:
    def __init__(self, answer: Callable[[httpx2.Request], httpx2.Response] = _answer) -> None:
        self.budgets = ForgeBudgets(clock=FixedClock(_NOW))
        self.requests: list[httpx2.Request] = []
        self._answer = answer

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self._answer(request)

    def source(self, base_url: str = "http://forge") -> GitHubWorkSource:
        gate = self.budgets.sweeping(_FORGE, "gh")
        client = httpx2.Client(base_url=base_url, transport=httpx2.MockTransport(self._handle))
        client.event_hooks["response"].append(lambda response: gate.learn(response.headers))
        return GitHubWorkSource(client, source_id="widget", slug="widget", repo="acme/widget", web_base="x", gate=gate)


def test_a_batch_is_one_request_and_a_missing_alias_errors_only_its_ref() -> None:
    world = _World()
    result = world.source().fetch_many([_ref(1), _ref(2), _ref(3)], include_comments=True)
    assert len(world.requests) == 1
    assert result[_ref(1)] == WorkItem(body="body 1", title="title 1", comments=[])
    assert result[_ref(2)] == WorkItem(body="body 2", title="title 2", comments=["c"])
    error = result[_ref(3)]
    assert isinstance(error, WorkSourceError)
    assert "Could not resolve" in str(error)
    assert error.retry_after is None


def test_comments_are_selected_only_when_asked_for() -> None:
    world = _World()
    world.source().fetch_many([_ref(1)], include_comments=False)
    assert "comments" not in json.loads(world.requests[0].content)["query"]


@pytest.mark.parametrize(
    ("base_url", "path"),
    [
        ("https://api.github.com", "/graphql"),
        ("http://forge", "/graphql"),
        ("https://ghe.example/api/v3", "/api/graphql"),
        ("https://ghe.example/api/v3/", "/api/graphql"),
    ],
)
def test_the_graphql_url_is_derived_from_the_rest_base(base_url: str, path: str) -> None:
    world = _World()
    world.source(base_url).fetch_many([_ref(1)], include_comments=False)
    assert world.requests[0].url.path == path
    assert json.loads(world.requests[0].content)["variables"] == {"owner": "acme", "name": "widget"}


def test_a_turn_away_sends_nothing_and_names_the_lift_on_every_ref() -> None:
    world = _World()
    world.budgets.learn(_KEY, {**_rate(40), "X-RateLimit-Resource": "core"})
    result = world.source().fetch_many([_ref(1), _ref(2)], include_comments=False)
    assert world.requests == []
    for outcome in result.values():
        assert isinstance(outcome, WorkSourceError)
        assert outcome.retry_after == _RESET
        assert str(_RESET) in str(outcome)


@pytest.mark.parametrize(
    "refusal",
    [
        httpx2.Response(403, json={"message": "API rate limit exceeded"}, headers=_rate(0)),
        httpx2.Response(
            200,
            json={"data": None, "errors": [{"type": "RATE_LIMITED", "message": "limit"}]},
            headers={k: v for k, v in _rate(0).items() if k != "X-RateLimit-Remaining"},
        ),
    ],
    ids=["http-403", "graphql-200-rate-limited"],
)
def test_both_rate_refusal_forms_put_retry_after_on_every_ref_and_spend_the_budget(refusal: httpx2.Response) -> None:
    world = _World(lambda _request: refusal)
    result = world.source().fetch_many([_ref(1), _ref(2)], include_comments=False)
    assert len(world.requests) == 1
    for outcome in result.values():
        assert isinstance(outcome, WorkSourceError)
        assert outcome.retry_after == _RESET
    with pytest.raises(ForgeBudgetLow):
        world.budgets.admit(_KEY, ForgePriority.LANDING)


def test_any_other_failure_errors_every_ref_without_retry_after() -> None:
    world = _World(lambda _request: httpx2.Response(500, json={"message": "boom"}))
    result = world.source().fetch_many([_ref(1), _ref(2)], include_comments=False)
    for outcome in result.values():
        assert isinstance(outcome, WorkSourceError)
        assert outcome.retry_after is None
