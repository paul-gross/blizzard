"""Runner restart-resume — the graceful-restart re-attach (issue #12, D-082, unit tier).

A graceful ``blizzard-runner`` shutdown marks every in-flight lease for restart-resume, and
the startup RESUME step re-attaches each marked session **in place** — same lease/epoch/
session, only the pid rewritten, no retry consumed — or **abandons** a chunk the hub
reassigned/detached while the runner was down (D-088). These drive the marking hook and the
RESUME step directly against a real tmp store with fakes at the seams (``bzh:steppable-loop``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import mark_resume_intents, resume
from blizzard.runner.loop.tick import tick
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, RouteView
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
    """A build lease spawned into env e1 with a live worker, plus its binding."""
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


def _running_chunk(chunk="ch_1", *, runner_id="r1"):  # type: ignore[no-untyped-def]
    return ChunkDetail(
        chunk_id=chunk,
        graph_id="gr_1",
        status=ChunkStatus.RUNNING,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id=runner_id, workspace_id="ws1", environment_ids=["e1"]),
    )


# --------------------------------------------------------------------------- #
# Marking — the graceful-shutdown hook
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_marks_active_session_bearing_lease(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)

    marked = mark_resume_intents(store, now=_NOW)

    assert marked == 1
    assert store.resume_intent_lease_ids() == {"lease_1"}


@pytest.mark.unit
def test_marking_skips_parked_pending_and_unspawned(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    # A parked lease (dormant on a question) — its resume is the answer, not a restart.
    _seed_running_lease(store, chunk="ch_park", lease="lease_park")
    store.record_ask(
        lease_id="lease_park",
        chunk_id="ch_park",
        question_id="qn_1",
        question="Q",
        options=[],
        session_id="sess-a",
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_park", chunk_id="ch_park", question_id="qn_1", parked_at=_NOW)
    # A lease whose completion is already buffered — its node-step is done, awaiting flush.
    _seed_running_lease(store, chunk="ch_pending", lease="lease_pending")
    store.enqueue_outbound(
        kind="completion.submitted", chunk_id="ch_pending", lease_id="lease_pending", payload="{}", created_at=_NOW
    )
    # An unspawned lease (minted, never reached spawn-return) — nothing to resume.
    store.record_lease(
        NewLease(
            lease_id="lease_orphan",
            chunk_id="ch_orphan",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )

    marked = mark_resume_intents(store, now=_NOW)

    assert marked == 0
    assert store.resume_intent_lease_ids() == set()


@pytest.mark.unit
def test_remark_across_two_restarts_reopens_the_intent(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    # First restart: mark then clear (RESUME consumed it).
    mark_resume_intents(store, now=_NOW)
    store.record_resume_clear(lease_id="lease_1", cleared_at=_NOW)
    assert store.resume_intent_lease_ids() == set()
    # Second graceful restart while the same lease is still in flight — re-marked strictly later.
    mark_resume_intents(store, now=_NOW + timedelta(minutes=5))
    assert store.resume_intent_lease_ids() == {"lease_1"}


# --------------------------------------------------------------------------- #
# RESUME — resume in place
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_resume_in_place_keeps_lease_epoch_session_rewrites_pid(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    mark_resume_intents(store, now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    probe = FakeProbe(alive={(100, "start-100"), (4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    resume(ctx)

    # Same session resumed in place with the restart message; the survivor pid was killed first.
    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The supervisor restarted; continue your task where you left off.")
    ]
    assert probe.killed == [100]
    # Same lease/epoch/session, only the pid rewritten to the resumed process.
    lease = store.active_lease("lease_1")
    assert lease is not None
    assert (lease.lease_id, lease.epoch, lease.session_id, lease.pid) == ("lease_1", 1, "sess-a", 4321)
    # No retry consumed — no new lease minted, no closure recorded.
    assert store.attempt_count("ch_1", "nd_build") == 1
    # Intent consumed — a second RESUME pass is a no-op.
    assert store.resume_intent_lease_ids() == set()


@pytest.mark.unit
def test_resumed_lease_is_not_judged_by_advance(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    mark_resume_intents(store, now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    # The survivor is still alive at tick start (kill-first exercises it); the resumed pid is live.
    probe = FakeProbe(alive={(100, "start-100"), (4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    tick(ctx)

    # RESUME re-attached the session and ADVANCE saw a live worker — no verdict elicited,
    # no completion buffered, nothing worked twice.
    assert harness.judged == []
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 4321


# --------------------------------------------------------------------------- #
# RESUME — abandon a reassigned / detached chunk (no epoch bump)
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_resume_abandons_chunk_reassigned_to_another_runner(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    mark_resume_intents(store, now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk(runner_id="r2")  # another runner holds it now
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=probe)

    resume(ctx)

    # Abandoned: no resume delivered, the survivor killed, the environment released, the
    # lease closed — and no new lease minted (no epoch bump, no requeue).
    assert harness.resumed == []
    assert probe.killed == [100]
    assert provider.released == ["e1"]
    assert store.active_lease("lease_1") is None
    assert store.held_environment_ids() == []
    assert store.latest_epoch("ch_1") == 1  # never bumped
    assert store.resume_intent_lease_ids() == set()


@pytest.mark.unit
def test_resume_abandons_detached_chunk(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    mark_resume_intents(store, now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(  # detached: re-derived ready, route released (D-088)
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.READY,
        current_node_id="nd_build",
        latest_epoch=1,
        route=None,
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict=None), probe=FakeProbe()
    )

    resume(ctx)

    assert provider.released == ["e1"]
    assert store.active_lease("lease_1") is None
    assert store.resume_intent_lease_ids() == set()


@pytest.mark.unit
def test_resume_abandons_chunk_unknown_at_the_hub(tmp_path):  # type: ignore[no-untyped-def]
    """The explicit companion to the two cases above: a 404 (``ChunkNotFoundError``) is
    terminal, not the generic ``HubClientError`` the deferral branch below waits out —
    ``_resume_marked_lease`` abandons on it directly rather than leaving the intent open
    for PULL's ``_release_detached`` to find on some later tick (blizzard#9)."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    mark_resume_intents(store, now=_NOW)

    hub = FakeHub()
    hub.not_found = {"ch_1"}
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict=None), probe=FakeProbe()
    )

    resume(ctx)

    assert provider.released == ["e1"]
    assert store.active_lease("lease_1") is None
    assert store.resume_intent_lease_ids() == set()


# --------------------------------------------------------------------------- #
# RESUME — resilience
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_resume_defers_when_hub_unreachable(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    mark_resume_intents(store, now=_NOW)

    hub = FakeHub()
    hub.down = True  # get_chunk cannot be reached — ownership unverifiable this tick
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    resume(ctx)

    # The intent stays open (retry next tick), the environment stays held, nothing resumed.
    assert store.resume_intent_lease_ids() == {"lease_1"}
    assert harness.resumed == []
    assert store.held_environment_ids() == ["e1"]


@pytest.mark.unit
def test_resume_is_a_noop_without_intents(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)  # in flight, but no graceful-shutdown mark

    hub = FakeHub()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    resume(ctx)

    assert harness.resumed == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100  # untouched


@pytest.mark.unit
def test_resume_clears_intent_for_lease_closed_while_down(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    mark_resume_intents(store, now=_NOW)
    # The lease closed while the runner was down (unusual) — RESUME just clears the intent.
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="failed", closed_at=_NOW)

    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
    )

    resume(ctx)

    assert store.resume_intent_lease_ids() == set()
