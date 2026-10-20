"""The usage-limit brake reaches every runner of the host spending the limited subscription (component tier): one
locked write transaction over the spender set, each runner's fact stamped and reported by its own store, the lift
staying per runner."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import RUNNER_LOCALLY_PAUSED
from blizzard.runner.events.broker import EventBroker, RunnerEventPublisher
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.usage import UsageLimit
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.context import LoopConfig, ResolvedSubscription
from blizzard.runner.loop.steps import Advance
from blizzard.runner.throttle.pause import ILockedBrakes, LocalBrake, PauseService, SpendingRunner, UsageLimitBrakes
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    make_store,
    registered_fixture_store,
    runner_store_errors,
    serving,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]
_REASON = "usage limit: max via claude_code (resets 2026-07-13T14:00Z)"


class _Host:
    """Four runners over one store file: the reporter ``rn_a``, a fellow spender ``rn_b`` of its subscription, ``rn_c``
    spending another, and ``rn_d`` whose identity row was never recorded."""

    def __init__(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        base = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=None)
        self.engine = base._engine
        self.a = registered_fixture_store(self.engine, "rn_a")
        self.b = registered_fixture_store(self.engine, "rn_b")
        self.c = registered_fixture_store(self.engine, "rn_c")
        self.d = SqlAlchemyRunnerStore(self.engine, runner_store_errors(), "rn_d")
        self.broker = EventBroker()

    def spender(self, store: SqlAlchemyRunnerStore, runner_id: str) -> SpendingRunner:
        return SpendingRunner(store, RunnerEventPublisher.over(self.broker, runner_id))

    def brakes(self, *others: SpendingRunner) -> UsageLimitBrakes:
        return UsageLimitBrakes(self.spender(self.a, "rn_a"), others, FixedClock(_NOW))


def _reports(store: SqlAlchemyRunnerStore) -> list[dict[str, object]]:
    return [json.loads(f.payload) for f in store.pending_outbound() if f.kind == RUNNER_LOCALLY_PAUSED]


def test_a_limit_reported_by_one_spender_brakes_every_spender_and_no_other_runner(tmp_path) -> None:  # type: ignore[no-untyped-def]
    host = _Host(tmp_path)

    host.brakes(host.spender(host.b, "rn_b"), host.spender(host.d, "rn_d")).engage(by="usage-limit", reason=_REASON)

    assert (host.a.local_paused(), host.b.local_paused()) == (True, True)
    assert host.a.local_pause_reason() == host.b.local_pause_reason() == _REASON
    assert [r["reason"] for r in _reports(host.a)] == [_REASON]
    assert [r["reason"] for r in _reports(host.b)] == [_REASON]
    # Another subscription's runner is not named; a runner with no identity row has written nothing, so it is skipped.
    assert (host.c.local_paused(), host.d.local_paused()) == (False, False)
    assert _reports(host.c) == _reports(host.d) == []
    announced = [json.loads(e.data) for e in host.broker.snapshot() if e.type == "fact-changed"]
    assert sorted(frame["runner_id"] for frame in announced) == ["rn_a", "rn_b"]


def test_an_already_braked_spender_is_not_braked_again(tmp_path) -> None:  # type: ignore[no-untyped-def]
    host = _Host(tmp_path)
    PauseService(host.b, FixedClock(_NOW)).set_local_pause(paused=True, by="operator")

    host.brakes(host.spender(host.b, "rn_b")).engage(by="usage-limit", reason=_REASON)

    assert host.b.local_pause_reason() is None  # the operator's standing fact, kept
    assert [r["by"] for r in _reports(host.b)] == ["operator"]
    assert [r["by"] for r in _reports(host.a)] == ["usage-limit"]


class _FailingEngagement(SqlAlchemyRunnerStore):
    """A runner's real pause store, except the engagement write raises — the fault in that runner's turn."""

    def record_local_pause_locked(self, *_: object, **__: object) -> int:
        raise RuntimeError("engagement failed")


def _seed_exited_lease(store: SqlAlchemyRunnerStore) -> None:
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")


def _advance_over(host: _Host, *spenders: SpendingRunner) -> None:
    hub = FakeHub()
    hub.default_runner_id = "rn_a"
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    limit = UsageLimit(resets_at=_NOW + timedelta(hours=2), detail="limited")
    ctx = make_context(
        host.a,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass", usage_limit=limit),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_name="rn_a", runner_id="rn_a", bindings=serving(1)),
        subscriptions=(ResolvedSubscription(slug="max", name="Max", provider="anthropic", sample_interval_seconds=60),),
        slug_spenders=spenders,
    )
    Advance(ctx).run()


def test_a_usage_limit_exit_brakes_and_parks_through_the_fan_out(tmp_path) -> None:  # type: ignore[no-untyped-def]
    host = _Host(tmp_path)
    _seed_exited_lease(host.a)

    _advance_over(host, host.spender(host.b, "rn_b"))

    assert (host.a.local_paused(), host.b.local_paused()) == (True, True)
    assert host.a.local_pause_reason() == host.b.local_pause_reason() == _REASON
    assert host.a.pause_parked_lease_ids() == {"lease_1"}


def test_a_fault_in_the_second_spenders_engagement_leaves_neither_braked_and_the_lease_unparked(tmp_path) -> None:  # type: ignore[no-untyped-def]
    host = _Host(tmp_path)
    _seed_exited_lease(host.a)
    failing = SpendingRunner(_FailingEngagement(host.engine, runner_store_errors(), "rn_c"), None)

    with pytest.raises(RuntimeError, match="engagement failed"):
        _advance_over(host, host.spender(host.b, "rn_b"), failing)

    assert (host.a.local_paused(), host.b.local_paused(), host.c.local_paused()) == (False, False, False)
    assert _reports(host.a) == _reports(host.b) == _reports(host.c) == []
    assert host.a.pause_parked_lease_ids() == set()
    assert [e for e in host.broker.snapshot() if e.type == "fact-changed"] == []


def test_a_runner_spending_no_subscription_engages_only_its_own_brake(tmp_path) -> None:  # type: ignore[no-untyped-def]
    host = _Host(tmp_path)

    host.brakes().engage(by="usage-limit", reason="usage limit: claude_code")

    assert host.a.local_paused() is True
    assert (host.b.local_paused(), host.c.local_paused()) == (False, False)


def test_the_lift_is_per_runner_and_leaves_the_neighbour_braked(tmp_path) -> None:  # type: ignore[no-untyped-def]
    host = _Host(tmp_path)
    host.brakes(host.spender(host.b, "rn_b")).engage(by="usage-limit", reason=_REASON)

    PauseService(host.b, FixedClock(_NOW)).set_local_pause(paused=False, by="operator")

    assert host.b.local_paused() is False
    assert host.a.local_paused() is True


def test_the_fallback_reset_reads_only_the_runners_own_subscription(tmp_path) -> None:  # type: ignore[no-untyped-def]
    host = _Host(tmp_path)
    _seed_exited_lease(host.a)
    # Another slug is exhausted; the runner's own has no sample at all.
    host.a.record_external_usage_attempt(
        slug="other",
        sampled_at=_NOW,
        payload=json.dumps(
            {
                "windows": [
                    {
                        "window": "5h",
                        "utilization_pct": 100.0,
                        "resets_at": (_NOW + timedelta(hours=3)).isoformat(),
                        "window_seconds": 18000,
                    }
                ]
            }
        ),
        report_kind="",
        report_payload="",
    )
    hub = FakeHub()
    hub.default_runner_id = "rn_a"
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    ctx = make_context(
        host.a,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass", usage_limit=UsageLimit(resets_at=None, detail="limited")),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_name="rn_a", runner_id="rn_a", bindings=serving(1)),
        subscriptions=(ResolvedSubscription(slug="max", name="Max", provider="anthropic", sample_interval_seconds=60),),
    )

    Advance(ctx).run()

    assert host.a.local_pause_reason() == f"usage limit: max via {CLAUDE_CODE_HARNESS_ID}"


def test_a_direct_engage_reads_and_writes_under_the_brake_lock(tmp_path) -> None:  # type: ignore[no-untyped-def]
    host = _Host(tmp_path)
    seen: list[str] = []

    class _Spy(SqlAlchemyRunnerStore):
        def local_brake_locked(self, handle: ILockedBrakes) -> LocalBrake:
            seen.append("read")
            return super().local_brake_locked(handle)

        def record_local_pause_locked(self, handle: ILockedBrakes, **fact: Any) -> int:
            seen.append("write")
            return super().record_local_pause_locked(handle, **fact)

    spy = _Spy(host.engine, runner_store_errors(), "rn_a")
    service = PauseService(spy, FixedClock(_NOW))
    service.engage(by="usage-limit", reason=_REASON)
    service.engage(by="usage-limit", reason=_REASON)

    assert seen == ["read", "write", "read"]
    assert [r["reason"] for r in _reports(spy)] == [_REASON]
