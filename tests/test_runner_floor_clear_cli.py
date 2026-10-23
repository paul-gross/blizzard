"""``blizzard runner clear-floor``: exactly one of ``--subscription`` and ``--runner``, each reaching its own route
with who cleared it, and the refusals mapped to a message naming what to do instead."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

import httpx2
import pytest
from click.testing import CliRunner

from blizzard.foundation.clock import FixedClock
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.cli.daemon import RunnerDaemon
from blizzard.runner.throttle.floor import RunnerFloorClearRefused
from blizzard.runner.throttle.floor_clear import RunnerFloorClearService, SubscriptionFloorClearService
from blizzard.runner.throttle.usage_limits import FloorClear

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)
_RUNNERS = [{"runner_id": "rn_a", "name": "alpha", "state": "serving", "halted_reasons": []}]
_CLEARED = {"slug": None, "cleared_at": "2026-10-22T12:00:00+00:00", "by": "operator"}

Calls = list[tuple[str, dict[str, object]]]


def _host(monkeypatch: pytest.MonkeyPatch, answer: Callable[[httpx2.Request], httpx2.Response]) -> Calls:
    """Every verb reaches a mock host of runner ``alpha``, which answers its floor clears with ``answer``."""
    calls: Calls = []

    def route(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/api/host/runners":
            return httpx2.Response(200, json={"items": _RUNNERS})
        calls.append((request.url.path, json.loads(request.content or b"{}")))
        return answer(request)

    def reach(
        cls: type[RunnerDaemon], verb: str, _dir: str, _url: str | None, _door: object, runner: str | None = None
    ) -> RunnerDaemon:
        return RunnerDaemon(
            verb, httpx2.Client(transport=httpx2.MockTransport(route), base_url="http://host"), "host", runner
        )

    monkeypatch.setattr(RunnerDaemon, "reach", classmethod(reach))
    return calls


@pytest.mark.parametrize("targets", [[], ["--subscription", "team-plan", "--runner", "alpha"]])
def test_clear_floor_takes_exactly_one_target(targets: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _host(monkeypatch, lambda _request: httpx2.Response(200, json=_CLEARED))

    result = CliRunner().invoke(runner_group, ["clear-floor", *targets])

    assert result.exit_code == 2, result.output
    assert "exactly one of --subscription and --runner" in result.output
    assert calls == []


def test_a_subscription_clear_reaches_the_host_as_the_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _host(monkeypatch, lambda _request: httpx2.Response(200, json=_CLEARED))

    result = CliRunner().invoke(runner_group, ["clear-floor", "--subscription", "team-plan"])

    assert result.exit_code == 0, result.output
    assert calls == [("/api/host/subscriptions/team-plan/floor/clear", {"by": "operator"})]
    assert "subscription team-plan" in result.output


def test_a_runner_clear_reaches_that_runner_with_who_cleared_it(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _host(monkeypatch, lambda _request: httpx2.Response(200, json=_CLEARED))

    result = CliRunner().invoke(runner_group, ["clear-floor", "--runner", "alpha", "--by", "alice"])

    assert result.exit_code == 0, result.output
    assert calls == [("/runners/rn_a/api/runner/floor/clear", {"by": "alice"})]


def test_an_undeclared_subscription_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch, lambda _request: httpx2.Response(404, json={"detail": "no subscription nobody on this host"}))

    result = CliRunner().invoke(runner_group, ["clear-floor", "--subscription", "nobody"])

    assert result.exit_code == 1, result.output
    assert "declares no subscription 'nobody'" in result.output


def test_a_runner_spending_a_subscription_is_refused_naming_the_subscription_to_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _host(
        monkeypatch,
        lambda _request: httpx2.Response(409, json={"detail": {"slug": "team-plan", "message": "spends team-plan"}}),
    )

    result = CliRunner().invoke(runner_group, ["clear-floor", "--runner", "alpha"])

    assert result.exit_code == 1, result.output
    assert "clear-floor --subscription team-plan" in result.output


class _RunnerClears:
    def __init__(self) -> None:
        self.clears: list[FloorClear] = []

    def record_floor_clear(self, clear: FloorClear) -> None:
        self.clears.append(clear)


class _SubscriptionClears:
    def __init__(self) -> None:
        self.clears: list[tuple[str, FloorClear]] = []

    def record_floor_clear(self, slug: str, clear: FloorClear) -> None:
        self.clears.append((slug, clear))


def test_a_runner_spending_a_subscription_records_no_clear_of_its_own() -> None:
    store = _RunnerClears()
    service = RunnerFloorClearService(store, "team-plan", FixedClock(_NOW))

    with pytest.raises(RunnerFloorClearRefused) as refused:
        service.clear(by="operator")

    assert refused.value.slug == "team-plan"
    assert store.clears == []


def test_a_clear_is_recorded_at_the_clock_s_now() -> None:
    runner_store, host_store = _RunnerClears(), _SubscriptionClears()

    RunnerFloorClearService(runner_store, None, FixedClock(_NOW)).clear(by="bob")
    SubscriptionFloorClearService(host_store, FixedClock(_NOW)).clear("team-plan", by="alice")

    assert runner_store.clears == [FloorClear(_NOW, "bob")]
    assert host_store.clears == [("team-plan", FloorClear(_NOW, "alice"))]
