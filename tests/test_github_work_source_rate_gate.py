"""The GitHub work source's forge calls pass its forge key's shared rate budget at ``sweeping``."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeId
from blizzard.hub.domain.config.secrets import SecretValue
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, WorkSourceFields, WorkSourceId
from blizzard.hub.forge.budget import BudgetKey, ForgeBudgetLow, ForgeBudgets, ForgePriority
from blizzard.hub.work_sources.annotator import WorkAnnotateError, WorkStatusMarker
from blizzard.hub.work_sources.closer import WorkCloseError, WorkItemGoneError
from blizzard.hub.work_sources.internal.factory import WorkSourceEntry
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.source import WorkSourceError

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
_RESET = _NOW + timedelta(minutes=10)
_FORGE = ForgeId("frg_example")
_KEY = BudgetKey(_FORGE, "gh")
_POINTER = WorkRef(source="widget", ref="12")


def _issue(request: httpx2.Request, headers: dict[str, str] | None = None) -> httpx2.Response:
    if request.url.path.endswith("/comments"):
        return httpx2.Response(200, json=[], headers=headers)
    return httpx2.Response(200, json={"title": "t", "body": "b"}, headers=headers)


def _rate(remaining: int, *, limit: int = 100) -> dict[str, str]:
    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(int(_RESET.timestamp())),
    }


class _World:
    def __init__(self, answer: Callable[[httpx2.Request], httpx2.Response] | None = None) -> None:
        self.clock = FixedClock(_NOW)
        self.budgets = ForgeBudgets(clock=self.clock)
        self.requests: list[httpx2.Request] = []
        self._answer = answer or _issue

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self._answer(request)

    def source(self, secret: str | None = "gh") -> GitHubWorkSource:
        gate = self.budgets.sweeping(_FORGE, secret)
        client = httpx2.Client(base_url="http://forge", transport=httpx2.MockTransport(self._handle))
        client.event_hooks["response"].append(lambda response: gate.learn(response.headers))
        return GitHubWorkSource(client, source_id="widget", slug="widget", repo="acme/widget", web_base="x", gate=gate)


def _calls(source: GitHubWorkSource) -> list[tuple[Callable[[], object], type[Exception]]]:
    return [
        (lambda: source.fetch(_POINTER), WorkSourceError),
        (lambda: source.set_status(_POINTER, WorkStatusMarker.INGESTED), WorkAnnotateError),
        (lambda: source.clear_status(_POINTER), WorkAnnotateError),
        (source.marked_refs, WorkAnnotateError),
        (lambda: source.close(_POINTER, trace=None), WorkCloseError),
    ]


def test_every_call_is_turned_away_below_sweeping_s_threshold_with_no_request() -> None:
    world = _World()
    world.budgets.learn(_KEY, _rate(40))
    for call, error in _calls(world.source()):
        with pytest.raises(error) as raised:
            call()
        assert getattr(raised.value, "retry_after", None) == _RESET
    assert world.requests == []


def test_a_call_goes_out_above_sweeping_s_threshold_and_teaches_the_budget() -> None:
    world = _World(lambda request: _issue(request, _rate(41)))
    world.budgets.learn(_KEY, _rate(41))
    world.source().fetch(_POINTER)
    assert len(world.requests) == 2
    assert [reading.remaining for reading in world.budgets.readings(_KEY)] == [41]


def test_a_forge_rate_refusal_sets_retry_after_and_spends_the_shared_budget() -> None:
    world = _World(lambda _request: httpx2.Response(403, json={"message": "API rate limit exceeded"}, headers=_rate(0)))
    for call, error in _calls(world.source()):
        with pytest.raises(error) as raised:
            call()
        assert getattr(raised.value, "retry_after", None) == _RESET
    with pytest.raises(ForgeBudgetLow):
        world.budgets.admit(_KEY, ForgePriority.LANDING)


def _unreachable(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("unreachable", request=request)


@pytest.mark.parametrize(
    "answer",
    [
        lambda _request: httpx2.Response(500, json={"message": "boom"}),
        lambda _request: httpx2.Response(403, json={"message": "Resource not accessible"}, headers=_rate(50)),
        _unreachable,
    ],
    ids=["server-error", "forbidden-with-budget", "unreachable"],
)
def test_every_other_failure_leaves_retry_after_unset(answer: Callable[[httpx2.Request], httpx2.Response]) -> None:
    world = _World(answer)
    for call, error in _calls(world.source()):
        with pytest.raises(error) as raised:
            call()
        assert getattr(raised.value, "retry_after", "missing") is None
    world.budgets.admit(_KEY, ForgePriority.LANDING)


def test_a_gone_item_leaves_retry_after_unset() -> None:
    world = _World(lambda _request: httpx2.Response(404))
    with pytest.raises(WorkItemGoneError) as raised:
        world.source().close(_POINTER, trace=None)
    assert raised.value.retry_after is None


def test_a_source_with_no_secret_spends_the_forge_s_anonymous_budget() -> None:
    world = _World()
    world.budgets.learn(BudgetKey(_FORGE, None), _rate(10))
    with pytest.raises(WorkSourceError):
        world.source(secret=None).fetch(_POINTER)
    world.source(secret="gh").fetch(_POINTER)
    assert len(world.requests) == 2


def _record(secret: str | None) -> ConfiguredWorkSource:
    return ConfiguredWorkSource(
        work_source_id=WorkSourceId("wsrc_widget"),
        slug="widget",
        name="widget",
        fields=WorkSourceFields(
            provider="github", locator="acme/widget", forge_id=_FORGE, annotate=True, secret=secret
        ),
        revision=1,
        created_at=_NOW,
        created_by="op",
    )


_CONFIGURED_FORGE = ConfiguredForge(
    forge_id=_FORGE,
    slug="example",
    name="example",
    kind="github",
    fields=ForgeFields("http://forge", None),
    revision=1,
    created_at=_NOW,
    created_by="op",
)


@pytest.mark.parametrize("secret", ["gh", None])
def test_the_factory_binds_each_source_to_its_forge_key_s_budget(secret: str | None) -> None:
    budgets = ForgeBudgets(clock=FixedClock(_NOW))
    entry = WorkSourceEntry.of(_record(secret), _CONFIGURED_FORGE, SecretValue("token"), budgets=budgets)
    assert entry.gate == budgets.sweeping(_FORGE, secret)


def test_the_factory_s_client_teaches_the_budget_from_each_answer() -> None:
    budgets = ForgeBudgets(clock=FixedClock(_NOW))
    client = WorkSourceEntry.of(_record("gh"), _CONFIGURED_FORGE, SecretValue("token"), budgets=budgets).client
    for hook in client.event_hooks["response"]:
        hook(httpx2.Response(200, headers=_rate(7)))
    assert [reading.remaining for reading in budgets.readings(_KEY)] == [7]
