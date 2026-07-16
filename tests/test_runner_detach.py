"""A live runner learns its chunk was detached (issue #38, D-088).

``_release_detached``, folded into PULL between ``_sync_registry`` and the flush, asks the
hub — per active lease, on every tick — whether this runner still holds the chunk's route,
and abandons (kill the worker, release the environments, close the lease ``released``, no
epoch bump, no requeue fact, no retry consumed) any lease it no longer holds. This is the
live-tick counterpart of restart-resume's ``_resume_marked_lease`` (``test_runner_restart_
resume.py``), which only ever runs after a restart — these tests are the live-tick half, so
they live here rather than falsifying that file's restart-scoped docstring
(``canon:truthful-names``).

The central design point under test: the predicate is **route-only**, not status-and-route.
A live runner legitimately holds an active lease while its chunk derives ``delivering``,
``waiting_on_human``, or ``needs_human`` — copying restart-resume's ``status == RUNNING and
ours`` predicate here would wrongly abandon every one of those. Route identity — ``route is
None`` (detached) or ``route.runner_id`` naming another runner (reassigned) — is the correct
and sufficient signal.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import pull
from blizzard.runner.loop.tick import tick
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, RouteView
from blizzard.wire.facts import LEASE_MINTED
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
)

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_running_lease(  # type: ignore[no-untyped-def]
    store, *, chunk="ch_1", lease="lease_1", pid=100, start="start-100", session="sess-a", epoch=1
):
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
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(lease, pid=pid, process_start_time=start, session_id=session, spawned_at=_NOW)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _detached_chunk(chunk="ch_1", *, status=ChunkStatus.READY):  # type: ignore[no-untyped-def]
    return ChunkDetail(
        chunk_id=chunk, graph_id="gr_1", status=status, current_node_id="nd_build", latest_epoch=1, route=None
    )


def _routed_chunk(chunk="ch_1", *, status: ChunkStatus, runner_id="r1"):  # type: ignore[no-untyped-def]
    return ChunkDetail(
        chunk_id=chunk,
        graph_id="gr_1",
        status=status,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id=runner_id, workspace_id="ws1", environment_ids=["e1"]),
    )


def _ctx(store, hub, *, provider=None, probe=None):  # type: ignore[no-untyped-def]
    return make_context(
        store,
        hub=hub,
        provider=provider if provider is not None else FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=probe if probe is not None else FakeProbe(alive={(100, "start-100")}),
    )


# --------------------------------------------------------------------------- #
# PULL abandons a detached / reassigned live lease
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_pull_abandons_a_live_detached_chunk(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _detached_chunk()  # route released — re-derived ready
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    pull(ctx)

    assert probe.killed == [100]  # worker killed
    assert provider.released == ["e1"]  # environment released
    assert store.active_lease("lease_1") is None  # lease closed
    assert store.latest_epoch("ch_1") == 1  # no epoch bump
    assert store.pending_outbound() == []  # no requeue fact buffered
    assert store.attempt_count("ch_1", "nd_build") == 1  # no retry consumed


@pytest.mark.unit
def test_pull_abandons_a_chunk_reassigned_to_another_runner(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _routed_chunk(status=ChunkStatus.RUNNING, runner_id="other-runner")
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    pull(ctx)

    assert probe.killed == [100]
    assert provider.released == ["e1"]
    assert store.active_lease("lease_1") is None
    assert store.latest_epoch("ch_1") == 1
    assert store.pending_outbound() == []
    assert store.attempt_count("ch_1", "nd_build") == 1


# --------------------------------------------------------------------------- #
# The route-only predicate — a live runner keeps its healthy lease
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_pull_leaves_a_still_running_lease_untouched(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _routed_chunk(status=ChunkStatus.RUNNING)
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    pull(ctx)

    assert probe.killed == []
    assert provider.released == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100


@pytest.mark.unit
@pytest.mark.parametrize("status", [ChunkStatus.DELIVERING, ChunkStatus.WAITING_ON_HUMAN, ChunkStatus.NEEDS_HUMAN])
def test_pull_leaves_a_still_ours_non_running_lease_untouched(tmp_path, status):  # type: ignore[no-untyped-def]
    """The regression test for the route-only predicate: a status check here (copying
    restart-resume's ``status == RUNNING and ours``) would wrongly abandon every one of
    these — a live runner legitimately holds an active lease while its chunk derives
    ``delivering`` (a hub-node hold), ``waiting_on_human`` (an open ask), or ``needs_human``
    (an open escalation)."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _routed_chunk(status=status)
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    pull(ctx)

    assert probe.killed == []
    assert provider.released == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100


# --------------------------------------------------------------------------- #
# Resilience — hub unreachable
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_pull_defers_when_hub_unreachable(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.enqueue_outbound(kind=LEASE_MINTED, chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW)
    hub = FakeHub()
    hub.down = True  # get_chunk (and everything else) unreachable
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    pull(ctx)

    # No abandon, no crash — the lease and its environment survive untouched.
    assert probe.killed == []
    assert provider.released == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100
    # flush_outbound still ran (and, correctly, could not deliver): the buffered fact is
    # still pending, not lost, not acked.
    pending = store.pending_outbound()
    assert len(pending) == 1
    assert pending[0].kind == LEASE_MINTED


# --------------------------------------------------------------------------- #
# Ordering — the abandon happens before the flush, within one PULL
# --------------------------------------------------------------------------- #


class _OrderTrackingHub(FakeHub):
    """A :class:`FakeHub` that records the order ``get_chunk`` / ``push_facts`` are called in."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def get_chunk(self, chunk_id: str) -> ChunkDetail:
        self.calls.append("get_chunk")
        return super().get_chunk(chunk_id)

    def push_facts(self, batch):  # type: ignore[no-untyped-def]
        self.calls.append("push_facts")
        return super().push_facts(batch)


@pytest.mark.unit
def test_pull_abandons_before_it_flushes(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.enqueue_outbound(kind=LEASE_MINTED, chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW)
    hub = _OrderTrackingHub()
    hub.chunks["ch_1"] = _detached_chunk()
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    pull(ctx)

    assert hub.calls == ["get_chunk", "push_facts"]  # the ownership check precedes the flush
    # And the abandon's effects (kill + release + close) are already in place.
    assert probe.killed == [100]
    assert provider.released == ["e1"]
    assert store.active_lease("lease_1") is None


# --------------------------------------------------------------------------- #
# Component tier — the seam across two ticks: released this tick, never re-claimed next
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_tick_releases_a_detached_chunk_and_the_next_tick_does_not_reclaim_it(tmp_path):  # type: ignore[no-untyped-def]
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
