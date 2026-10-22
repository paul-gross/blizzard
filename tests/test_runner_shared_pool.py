"""Two runners of one host drawing from one workspace's pool never hold one environment.

Both runners share one store file, one host-store bundle and one workspace registry, as a host's runners do. Runner A's
acquisition blocks inside the provider; while it is blocked, runner B's heartbeat and outbox writes commit — no store
transaction is open across the provider's preparation — and B's own acquisition waits on the workspace's lock. Once A
binds, B acquires from what is left. The hub claim is scripted ambiguous, so each binding stays held for the check.
Driven against a real (tmp sqlite) runner store with fakes only at the seams."""

from __future__ import annotations

import dataclasses
import threading
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.environments.workspaces import WorkspaceEntry, WorkspaceRegistry
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.hub.client import ClaimRequest, HubClientError, QueueEntry, RouteClaimOutcome
from blizzard.runner.lifecycle.claim import ReadyQueue
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.tick import tick
from tests.runner_fakes import (
    FIXTURE_PROJECT_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    fixture_route,
    make_context,
    make_store,
    runner_invariant_violations,
    runner_store_errors,
    serving,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_WAIT = 5.0


class _AmbiguousClaimHub(FakeHub):
    """A hub whose claim never answers — the binding is left held, as an ambiguous claim leaves it."""

    def claim_route(self, claim: ClaimRequest) -> RouteClaimOutcome:
        self.claims.append(claim)
        raise HubClientError("claim timed out (scripted)")


class _BlockingProvider(FakeProvider):
    """A provider whose first acquisition blocks until released, recording each acquisition it enters."""

    def __init__(self, pool: dict[str, str]) -> None:
        super().__init__(pool)
        self.entered = threading.Semaphore(0)
        self.release_first = threading.Event()
        self.acquisitions: list[str] = []
        self._first = True

    def acquire(self, chunk_id: str, count: int, held_ids: list[str]) -> list[AcquiredEnvironment]:
        self.acquisitions.append(chunk_id)
        self.entered.release()
        if self._first:
            self._first = False
            assert self.release_first.wait(_WAIT)
        return super().acquire(chunk_id, count, held_ids)


def _runner(store: SqlAlchemyRunnerStore, runner_id: str, workspaces: WorkspaceRegistry, chunk_id: str):  # type: ignore[no-untyped-def]
    hub = _AmbiguousClaimHub(default_runner_id=runner_id)
    hub.queue = [QueueEntry(chunk_id=chunk_id, graph_id="gr_1", position=0, project_id=FIXTURE_PROJECT_ID)]
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({}),
        harness=FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t", pgid=1), verdict=None),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_name=runner_id, runner_id=runner_id, bindings=serving(1), agents=1),
        workspaces=workspaces,
    )


def test_two_runners_on_one_workspace_never_hold_one_environment(tmp_path) -> None:  # type: ignore[no-untyped-def]
    provider = _BlockingProvider({"e1": "/ws/e1", "e2": "/ws/e2"})
    workspaces = WorkspaceRegistry([WorkspaceEntry("ws1", provider, spawn_root="")])
    store_a = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id="r_a")
    store_b = SqlAlchemyRunnerStore(store_a._engine, runner_store_errors(), "r_b")
    ctx_a = _runner(store_a, "r_a", workspaces, "ch_a")
    ctx_b = dataclasses.replace(_runner(store_b, "r_b", workspaces, "ch_b"), host_stores=ctx_a.host_stores)

    runner_a = threading.Thread(target=lambda: ReadyQueue.peeked(ctx_a, (fixture_route(),)).claim_one())
    runner_a.start()
    assert provider.entered.acquire(timeout=_WAIT)  # A is inside the provider, holding the workspace's lock

    ctx_b.host_stores.daemon_liveness.record_daemon_liveness(alive_at=_NOW)
    seq = ctx_b.stores.outbound.enqueue_outbound(
        kind="lease.minted", chunk_id="ch_b", lease_id=None, payload="{}", created_at=_NOW
    )
    assert [row.seq for row in store_b.pending_outbound()] == [seq]

    # B cannot take the lock: its claim is "nothing this tick" and never enters the provider.
    assert not ReadyQueue.peeked(ctx_b, (fixture_route(),)).claim_one()
    assert provider.acquisitions == ["ch_a"]

    provider.release_first.set()
    runner_a.join(_WAIT)
    assert not runner_a.is_alive()
    ReadyQueue.peeked(ctx_b, (fixture_route(),)).claim_one()

    held = {
        binding.chunk_id: binding.environment_id
        for binding in ctx_a.host_stores.runner_audit.held_workspace_bindings("ws1")
    }
    assert held == {"ch_a": "e1", "ch_b": "e2"}
    assert [v for v in runner_invariant_violations(store_a) if v.invariant == "runner:unique-env-binding"] == []


def test_a_runner_that_cannot_take_the_workspace_lock_still_finishes_its_tick(tmp_path) -> None:  # type: ignore[no-untyped-def]
    provider = _BlockingProvider({"e1": "/ws/e1", "e2": "/ws/e2"})
    workspaces = WorkspaceRegistry([WorkspaceEntry("ws1", provider, spawn_root="")])
    store_a = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id="r_a")
    store_b = SqlAlchemyRunnerStore(store_a._engine, runner_store_errors(), "r_b")
    ctx_a = _runner(store_a, "r_a", workspaces, "ch_a")
    ctx_b = dataclasses.replace(_runner(store_b, "r_b", workspaces, "ch_b"), host_stores=ctx_a.host_stores)

    runner_a = threading.Thread(target=lambda: ReadyQueue.peeked(ctx_a, (fixture_route(),)).claim_one())
    runner_a.start()
    assert provider.entered.acquire(timeout=_WAIT)  # A is inside the provider, holding the workspace's lock

    ticking = threading.Thread(target=lambda: tick(ctx_b))
    ticking.start()
    ticking.join(_WAIT)
    try:
        assert not ticking.is_alive()  # B's tick returned while A held the lock
        assert provider.acquisitions == ["ch_a"]
    finally:
        provider.release_first.set()
        runner_a.join(_WAIT)
