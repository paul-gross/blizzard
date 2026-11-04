"""Giving a gate-parked chunk back: whether a runner offers, the offer letting go only on the hub's acceptance, the
HTTP client's reading of each answer, and the three call sites — a held chunk's next node, an adopted claim, and a
usage-limited park."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from datetime import UTC, datetime

import httpx2
import pytest

from blizzard.runner.hub.client import HubClientError
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from blizzard.runner.throttle.pause import USAGE_LIMIT_PARK_CAUSE, PausePark
from blizzard.runner.throttle.release import (
    Release,
    ReleaseReason,
    ReleaseRefusals,
    releases_between_steps,
    releases_on_decline,
    releases_usage_limited,
)
from blizzard.runner.throttle.settings import HoldOrRelease, ThrottleSettings
from tests.runner_fakes import (
    FakeHub,
    ReleaseCall,
)

_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_ON_DECLINE = ThrottleSettings(on_decline=HoldOrRelease.RELEASE)
_ON_USAGE_LIMIT = ThrottleSettings(on_usage_limit=HoldOrRelease.RELEASE)
_PARK = PausePark(
    lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW, interrupted_elicitation_id=None, cause=USAGE_LIMIT_PARK_CAUSE
)


# --- whether a runner offers, by value -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "declined", "offers"),
    [
        (None, True, False),
        (ThrottleSettings(), True, False),
        (_ON_USAGE_LIMIT, True, False),
        (_ON_DECLINE, False, False),
        (_ON_DECLINE, True, True),
    ],
)
def test_a_step_is_offered_back_only_when_declined_on_a_runner_set_to_release(
    settings: ThrottleSettings | None, declined: bool, offers: bool
) -> None:
    assert releases_between_steps(settings, declined=declined) is offers
    assert releases_on_decline(settings) is (settings is _ON_DECLINE)


@pytest.mark.parametrize(
    ("settings", "park", "operator_holds", "declined", "offers"),
    [
        (_ON_USAGE_LIMIT, _PARK, False, True, True),
        (_ON_DECLINE, _PARK, False, True, False),
        (None, _PARK, False, True, False),
        (_ON_USAGE_LIMIT, dataclasses.replace(_PARK, cause=None), False, True, False),
        (_ON_USAGE_LIMIT, _PARK, True, True, False),
        (_ON_USAGE_LIMIT, _PARK, False, False, False),
    ],
)
def test_a_park_is_offered_back_only_when_a_usage_limit_wrote_it_and_its_floor_still_declines(
    settings: ThrottleSettings | None, park: PausePark, operator_holds: bool, declined: bool, offers: bool
) -> None:
    assert releases_usage_limited(settings, park, operator_holds=operator_holds, declined=declined) is offers


# --- the offer -------------------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _ReleaseCtx:
    hub: FakeHub | HttpHubClient
    release_refusals: ReleaseRefusals = dataclasses.field(default_factory=ReleaseRefusals)


def _offer(
    hub: FakeHub | HttpHubClient, let_go: Callable[[], None], *, ctx: _ReleaseCtx | None = None, epoch: int = 3
) -> bool:
    return Release(ctx or _ReleaseCtx(hub)).offer(
        "ch_1", epoch=epoch, route_token="rtok", reason=ReleaseReason.THROTTLED, then=let_go
    )


def test_an_accepted_offer_lets_go_once_and_sends_the_reason() -> None:
    hub, let_go = FakeHub(), _Counter()

    assert _offer(hub, let_go) is True

    assert let_go.calls == 1
    assert hub.releases == [ReleaseCall("ch_1", 3, "rtok", "throttled")]


def test_a_refused_offer_holds() -> None:
    hub, let_go = FakeHub(), _Counter()
    hub.release_refused.add("ch_1")

    assert _offer(hub, let_go) is False
    assert let_go.calls == 0


def test_a_refused_offer_is_not_repeated_at_the_same_epoch_but_is_at_the_next() -> None:
    hub, let_go = FakeHub(), _Counter()
    hub.release_refused.add("ch_1")
    ctx = _ReleaseCtx(hub)

    assert _offer(hub, let_go, ctx=ctx) is False
    assert _offer(hub, let_go, ctx=ctx) is False
    assert len(hub.releases) == 1

    hub.release_refused.clear()
    assert _offer(hub, let_go, ctx=ctx, epoch=4) is True
    assert len(hub.releases) == 2


def test_an_unreachable_hub_holds() -> None:
    hub, let_go = FakeHub(), _Counter()
    hub.down = True

    assert _offer(hub, let_go) is False
    assert let_go.calls == 0


class _Counter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


# --- the HTTP client -------------------------------------------------------------------------------------------------


def _http(status: int, seen: list[httpx2.Request] | None = None) -> HttpHubClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        body = {"chunk_id": "ch_1", "released_id": 7} if status == 200 else {"detail": "no"}
        return httpx2.Response(status, json=body)

    return HttpHubClient(httpx2.Client(base_url="http://hub.test", transport=httpx2.MockTransport(handler)))


def test_the_client_posts_the_release_and_reads_200_as_accepted() -> None:
    seen: list[httpx2.Request] = []

    assert _http(200, seen).release("ch_1", epoch=3, route_token="rtok", reason="throttled") is True

    (request,) = seen
    assert (request.method, request.url.path) == ("POST", "/api/fleet/chunks/ch_1/release")
    assert json.loads(request.content) == {"epoch": 3, "route_token": "rtok", "reason": "throttled"}


@pytest.mark.parametrize("status", [409, 403, 404])
def test_the_client_reads_each_refusal_as_held(status: int) -> None:
    assert _http(status).release("ch_1", epoch=3, route_token=None, reason="throttled") is False


def test_the_client_raises_on_a_hub_failure_and_a_transport_error() -> None:
    with pytest.raises(HubClientError):
        _http(500).release("ch_1", epoch=3, route_token=None, reason="throttled")

    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    client = HttpHubClient(httpx2.Client(base_url="http://hub.test", transport=httpx2.MockTransport(unreachable)))
    with pytest.raises(HubClientError):
        client.release("ch_1", epoch=3, route_token=None, reason="throttled")
