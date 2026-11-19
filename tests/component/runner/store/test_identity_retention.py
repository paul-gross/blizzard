"""Retirement keeps a runner's identity: the host's watch records the hub's proof as the runner's ``retired`` fact."""

from datetime import UTC, datetime
from pathlib import Path

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.runner.hosting.model import HUB, HostedRunnerOp
from blizzard.runner.hosting.retirement import HubNoticedRetirement, RetirementNotices
from blizzard.runner.hub.client import TokenRefusal
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.hub.identity_service import RetiredRunnerWatch
from blizzard.runner.store.internal.host_hosted_runner_store import HostHostedRunnerStore
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.store.scoped import RunnerStoreConnections
from tests.support.runner_fakes import make_store, runner_store_errors
from tests.support.runner_init_fakes import FakeTokenIdentityReader

_NOW = datetime(2026, 11, 1, tzinfo=UTC)


def test_the_retirement_watch_records_the_retired_fact_and_keeps_the_identity(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    identity = RunnerIdentityStore(store.connections)
    runner_id = store.connections.bound_runner_id()
    identity.record_runner_identity(RunnerIdentity(runner_id, "own", _NOW))
    hosted = HostHostedRunnerStore(RunnerStoreConnections(store.engine, runner_store_errors()))
    notices = RetirementNotices()
    watch = RetiredRunnerWatch(
        runner_id=runner_id,
        rows=identity,
        retirement=HubNoticedRetirement(runner_id, hosted, FixedClock(_NOW), notices),
        token_identity=FakeTokenIdentityReader(TokenRefusal(RunnerTokenRefusalReason.RETIRED, runner_id=runner_id)),
    )

    assert watch.record_if_retired() is True
    assert watch.record_if_retired() is False

    assert identity.runner_identity() is not None
    assert hosted.retired() == {runner_id}
    retired = [fact for fact in hosted.facts(runner_id) if fact.op is HostedRunnerOp.RETIRED]
    assert [(fact.door, fact.actor, fact.recorded_at) for fact in retired] == [(HUB, HUB, _NOW)]
    assert notices.take() is True
    assert notices.take() is False
