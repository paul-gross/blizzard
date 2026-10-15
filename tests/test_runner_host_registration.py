"""The registered host's own tick steps: its registration — report, identity, each runner's standing — and the drain
of its own fact lane, over a fake host hub client and a real store."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.host_registration_state import HostRegistrationState
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.runner.hub.client import (
    HostFactPushAck,
    HostHeldRunner,
    HostRecord,
    HostRefusalReason,
    HostRefused,
    HostReport,
    HubClientError,
    IssuedIdentity,
    PushedFact,
    RetiredHostRunner,
)
from blizzard.runner.hub.host_identity import HostIdentity, HostIdentityHolder
from blizzard.runner.hub.host_registration import HostRegistration, HostRegistrationHolder
from blizzard.runner.hub.identity import HostStanding, IdentityVerdictHolder, RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.hub.verified import IdentityNotVerified, VerifiedHubClient
from blizzard.runner.lifecycle.host_lane_drain import HOST_LANE_DRAIN_LIMIT, HostLaneDrain
from tests.runner_fakes import FakeHub, make_host_stores, make_store

pytestmark = pytest.mark.component

_AT = datetime(2026, 10, 1, 12, tzinfo=UTC)
_HOST = "hs_1"
_TENANT = "tn_1"


@dataclass
class _FakeHostHub:
    """An :class:`IHostHubClient` answering each call from what a test set."""

    report_answer: HostRecord | HostRefused | HubClientError = field(
        default_factory=lambda: HostRecord(host_id=_HOST, tenant_id=_TENANT, host_name="host-a")
    )
    push_answer: HostFactPushAck | HostRefused | HubClientError | None = None
    reports: list[HostReport] = field(default_factory=list)
    pushes: list[list[PushedFact]] = field(default_factory=list)

    def report(self, report: HostReport) -> HostRecord | HostRefused:
        self.reports.append(report)
        if isinstance(self.report_answer, HubClientError):
            raise self.report_answer
        return self.report_answer

    def record(self) -> HostRecord | HostRefused:
        raise AssertionError("the registration step reports; it never reads the record alone")

    def push_facts(self, facts: Sequence[PushedFact]) -> HostFactPushAck | HostRefused:
        self.pushes.append(list(facts))
        if isinstance(self.push_answer, HubClientError):
            raise self.push_answer
        if self.push_answer is not None:
            return self.push_answer
        seqs = [fact.seq for fact in facts]
        return HostFactPushAck(high_water=max(seqs), applied=seqs, already_applied=[], rejected=[])

    def add_runner(self, name: str, *, projects: tuple[str, ...] | None = None) -> IssuedIdentity | HostRefused:
        raise AssertionError("not called")

    def retire_runner(self, runner_id: str, *, force: bool = False) -> RetiredHostRunner | HostRefused:
        raise AssertionError("not called")


@dataclass(frozen=True)
class _Runner:
    runner_id: str
    identity: RunnerIdentityHolder
    identity_verdict: IdentityVerdictHolder


def _runner(runner_id: str, *, tenant: str | None = _TENANT) -> _Runner:
    verdict = IdentityVerdictHolder("reset")
    verdict.record(IdentityState.MATCHED, store_runner_id=runner_id, token_runner_id=runner_id, checked_at=_AT)
    return _Runner(
        runner_id,
        RunnerIdentityHolder(RunnerIdentity(runner_id, runner_id, _AT, tenant_id=tenant)),
        verdict,
    )


def _held(*runner_ids: str, host: str = _HOST, tenant: str = _TENANT) -> HostRecord:
    return HostRecord(
        host_id=host,
        tenant_id=tenant,
        host_name="host-a",
        runners=tuple(HostHeldRunner(runner_id=r, runner_name=r) for r in runner_ids),
    )


@dataclass
class _World:
    hub: _FakeHostHub
    step: HostRegistration
    drain: HostLaneDrain
    runners: list[_Runner]
    status: HostRegistrationHolder
    identity: HostIdentityHolder
    stores: object


def _world(tmp_path: Path, *runners: _Runner) -> _World:
    stores = make_host_stores(make_store(f"sqlite:///{tmp_path / 'host.db'}", runner_id=None))
    hub = _FakeHostHub()
    status = HostRegistrationHolder.for_host(registered=True)
    identity = HostIdentityHolder()
    clock = FixedClock(_AT)
    held = list(runners)
    step = HostRegistration(
        client=hub,
        report=lambda: HostReport(url="https://host.example"),
        identities=stores.host_identity,
        identity=identity,
        status=status,
        runners=lambda: held,
        clock=clock,
    )
    drain = HostLaneDrain(client=hub, lane=stores.host_lane, registration=status, clock=clock)
    return _World(hub, step, drain, held, status, identity, stores)


def _buffer(world: _World, count: int) -> None:
    for index in range(count):
        world.stores.host_lane.record_host_external_usage_attempt(  # type: ignore[attr-defined]
            slug=f"s{index}",
            sampled_at=_AT,
            payload=None,
            report_kind="external_subscription_usage.missed",
            report_payload=json.dumps({"slug": f"s{index}"}),
        )


def test_the_first_answer_records_the_host_identity_and_registers(tmp_path: Path) -> None:
    world = _world(tmp_path, _runner("rn_a"))
    world.hub.report_answer = _held("rn_a")
    world.step.run()
    assert world.stores.host_identity.host_identity() == HostIdentity(_HOST, _TENANT, _AT)  # type: ignore[attr-defined]
    assert world.identity.current() == HostIdentity(_HOST, _TENANT, _AT)
    status = world.status.current()
    assert status.state is HostRegistrationState.REGISTERED
    assert status.held_runner_ids == ("rn_a",)
    assert world.hub.reports == [HostReport(url="https://host.example")]


def test_a_later_answer_naming_another_host_is_a_refusal_not_an_update(tmp_path: Path) -> None:
    runner = _runner("rn_a")
    world = _world(tmp_path, runner)
    world.hub.report_answer = _held()
    world.step.run()
    assert runner.identity_verdict.verdict().state is IdentityState.NOT_HELD
    world.hub.report_answer = _held("rn_a", host="hs_other")
    world.step.run()
    status = world.status.current()
    assert status.state is HostRegistrationState.REFUSED
    assert "hs_other" in (status.detail or "")
    assert world.identity.current() == HostIdentity(_HOST, _TENANT, _AT)
    assert world.stores.host_identity.host_identity() == HostIdentity(_HOST, _TENANT, _AT)  # type: ignore[attr-defined]
    # The refusing answer's runner list is not applied, and runners keep working.
    assert runner.identity_verdict.standing() is HostStanding.UNKNOWN
    assert runner.identity_verdict.matched()


def test_a_row_recorded_before_the_holder_was_seeded_is_the_identity_the_answer_is_judged_against(
    tmp_path: Path,
) -> None:
    world = _world(tmp_path, _runner("rn_a"))
    world.stores.host_identity.record_host_identity(HostIdentity("hs_row", _TENANT, _AT))  # type: ignore[attr-defined]
    world.hub.report_answer = _held("rn_a")
    world.step.run()
    assert world.identity.current() == HostIdentity("hs_row", _TENANT, _AT)
    assert world.status.current().state is HostRegistrationState.REFUSED


def test_standing_changes_take_effect_within_one_host_tick(tmp_path: Path) -> None:
    held, released, elsewhere = _runner("rn_a"), _runner("rn_b"), _runner("rn_c", tenant="tn_other")
    world = _world(tmp_path, held, released, elsewhere)
    gates = {r.runner_id: VerifiedHubClient(FakeHub(), r.identity_verdict) for r in world.runners}
    world.hub.report_answer = _held("rn_a", "rn_b", "rn_c")
    world.step.run()
    assert held.identity_verdict.matched() and released.identity_verdict.matched()

    world.hub.report_answer = _held("rn_a", "rn_c")
    world.step.run()
    assert held.identity_verdict.verdict().state is IdentityState.MATCHED
    released_verdict = released.identity_verdict.verdict()
    assert released_verdict.state is IdentityState.NOT_HELD
    assert released_verdict.reason == f"not held under this host ({_HOST}) at its hub"
    tenant_verdict = elsewhere.identity_verdict.verdict()
    assert tenant_verdict.state is IdentityState.TENANT_DIFFERS
    assert "tn_other" in (tenant_verdict.reason or "")
    gates["rn_a"].peek_queue((), policy="fifo")
    with pytest.raises(IdentityNotVerified):
        gates["rn_b"].peek_queue((), policy="fifo")

    world.hub.report_answer = _held("rn_a", "rn_b")
    world.step.run()
    assert released.identity_verdict.matched()


def test_a_token_refusal_and_a_host_refusal_never_clear_each_other() -> None:
    verdict = IdentityVerdictHolder("reset")
    verdict.stand(HostStanding.NOT_HELD, reason="not held")
    verdict.record(IdentityState.MATCHED, store_runner_id="rn_a", token_runner_id="rn_a", checked_at=_AT)
    assert verdict.verdict().state is IdentityState.NOT_HELD
    verdict.refuse(store_runner_id="rn_a", reason="revoked", checked_at=_AT)
    verdict.stand(HostStanding.HELD)
    assert verdict.verdict().state is IdentityState.REFUSED
    assert verdict.token_refusing()
    verdict.record(IdentityState.MATCHED, store_runner_id="rn_a", token_runner_id="rn_a", checked_at=_AT)
    assert verdict.matched()


@pytest.mark.parametrize(
    ("answer", "state"),
    [
        (HostRefused(HostRefusalReason.NOT_FOUND, 404, "Not Found"), HostRegistrationState.NO_HOSTS),
        (HostRefused(HostRefusalReason.CREDENTIAL_REFUSED, 401, "unknown"), HostRegistrationState.REFUSED),
    ],
)
def test_a_refused_report_leaves_every_runner_working_and_the_lane_buffered(
    tmp_path: Path, answer: HostRefused, state: HostRegistrationState
) -> None:
    runner = _runner("rn_a")
    world = _world(tmp_path, runner)
    world.hub.report_answer = _held()
    world.step.run()
    assert not runner.identity_verdict.matched()
    world.hub.report_answer = answer
    _buffer(world, 1)
    world.step.run()
    world.drain.run()
    assert world.status.current().state is state
    assert runner.identity_verdict.matched()
    assert world.hub.pushes == []
    assert world.stores.host_lane.host_buffered_count() == 1  # type: ignore[attr-defined]


def test_an_unreachable_hub_keeps_the_standings_it_last_judged(tmp_path: Path) -> None:
    runner = _runner("rn_a")
    world = _world(tmp_path, runner)
    world.hub.report_answer = _held()
    world.step.run()
    world.hub.report_answer = HubClientError("down")
    world.step.run()
    assert world.status.current().state is HostRegistrationState.UNREACHABLE
    assert runner.identity_verdict.verdict().state is IdentityState.NOT_HELD


def test_the_lane_drains_only_once_registered_and_clears_what_the_hub_answered(tmp_path: Path) -> None:
    world = _world(tmp_path)
    _buffer(world, 3)
    world.drain.run()
    assert world.hub.pushes == []
    world.step.run()
    world.drain.run()
    assert [[fact.seq for fact in push] for push in world.hub.pushes] == [[1, 2, 3]]
    assert world.hub.pushes[0][0].payload == {"slug": "s0"}
    lane = world.stores.host_lane  # type: ignore[attr-defined]
    assert lane.host_buffered_count() == 0
    assert lane.host_high_water_seen() == 3


def test_a_drain_pass_is_bounded_and_a_failed_push_keeps_the_facts(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.step.run()
    _buffer(world, HOST_LANE_DRAIN_LIMIT + 5)
    world.hub.push_answer = HubClientError("down")
    world.drain.run()
    lane = world.stores.host_lane  # type: ignore[attr-defined]
    assert len(world.hub.pushes[0]) == HOST_LANE_DRAIN_LIMIT
    assert lane.host_buffered_count() == HOST_LANE_DRAIN_LIMIT + 5
    world.hub.push_answer = None
    world.drain.run()
    world.drain.run()
    assert lane.host_buffered_count() == 0
    assert [len(push) for push in world.hub.pushes] == [HOST_LANE_DRAIN_LIMIT, HOST_LANE_DRAIN_LIMIT, 5]


def test_the_lane_clears_only_up_to_the_answered_mark_and_never_moves_it_back(tmp_path: Path) -> None:
    world = _world(tmp_path)
    world.step.run()
    _buffer(world, 3)
    lane = world.stores.host_lane  # type: ignore[attr-defined]
    world.hub.push_answer = HostFactPushAck(high_water=1, applied=[1], already_applied=[], rejected=[])
    world.drain.run()
    assert (lane.host_high_water_seen(), lane.host_buffered_count()) == (1, 2)
    world.hub.push_answer = None
    world.drain.run()
    assert (lane.host_high_water_seen(), lane.host_buffered_count()) == (3, 0)
    lane.record_host_high_water(seq=1, at=_AT)
    assert lane.host_high_water_seen() == 3
