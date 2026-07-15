"""Runner crash-resume — the ungraceful-restart re-attach (issue #13, D-055/D-082, unit tier).

An involuntary ``kill -9`` / OOM / reboot never runs the graceful shutdown marker, so startup
must detect the sessions killed mid-work itself: :func:`mark_crash_resume_intents` marks an
in-flight lease for same-lease resume iff its worker's process is gone, it recorded **no**
session-end (it did not declare done), and its heartbeat is **not** stale (it was working when
killed). The mark feeds the *same* startup RESUME step #12 built, so these drive the marking
against a real tmp store with fakes at the seams (``bzh:steppable-loop``), then hand off to the
existing resume machinery. The routing counterparts — clean exit → judge, stall → reap+retry —
are asserted as skips here and exercised end to end in ``test_runner_restart_resume.py`` /
``test_runner_loop.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import mark_crash_resume_intents, resume
from blizzard.runner.loop.tick import tick
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
)
from tests.test_runner_restart_resume import _running_chunk

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_STALE_LATER = _NOW + timedelta(hours=2)  # past HEARTBEAT_STALENESS_THRESHOLD (~1h)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_running_lease(  # type: ignore[no-untyped-def]
    store, *, chunk="ch_1", lease="lease_1", pid=100, start="start-100", session="sess-a", epoch=1, created=_NOW
):
    """A build lease spawned into env e1, plus its binding — the worker in flight at crash time."""
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
            created_at=created,
        )
    )
    store.record_spawn(lease, pid=pid, process_start_time=start, session_id=session)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=created)


# --------------------------------------------------------------------------- #
# Marking — startup crash detection
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_marks_worker_killed_mid_work(tmp_path):  # type: ignore[no-untyped-def]
    """Dead pid, no session-end, fresh heartbeat → a crash to resume in place."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)  # was actively working when killed

    marked = mark_crash_resume_intents(store, process=FakeProbe(alive=set()), now=_NOW)

    assert marked == 1
    assert store.resume_intent_lease_ids() == {"lease_1"}


@pytest.mark.unit
def test_skips_worker_that_declared_done(tmp_path):  # type: ignore[no-untyped-def]
    """A dead pid **with** a recorded session-end is a clean exit ADVANCE judges — never resumed."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    store.record_session_end(lease_id="lease_1", ended_at=_NOW)  # the worker declared done

    marked = mark_crash_resume_intents(store, process=FakeProbe(alive=set()), now=_NOW)

    assert marked == 0
    assert store.resume_intent_lease_ids() == set()


@pytest.mark.unit
def test_skips_stalled_worker(tmp_path):  # type: ignore[no-untyped-def]
    """A worker already stalled at crash time (stale heartbeat) is left to reap+retry — unchanged."""
    store = _store(tmp_path)
    _seed_running_lease(store)  # last activity = created_at = _NOW; measured stale two hours on

    marked = mark_crash_resume_intents(store, process=FakeProbe(alive=set()), now=_STALE_LATER)

    assert marked == 0
    assert store.resume_intent_lease_ids() == set()


@pytest.mark.unit
def test_skips_orphaned_but_alive_worker(tmp_path):  # type: ignore[no-untyped-def]
    """A bare kill of only the runner pid left the worker alive — re-adopted via its heartbeat, not re-spawned."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)

    marked = mark_crash_resume_intents(store, process=FakeProbe(alive={(100, "start-100")}), now=_NOW)

    assert marked == 0
    assert store.resume_intent_lease_ids() == set()


@pytest.mark.unit
def test_skips_parked_pending_and_unspawned(tmp_path):  # type: ignore[no-untyped-def]
    """The same non-crash shapes the graceful marker skips: dormant / already-elicited / never-spawned."""
    store = _store(tmp_path)
    # Parked (dormant on a question) — its resume is the answer, not a restart.
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
    # Pending — its completion is already buffered, awaiting flush.
    _seed_running_lease(store, chunk="ch_pending", lease="lease_pending")
    store.enqueue_outbound(
        kind="completion.submitted", chunk_id="ch_pending", lease_id="lease_pending", payload="{}", created_at=_NOW
    )
    # Unspawned — minted, never reached spawn-return, so nothing to resume.
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

    marked = mark_crash_resume_intents(store, process=FakeProbe(alive=set()), now=_NOW)

    assert marked == 0
    assert store.resume_intent_lease_ids() == set()


# --------------------------------------------------------------------------- #
# Hand-off — the marked lease flows through the existing RESUME step
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_marked_crash_lease_resumes_in_place(tmp_path):  # type: ignore[no-untyped-def]
    """A crash-marked lease re-attaches under the same lease/epoch/session — only the pid rewritten, no retry."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    mark_crash_resume_intents(store, process=FakeProbe(alive=set()), now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    probe = FakeProbe(alive={(4321, "start-4321")})  # the survivor is already gone; the resumed pid is live
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    resume(ctx)

    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The supervisor restarted; continue your task where you left off.")
    ]
    lease = store.active_lease("lease_1")
    assert lease is not None
    assert (lease.lease_id, lease.epoch, lease.session_id, lease.pid) == ("lease_1", 1, "sess-a", 4321)
    assert store.attempt_count("ch_1", "nd_build") == 1  # no retry consumed
    assert store.resume_intent_lease_ids() == set()  # intent consumed


@pytest.mark.unit
def test_crash_resumed_lease_is_not_judged_by_advance(tmp_path):  # type: ignore[no-untyped-def]
    """Over a full tick, RESUME re-attaches the crash lease before ADVANCE could fail it verdict-less."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    mark_crash_resume_intents(store, process=FakeProbe(alive=set()), now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    probe = FakeProbe(alive={(4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    tick(ctx)

    assert harness.judged == []  # never elicited a verdict
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 4321
