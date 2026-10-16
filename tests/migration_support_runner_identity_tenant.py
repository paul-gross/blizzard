"""Helpers the ``runner_identity_tenant`` tests share across the revisions they prove."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

from blizzard.foundation.clock import FixedClock
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

_RUNNER = "rn_01JX0000000000000000000000"


_REGISTERED = datetime(2026, 10, 6, 12, tzinfo=UTC)


_NOW = datetime(2026, 10, 8, 12, tzinfo=UTC)


def _ctx(store: SqlAlchemyRunnerStore, hub: FakeHub, *, checked: bool = False) -> LoopContext:
    """A freshly started runner over ``store``: its holder seeded from the row as composition seeds it
    at boot, its verdict unchecked — or, ``checked``, already matched and its hub ungated."""
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=FakeProbe(alive=set()),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_name="r-claude", runner_id=_RUNNER, bindings=serving(1)),
        registered=False,
        identity_verdict=None if checked else IdentityVerdictHolder("blizzard runner store reset /runner"),
    )
    return dataclasses.replace(ctx, identity=RunnerIdentityHolder(store.runner_identity()))


def _tenant_hub(tenant_id: str | None = "ten_default", tenant_name: str | None = "default") -> FakeHub:
    hub = FakeHub(default_runner_id=_RUNNER)
    hub.tenant_id, hub.tenant_name = tenant_id, tenant_name
    return hub
