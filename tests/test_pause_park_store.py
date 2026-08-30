"""The runner-side pause-park store — a separate table pair from park_facts (issue #46).

``OPEN_PAUSE_PARK`` must use a timestamp-correlated ``NOT EXISTS`` predicate, not a
naive set-difference — the naive form reads a re-parked lease as still resumed. Also
pins the three skip sites (REAP, ``mark_resume_intents``, ``mark_crash_resume_intents``)
that derive from ``parked_lease_ids()``'s union."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD, NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import Reap, ResumeIntents
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, make_context, make_store

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_HANDLE_PID = 100
_HANDLE_START = "start-100"
_HANDLE = WorkerHandle(session_id="sess-a", pid=_HANDLE_PID, process_start_time=_HANDLE_START)


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_spawned_lease(store):  # type: ignore[no-untyped-def]
    """An in-flight build lease spawned into env e1 — session-bearing, pid recorded."""
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_1", pid=_HANDLE_PID, process_start_time=_HANDLE_START, session_id="sess-a", spawned_at=_NOW
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _ctx(store, *, probe=None, clock=None):  # type: ignore[no-untyped-def]
    return make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=probe or FakeProbe(),
        clock=clock or FixedClock(_NOW),
    )


def test_repark_after_resume_on_the_same_lease_reads_as_parked(tmp_path):  # type: ignore[no-untyped-def]
    """Pause -> resume -> pause again, all on ``lease_1``: the second pause must be
    open, not read as still-resumed by a naive set-difference predicate."""
    store = _store(tmp_path)
    t0 = _NOW
    t1 = _NOW + timedelta(minutes=1)
    t2 = _NOW + timedelta(minutes=2)

    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=t0)
    store.record_pause_park_resume(lease_id="lease_1", resumed_at=t1)
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=t2)

    assert store.pause_parked_lease_ids() == {"lease_1"}


def test_same_instant_resume_wins_over_its_pause(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW)
    store.record_pause_park_resume(lease_id="lease_1", resumed_at=_NOW)

    assert store.pause_parked_lease_ids() == set()


def test_a_resume_closes_only_its_own_leases_pause_park(tmp_path):  # type: ignore[no-untyped-def]
    """The ``lease_id`` correlation in ``OPEN_PAUSE_PARK`` is load-bearing — without
    it, resuming one chunk would silently un-pause every paused chunk on the runner."""
    store = _store(tmp_path)
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW)
    store.record_pause_park(lease_id="lease_2", chunk_id="ch_2", parked_at=_NOW)

    store.record_pause_park_resume(lease_id="lease_1", resumed_at=_NOW + timedelta(minutes=1))

    assert store.pause_parked_lease_ids() == {"lease_2"}


def test_pause_park_resumed_lease_is_not_parked(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    later = _NOW + timedelta(minutes=5)
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW)
    store.record_pause_park_resume(lease_id="lease_1", resumed_at=later)

    assert store.pause_parked_lease_ids() == set()


def test_parked_lease_ids_is_the_union_of_ask_and_pause_parks(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)

    # An ask-park alone (lease_2), a pause-park alone (lease_3), and both on one
    # lease (lease_4) — parked_lease_ids() must read every one of them as parked.
    store.record_park(lease_id="lease_2", chunk_id="ch_2", question_id="qn_1", parked_at=_NOW)
    store.record_pause_park(lease_id="lease_3", chunk_id="ch_3", parked_at=_NOW)
    store.record_park(lease_id="lease_4", chunk_id="ch_4", question_id="qn_2", parked_at=_NOW)
    store.record_pause_park(lease_id="lease_4", chunk_id="ch_4", parked_at=_NOW)

    assert store.ask_parked_lease_ids() == {"lease_2", "lease_4"}
    assert store.pause_parked_lease_ids() == {"lease_3", "lease_4"}
    assert store.parked_lease_ids() == {"lease_2", "lease_3", "lease_4"}


# --- Zero-diff inheritance: parked_lease_ids()'s union covers pause-parks too ---


def test_reap_skips_a_pause_parked_lease_though_pid_reads_alive_and_stale(tmp_path):  # type: ignore[no-untyped-def]
    """REAP's skip inherits pause-parks via the union — without it, REAP would kill
    the worker and burn a retry on a chunk the operator merely paused."""
    store = _store(tmp_path)
    _seed_spawned_lease(store)
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW)

    later = _NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(hours=1)
    probe = FakeProbe(alive={(_HANDLE_PID, _HANDLE_START)})
    ctx = _ctx(store, probe=probe, clock=FixedClock(later))

    Reap(ctx).run()

    assert store.active_lease("lease_1") is not None  # claim kept — not closed
    assert probe.killed == []  # worker not killed
    assert [f for f in store.pending_outbound() if f.kind == "escalation.recorded"] == []


def test_mark_resume_intents_skips_a_pause_parked_lease(tmp_path):  # type: ignore[no-untyped-def]
    """The graceful-restart marker inherits the skip — a pause-parked lease has no
    live worker to resume, so a graceful shutdown must not mark it."""
    store = _store(tmp_path)
    _seed_spawned_lease(store)
    assert ResumeIntents(store).mark_graceful(now=_NOW) == 1  # unparked: marked
    store.record_resume_clear(lease_id="lease_1", cleared_at=_NOW + timedelta(seconds=1))

    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW + timedelta(seconds=2))

    assert ResumeIntents(store).mark_graceful(now=_NOW + timedelta(seconds=3)) == 0


def test_mark_crash_resume_intents_skips_a_pause_parked_lease(tmp_path):  # type: ignore[no-untyped-def]
    """The crash-recovery marker inherits the skip — after a ``kill -9`` the startup
    scan must not re-detect a pause-parked lease as a crash to resume."""
    store = _store(tmp_path)
    _seed_spawned_lease(store)
    store.record_daemon_liveness(runner_id="r1", alive_at=_NOW)
    probe = FakeProbe()  # the worker's process is gone — a crash to resume
    later = _NOW + timedelta(seconds=1)

    assert ResumeIntents(store).mark_crashed(process=probe, now=later) == 1  # unparked: marked
    store.record_resume_clear(lease_id="lease_1", cleared_at=later)

    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=later)

    assert ResumeIntents(store).mark_crashed(process=probe, now=_NOW + timedelta(seconds=2)) == 0
