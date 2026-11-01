"""Helpers the ``runner_identity_tenant`` tests share across the revisions they prove."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.hub.identity import IdentityVerdictHolder, RunnerIdentityHolder
from blizzard.runner.loop.context import LoopConfig, LoopContext
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    serving,
)

RUNNER = "rn_01JX0000000000000000000000"


REGISTERED = datetime(2026, 10, 6, 12, tzinfo=UTC)


NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def started_context(store: SqlAlchemyRunnerStore, hub: FakeHub, *, checked: bool = False) -> LoopContext:
    """A freshly started runner over ``store``: its holder seeded from the row as composition seeds it
    at boot, its verdict unchecked — or, ``checked``, already matched and its hub ungated."""
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(
            handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="s", pgid=100), verdict=None
        ),
        probe=FakeProbe(alive=set()),
        clock=FixedClock(NOW),
        config=LoopConfig(runner_name="r-claude", runner_id=RUNNER, bindings=serving(1), agents=1),
        registered=False,
        identity_verdict=None if checked else IdentityVerdictHolder("blizzard runner store reset /runner"),
    )
    return dataclasses.replace(ctx, identity=RunnerIdentityHolder(store.runner_identity()))


def tenant_hub(tenant_id: str | None = "ten_default", tenant_name: str | None = "default") -> FakeHub:
    hub = FakeHub(default_runner_id=RUNNER)
    hub.tenant_id, hub.tenant_name = tenant_id, tenant_name
    return hub
