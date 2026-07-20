"""A live runner learns its chunk was detached (issue #38).

``_reconcile_leases``, folded into PULL between ``_sync_registry`` and the flush, asks the
hub — per active lease, on every tick — whether this runner still holds the chunk's route,
and abandons (kill the worker, release the environments, close the lease ``released``, no
epoch bump, no requeue fact, no retry consumed) any lease it no longer holds. That same
sweep also parks a lease the operator paused (issue #46), off the same ``get_chunk``; the
pause half is covered by ``test_chunk_pause.py``, the detach half here. This is the
live-tick counterpart of restart-resume's ``_resume_marked_lease`` (``test_runner_restart_
resume.py``), which only ever runs after a restart — these tests are the live-tick half, so
they live here rather than falsifying that file's restart-scoped docstring
(``canon:truthful-names``).

The central design point under test: the predicate is **route-only**, not status-and-route.
A live runner legitimately holds an active lease while its chunk derives ``delivering``,
``waiting_on_human``, or ``needs_human`` — copying restart-resume's ``status == RUNNING and
ours`` predicate here would wrongly abandon every one of those. Route identity — ``route is
None`` (detached) or ``route.runner_id`` naming another runner (reassigned) — is the correct
and sufficient signal for every *non-terminal* status.

One exception, added for issue #118: ``stopped`` is checked **ahead of** route identity and
abandons even when the route still names this runner. ``stop`` releases a live route in the
same store transaction as its terminal fact, so ordinarily the route-is-gone branch above
already catches it — this status branch is a backstop for the narrow window where the hub's
own claim guard or a crash could otherwise leave a stopped chunk routed to a runner that would
never see the release (``test_pull_abandons_a_lease_whose_chunk_is_stopped_though_still_routed_
to_this_runner`` below), not a second general status-keyed predicate.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.work import DEFAULT_MODEL, ChunkStatus
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import advance, fill, pull, reap
from blizzard.runner.loop.tick import tick
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, RouteView
from blizzard.wire.facts import ESCALATION_RECORDED, LEASE_MINTED
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


def _seed_orphan_lease(store, *, chunk="ch_1", lease="lease_1", retries_max=0):  # type: ignore[no-untyped-def]
    """A lease minted but never spawned (a crash in FILL's mint->spawn window) — REAP's own
    orphan case, with no retry budget left so the very first reap attempt exhausts it."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=retries_max,
            created_at=_NOW,
        )
    )
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _detached_chunk(chunk="ch_1", *, status=ChunkStatus.READY):  # type: ignore[no-untyped-def]
    return ChunkDetail(
        chunk_id=chunk,
        graph_id="gr_1",
        status=status,
        current_node_id="nd_build",
        latest_epoch=1,
        model=DEFAULT_MODEL,
        route=None,
    )


def _routed_chunk(chunk="ch_1", *, status: ChunkStatus, runner_id="r1"):  # type: ignore[no-untyped-def]
    return ChunkDetail(
        chunk_id=chunk,
        graph_id="gr_1",
        status=status,
        current_node_id="nd_build",
        latest_epoch=1,
        model=DEFAULT_MODEL,
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


@pytest.mark.unit
def test_pull_abandons_a_lease_whose_chunk_is_stopped_though_still_routed_to_this_runner(tmp_path):  # type: ignore[no-untyped-def]
    """The must-fix-1 backstop (issue #118): the hub still routes the chunk to this
    runner, but it derives ``stopped``. The route-only predicate above would leave this
    alone (the route names this runner) — this status branch catches it directly,
    honoring the terminal fact rather than depending on the route release having landed."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _routed_chunk(status=ChunkStatus.STOPPED, runner_id="r1")
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    pull(ctx)

    assert probe.killed == [100]
    assert provider.released == ["e1"]
    assert store.active_lease("lease_1") is None


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
# REAP races ahead of PULL's own detach sweep (blizzard#38 slice 5)
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_reap_abandons_instead_of_escalating_a_detached_chunk(tmp_path):  # type: ignore[no-untyped-def]
    """Tick order is REAP -> RESUME -> PULL -> FILL -> ADVANCE, so a lease whose retry budget
    REAP exhausts can reach the requeue-or-escalate decision *before* PULL's own
    ``_reconcile_leases`` ever gets to ask the hub about it this tick. If the hub already
    detached this chunk (the operator released it to ``ready``), escalating anyway would post
    an ``escalation.recorded`` fact this same tick's flush cannot retract — flipping the chunk
    back to ``needs_human`` behind the operator's back, and PULL cannot un-post a fact once
    flushed. REAP's exhausted-budget path must re-ask the same ownership question and abandon
    in place instead of escalating — the identical outcome PULL would reach later this tick,
    just without the intervening false escalation."""
    store = _store(tmp_path)
    _seed_orphan_lease(store, retries_max=0)
    hub = FakeHub()
    hub.chunks["ch_1"] = _detached_chunk()  # the operator detached it before this tick started
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe()
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    reap(ctx)

    assert store.pending_outbound() == []  # no escalation.recorded fact posted
    assert provider.released == ["e1"]  # abandoned — envs released, same as PULL's own detach path
    assert store.active_lease("lease_1") is None  # lease closed
    assert store.latest_epoch("ch_1") == 1  # no requeue, no new lease minted


@pytest.mark.unit
def test_reap_still_escalates_an_exhausted_lease_that_is_still_ours(tmp_path):  # type: ignore[no-untyped-def]
    """The companion case: an exhausted-budget lease whose chunk the hub still routes here
    escalates exactly as before — the new ownership check must not suppress a genuine
    escalation, only one that would race ahead of a detach."""
    store = _store(tmp_path)
    _seed_orphan_lease(store, retries_max=0)
    hub = FakeHub()
    hub.chunks["ch_1"] = _routed_chunk(status=ChunkStatus.RUNNING)  # still ours
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe()
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    reap(ctx)

    pending = store.pending_outbound()
    assert len(pending) == 1 and pending[0].kind == ESCALATION_RECORDED
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


# --------------------------------------------------------------------------- #
# A chunk unknown at the hub (404) is terminal, not a transport failure (blizzard#9)
# --------------------------------------------------------------------------- #
#
# A hub store reset (or any other cause of the chunk record vanishing) surfaces as a 404
# on the same `GET /chunks/{id}` this module's detach sweep already polls every tick —
# `ChunkNotFoundError`, not the generic `HubClientError` an unreachable hub or a 5xx
# raises. Before blizzard#9, both were caught identically ("hub unreachable, keep
# working"), so a held chunk the hub no longer knows about looped forever: the runner
# never released the environment or claimed new work. The fix reads the 404 as detached
# too, so it flows through the exact same abandon path as a genuine detach/reassignment.


@pytest.mark.unit
def test_pull_abandons_a_live_lease_whose_chunk_the_hub_reports_unknown(tmp_path):  # type: ignore[no-untyped-def]
    """The regression pin: an exited worker still holding its env, its chunk 404ing at the
    hub (a store reset), is reaped and released rather than retried forever."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.not_found = {"ch_1"}
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe()  # the worker has already exited — no alive pid
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    pull(ctx)

    assert probe.killed == [100]  # worker reaped (best-effort — already exited)
    assert provider.released == ["e1"]  # environment released
    assert store.active_lease("lease_1") is None  # lease closed
    assert store.latest_epoch("ch_1") == 1  # no epoch bump
    assert store.pending_outbound() == []  # no requeue, no escalation
    assert store.attempt_count("ch_1", "nd_build") == 1  # no retry consumed


@pytest.mark.unit
def test_pull_defers_a_live_lease_on_a_transient_hub_failure(tmp_path):  # type: ignore[no-untyped-def]
    """The companion case: a transient hub failure (unreachable / 5xx) must NOT be read as
    the chunk being gone — the env stays held and the lease survives untouched, retried
    next tick, exactly as :func:`test_pull_defers_when_hub_unreachable` already pins for
    the plain-detach predicate. Kept alongside the 404 regression above so the two
    outcomes (terminal vs. retryable) are asserted side by side."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.down = True  # a transient hub failure — not a 404
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe()  # the worker has already exited — no alive pid
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    pull(ctx)

    assert probe.killed == []  # not reaped — a transient failure is not terminal
    assert provider.released == []  # environment stays held
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100  # lease survives, retried next tick


@pytest.mark.unit
def test_advance_held_chunk_unknown_at_the_hub_releases_envs(tmp_path):  # type: ignore[no-untyped-def]
    """The leaseless counterpart: a chunk parked at a hub node (envs held, no active lease)
    whose ``GET /chunks/{id}`` now 404s is released the same way a landed delivery is —
    :func:`_advance_held_chunk`'s own terminal branch, distinct from the active-lease path
    :func:`_reassigned_or_detached` covers above."""
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.not_found = {"ch_1"}
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = _ctx(store, hub, provider=provider, probe=FakeProbe())

    advance(ctx)

    assert provider.released == ["e1"]
    assert store.bindings_for_chunk("ch_1") == []


@pytest.mark.unit
def test_advance_held_chunk_defers_on_a_transient_hub_failure(tmp_path):  # type: ignore[no-untyped-def]
    """Companion to the above: an unreachable hub leaves the held-but-leaseless chunk's
    environment untouched, retried next tick."""
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.down = True
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = _ctx(store, hub, provider=provider, probe=FakeProbe())

    advance(ctx)

    assert provider.released == []
    assert len(store.bindings_for_chunk("ch_1")) == 1  # unchanged, still held


@pytest.mark.unit
def test_reap_abandons_instead_of_escalating_a_chunk_unknown_at_the_hub(tmp_path):  # type: ignore[no-untyped-def]
    """The exhausted-budget escalate guard shares the same ownership predicate
    (:func:`_reassigned_or_detached`), so a chunk that 404s there is abandoned instead of
    escalated too — mirroring ``test_reap_abandons_instead_of_escalating_a_detached_chunk``
    for the unknown-chunk cause."""
    store = _store(tmp_path)
    _seed_orphan_lease(store, retries_max=0)
    hub = FakeHub()
    hub.not_found = {"ch_1"}
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe()
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    reap(ctx)

    assert store.pending_outbound() == []  # no escalation.recorded fact posted
    assert provider.released == ["e1"]  # abandoned — envs released
    assert store.active_lease("lease_1") is None
    assert store.latest_epoch("ch_1") == 1  # no requeue, no new lease minted


@pytest.mark.unit
def test_reap_orphan_requeue_releases_envs_when_chunk_unknown_at_the_hub(tmp_path):  # type: ignore[no-untyped-def]
    """The requeue path's own 404 guard: REAP's ``_fail_attempt`` closes the exhausted
    attempt and calls ``_requeue`` *before* PULL's own live-tick sweep
    (``_reconcile_leases``) ever runs this tick, and by the time ``_requeue`` calls
    ``get_envelope`` the prior lease is already closed — so there is no active lease
    left for that sweep to find and abandon later. Left as a generic ``HubClientError``,
    a chunk gone by requeue time would hold its environment forever, the same shape
    issue #9 fixed for the active-lease case."""
    store = _store(tmp_path)
    _seed_orphan_lease(store, retries_max=2)  # under budget — requeues rather than escalates
    hub = FakeHub()
    hub.not_found = {"ch_1"}
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe()
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    reap(ctx)

    assert provider.released == ["e1"]  # environment released rather than held forever
    assert store.active_lease("lease_1") is None
    assert store.bindings_for_chunk("ch_1") == []


@pytest.mark.unit
def test_fill_releases_an_interrupted_claim_binding_when_chunk_unknown_at_the_hub(tmp_path):  # type: ignore[no-untyped-def]
    """The interrupted-claim reconciler's own 404 guard: a binding left by a crash in
    FILL's bind->claim->spawn window, whose chunk the hub no longer knows about, is
    released the same way ``_advance_held_chunk`` releases a held-but-leaseless chunk
    (blizzard#9) — not left for the reconciler to keep re-asking about forever."""
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.not_found = {"ch_1"}
    hub.queue = []  # nothing new to fill — the reconciler is the only path that could act
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = _ctx(store, hub, provider=provider, probe=FakeProbe())

    fill(ctx)

    assert provider.released == ["e1"]
    assert store.bindings_for_chunk("ch_1") == []


@pytest.mark.component
def test_tick_releases_a_chunk_unknown_at_the_hub_and_the_next_tick_does_not_reclaim_it(tmp_path):  # type: ignore[no-untyped-def]
    """The full-tick seam, mirroring ``test_tick_releases_a_detached_chunk_and_the_next_
    tick_does_not_reclaim_it``: a 404'ing chunk is reaped and released in one tick, and
    stays released — the runner does not loop on the 404 forever (blizzard#9)."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.not_found = {"ch_1"}
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe()  # the worker has already exited
    ctx = _ctx(store, hub, provider=provider, probe=probe)

    tick(ctx)

    assert probe.killed == [100]
    assert provider.released == ["e1"]
    assert store.active_lease("lease_1") is None
    assert store.live_tenure_chunk_ids() == []
    assert store.bindings_for_chunk("ch_1") == []

    tick(ctx)

    assert store.live_tenure_chunk_ids() == []
    assert store.bindings_for_chunk("ch_1") == []
    assert store.active_lease("lease_1") is None
