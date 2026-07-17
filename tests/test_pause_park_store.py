"""The runner-side pause-park store — a separate table pair from park_facts (issue #46).

Pins ``_pause_park_is_open``'s re-pause correctness and the ``parked_lease_ids()``
union (plan §1, ``blizzard-workspace/.winter/workflows/2026-07-16-chunk-pause/00-plan.md``).

A pause has no natural key like an ask's fresh ``question_id`` per ask, so a plain
``lease_id NOT IN (select lease_id from pause_park_resumes)`` set-difference is wrong:
it would read a chunk paused -> resumed -> paused again on the *same* lease as still
resumed, leaving the second pause invisible and its worker running. The real predicate
is timestamp-correlated (``NOT EXISTS``, mirroring ``_intent_is_open``); the first test
below is written to fail against the naive set-difference and pass only against the
real one — confirmed by hand before landing (see the developer's report).

The last section pins plan §1.3's **zero-diff inheritance** claim: because
``parked_lease_ids()`` is the union, the three existing skip sites — REAP's
(``steps.py:227``, ``:230-234``), ``mark_resume_intents`` (``:285``, ``:291``) and
``mark_crash_resume_intents`` (``:342``, ``:353``) — skip a pause-parked lease with
**no loop diff at all**. That claim is what earns P3 its share of plan row 10, so it
is asserted here against the real loop steps rather than left to P4's full-``tick()``
suite (``test_chunk_pause.py``), which does not exist yet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import mark_crash_resume_intents, mark_resume_intents, reap
from blizzard.runner.store.repository import NewLease
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
    """Pause -> resume -> pause again, all on ``lease_1``: the second pause must be open.

    A naive ``lease_id NOT IN (select lease_id from pause_park_resumes)`` predicate
    reads *any* resume as closing *every* pause on the lease, forever — so this test
    fails against that shape and only passes against the timestamp-correlated one.
    """
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
    """The ``lease_id`` correlation in ``_pause_park_is_open`` is load-bearing.

    Without the ``pause_park_resumes.c.lease_id == pause_parks.c.lease_id`` conjunct the
    predicate reads *any* resume stamped at or after a park as closing it — so resuming
    one chunk would silently un-pause **every** paused chunk on the runner, and their
    workers would be resumed against the operator's standing instruction. The timestamp
    half alone cannot fence that: both parks here share one instant.
    """
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


# --------------------------------------------------------------------------- #
# Zero-diff inheritance (plan §1.3) — the union alone makes the existing skip
# sites correct for a pause-park, with no diff to steps.py. P3 lands no loop
# change, so these drive the real loop steps against a pause-parked lease.
# --------------------------------------------------------------------------- #


def test_reap_skips_a_pause_parked_lease_though_pid_reads_alive_and_stale(tmp_path):  # type: ignore[no-untyped-def]
    """REAP's skip (``steps.py:227``, ``:230-234``) inherits pause-parks via the union.

    The mirror of ``test_parked_lease_is_not_reaped_though_pid_reads_alive_and_stale``
    for the pause half: the recorded pid reads **alive** and the heartbeat is far past
    the staleness threshold, so without the skip REAP would kill the worker and burn a
    retry on a chunk the operator merely paused. The reap clock is stopped instead.
    """
    store = _store(tmp_path)
    _seed_spawned_lease(store)
    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW)

    later = _NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(hours=1)
    probe = FakeProbe(alive={(_HANDLE_PID, _HANDLE_START)})
    ctx = _ctx(store, probe=probe, clock=FixedClock(later))

    reap(ctx)

    assert store.active_lease("lease_1") is not None  # claim kept — not closed
    assert probe.killed == []  # worker not killed
    assert [f for f in store.pending_outbound() if f.kind == "escalation.recorded"] == []


def test_mark_resume_intents_skips_a_pause_parked_lease(tmp_path):  # type: ignore[no-untyped-def]
    """The graceful-restart marker (``steps.py:285``, ``:291``) inherits the skip.

    A pause-parked lease has no live worker to resume, so a graceful shutdown must not
    mark it — RESUME would otherwise resume the worker the pause exists to stop.
    """
    store = _store(tmp_path)
    _seed_spawned_lease(store)
    assert mark_resume_intents(store, now=_NOW) == 1  # unparked: marked
    store.record_resume_clear(lease_id="lease_1", cleared_at=_NOW + timedelta(seconds=1))

    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW + timedelta(seconds=2))

    assert mark_resume_intents(store, now=_NOW + timedelta(seconds=3)) == 0


def test_mark_crash_resume_intents_skips_a_pause_parked_lease(tmp_path):  # type: ignore[no-untyped-def]
    """The crash-recovery marker (``steps.py:342``, ``:353``) inherits the skip.

    The counterpart to the graceful marker above: after a ``kill -9`` the startup scan
    must not re-detect a pause-parked lease as a crash to resume. (P4's RESUME branch
    then handles the *unparked* paused lease — the plan's §7 crash point.)
    """
    store = _store(tmp_path)
    _seed_spawned_lease(store)
    store.record_daemon_liveness(runner_id="r1", alive_at=_NOW)
    probe = FakeProbe()  # the worker's process is gone — a crash to resume
    later = _NOW + timedelta(seconds=1)

    assert mark_crash_resume_intents(store, process=probe, now=later) == 1  # unparked: marked
    store.record_resume_clear(lease_id="lease_1", cleared_at=later)

    store.record_pause_park(lease_id="lease_1", chunk_id="ch_1", parked_at=later)

    assert mark_crash_resume_intents(store, process=probe, now=_NOW + timedelta(seconds=2)) == 0
