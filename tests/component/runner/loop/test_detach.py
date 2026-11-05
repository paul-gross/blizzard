"""A live runner learns its chunk was detached.

``_reconcile_leases``, folded into PULL, asks the hub per active lease whether this
runner still holds the chunk's route, abandoning (kill, release, close) any it no
longer holds. The predicate is **route-only**, not status-based, except ``stopped``
(backstop), checked ahead of route identity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import SessionMode
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.hub.client import QueueEntry
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.steps import Fill, Pull
from blizzard.runner.loop.tick import tick
from blizzard.runner.node_steps.chunk_state import ChunkState
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)


def _store(tmp_path: Path):
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_running_lease(
    store: SqlAlchemyRunnerStore,
    *,
    chunk: str = "ch_1",
    lease: str = "lease_1",
    pid: int = 100,
    start: str = "start-100",
    session: str = "sess-a",
    epoch: int = 1,
) -> None:
    """A build lease spawned into env e1 with a live worker, plus its binding — no resume
    intent (a live tick's lease, not a restart-marked one)."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease,
        pid=pid,
        process_start_time=start,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, session),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")


def _detached_chunk(chunk: str = "ch_1", *, status: ChunkStatus = ChunkStatus.READY) -> ChunkState:
    return ChunkState(
        chunk_id=chunk,
        status=status,
        latest_epoch=1,
        route_runner_id=None,
    )


def _ctx(
    store: SqlAlchemyRunnerStore, hub: FakeHub, *, provider: FakeProvider | None = None, probe: FakeProbe | None = None
) -> LoopContext:
    return make_context(
        store,
        hub=hub,
        provider=provider if provider is not None else FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=probe if probe is not None else FakeProbe(alive={(100, "start-100")}),
    )


# Component tier — the seam across two ticks: released this tick, never re-claimed next


def test_tick_releases_a_detached_chunk_and_the_next_tick_does_not_reclaim_it(tmp_path: Path):
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _detached_chunk()
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    tick(ctx)

    assert probe.killed == [100]
    assert provider.released == ["e1"]
    assert store.active_lease("lease_1") is None
    assert store.live_tenure_chunk_ids() == []
    assert store.bindings_for_chunk("ch_1") == []

    # The next tick: nothing left to reap/resume/advance for ch_1, and FILL (which peeks
    # an empty queue here) does not re-claim it — the released chunk stays released.
    tick(ctx)

    assert store.live_tenure_chunk_ids() == []
    assert store.bindings_for_chunk("ch_1") == []
    assert store.active_lease("lease_1") is None


def test_a_chunk_detached_mid_node_and_reclaimed_starts_a_fresh_session_not_a_resume(tmp_path: Path):
    """Detach discards the attempt's session (``domain/execution/recovery.md`` §Detach): a
    re-claim on the same runner spawns a new session instead of resuming the detached one."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _detached_chunk()
    clock = FixedClock(_NOW)
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(alive={(100, "start-100")}),
        clock=clock,
    )

    Pull(ctx).run()  # the detach lands — kill, release, close `released`
    assert store.live_tenure_chunk_ids() == []

    clock.advance(timedelta(minutes=1))
    env = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], epoch=2, session=SessionMode.RESUME
    )
    hub.claim_outcome = claimed_outcome("ch_1", env)
    hub.queue = [QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    Fill(ctx).run()

    assert len(hub.claims) == 1
    assert harness.resume_froms == [None]


def test_tick_releases_a_chunk_unknown_at_the_hub_and_the_next_tick_does_not_reclaim_it(tmp_path: Path):
    """The full-tick seam, mirroring ``test_tick_releases_a_detached_chunk_and_the_next_
    tick_does_not_reclaim_it``: a 404'ing chunk is reaped and released in one tick, and
    stays released — the runner does not loop on the 404 forever."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.not_found = {"ch_1"}
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe()  # the worker has already exited — recorded (pid, start_time) reads dead
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    tick(ctx)

    assert probe.killed == []  # already gone — no blind kill of a possibly-reused pid
    assert provider.released == ["e1"]
    assert store.active_lease("lease_1") is None
    assert store.live_tenure_chunk_ids() == []
    assert store.bindings_for_chunk("ch_1") == []

    tick(ctx)

    assert store.live_tenure_chunk_ids() == []
    assert store.bindings_for_chunk("ch_1") == []
    assert store.active_lease("lease_1") is None
