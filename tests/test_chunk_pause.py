"""The runner loop honors a hub-side chunk pause (issue #46) — loop component tier.

A pause must keep the claim — kill the worker, park the lease, hold environments — and
resume the same session when it clears; a pause is not a detach. Every test drives the
full composed tick since this subsystem's bugs are step-ordering bugs.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import Advance, Fill, ResumeIntents
from blizzard.runner.loop.tick import tick
from blizzard.runner.store import schema as runner_schema
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, PauseView, RouteView
from blizzard.wire.facts import ESCALATION_RECORDED, RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED
from blizzard.wire.question import QuestionView
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_envelope,
    make_store,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_running_lease(store, *, chunk="ch_1", lease="lease_1", pid=100, start="start-100"):  # type: ignore[no-untyped-def]
    """A build lease spawned into env e1, plus its binding. The probe decides liveness."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(lease, pid=pid, process_start_time=start, session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _paused_chunk(chunk="ch_1", *, runner_id="r1", status=ChunkStatus.PAUSED):  # type: ignore[no-untyped-def]
    """A chunk the operator paused, still routed to us. ``status`` is overridable: the
    runner must key on the pause fact, not the derived status, since a paused+asked chunk
    derives ``waiting_on_human`` while still carrying ``pause``.
    """
    return ChunkDetail(
        chunk_id=chunk,
        graph_id="gr_1",
        status=status,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id=runner_id, workspace_id="ws1", environment_ids=["e1"]),
        pause=PauseView(by="operator", set_at="2026-07-16T12:00:00Z"),
    )


def _running_chunk(chunk="ch_1", *, runner_id="r1"):  # type: ignore[no-untyped-def]
    """The same chunk unpaused — no ``pause`` view."""
    return ChunkDetail(
        chunk_id=chunk,
        graph_id="gr_1",
        status=ChunkStatus.RUNNING,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id=runner_id, workspace_id="ws1", environment_ids=["e1"]),
    )


def _closure_reasons(store, lease_id="lease_1"):  # type: ignore[no-untyped-def]
    return [c.reason for c in store.list_closed_leases(10) if c.lease.lease_id == lease_id]


def _make_ctx(store, hub, harness, probe, **kw):  # type: ignore[no-untyped-def]
    return make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe, **kw)


def _pause_locally(store, ctx, *, paused: bool):  # type: ignore[no-untyped-def]
    """Set the runner's own brake, the way `PATCH /runner` does — fact + report, one write.

    The twin of `test_runner_paused.py`'s helper, kept local rather than imported across
    test modules.
    """
    store.record_local_pause(
        "r1",
        paused=paused,
        at=ctx.clock.now(),
        by="operator",
        report_kind=RUNNER_LOCALLY_PAUSED if paused else RUNNER_LOCALLY_RESUMED,
        report_payload=json.dumps({"runner_id": "r1", "by": "operator"}),
    )


# --------------------------------------------------------------------------- #
# Row 11 — THE KEYSTONE: a restart into a standing pause keeps the claim.


def test_restart_into_a_standing_pause_keeps_the_claim(tmp_path):  # type: ignore[no-untyped-def]
    """A runner restarted while one of its chunks is paused must park it, not abandon it
    (plan §0.2 B) — a status-keyed RESUME branch would drop a still-routed chunk through
    to ``_abandon_reassigned`` instead."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe()  # the worker died with the daemon — a real restart's shape
    # Startup crash-recovery marks the killed-mid-work lease (the ungraceful path, #13).
    assert ResumeIntents(store).mark_crashed(process=probe, now=_NOW + timedelta(seconds=1)) == 1

    hub = FakeHub()
    hub.chunks["ch_1"] = _paused_chunk()  # paused while the runner was down; route still ours
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness,
        probe=probe,
        clock=FixedClock(_NOW + timedelta(seconds=1)),
    )

    tick(ctx)

    # The claim survives: the lease is still active, still ours, still session-bearing.
    lease = store.active_lease("lease_1")
    assert lease is not None, "the paused lease was closed — pause degraded into detach"
    assert lease.session_id == "sess-a" and lease.epoch == 1
    # No closure of any kind, and emphatically not `released` (the abandon's signature).
    assert _closure_reasons(store) == []
    # The environments are HELD — the whole point of keeping the claim (plan §3.1).
    assert store.held_environment_ids() == ["e1"]
    assert provider.released == []
    # It is parked on the pause, so REAP's clock is stopped and ADVANCE routes it here.
    assert store.pause_parked_lease_ids() == {"lease_1"}
    # No retry consumed, no epoch bump, no fresh lease minted.
    assert store.attempt_count("ch_1", "nd_build") == 1
    assert store.latest_epoch("ch_1") == 1
    # Nothing was resumed or spawned: the chunk is paused.
    assert harness.resumed == [] and harness.spawns == [] and harness.judged == []


def test_a_chunk_detached_and_then_paused_is_still_abandoned(tmp_path):  # type: ignore[no-untyped-def]
    """Detach wins over pause — the ``ours`` conjunct on both pause branches. A chunk
    detached and paused is not ours to hold: the route is gone, so it is abandoned, not
    parked."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe()
    assert ResumeIntents(store).mark_crashed(process=probe, now=_NOW + timedelta(seconds=1)) == 1

    hub = FakeHub()
    detached = _paused_chunk()
    detached.route = None  # detached at the hub, and paused too
    hub.chunks["ch_1"] = detached
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness,
        probe=probe,
        clock=FixedClock(_NOW + timedelta(seconds=1)),
    )

    tick(ctx)

    assert store.active_lease("lease_1") is None  # closed
    assert _closure_reasons(store) == ["released"]  # abandoned, not parked
    assert store.pause_parked_lease_ids() == set()  # emphatically NOT parked on the pause
    assert provider.released == ["e1"]  # the environments go back to the pool
    assert store.held_environment_ids() == []


# --------------------------------------------------------------------------- #
# Row 9 — PULL kills and parks, and gives up nothing else.


def test_pull_kills_the_worker_and_parks_the_lease_keeping_everything_else(tmp_path):  # type: ignore[no-untyped-def]
    """A pause discovered on a live tick kills the worker and parks — the inverse of an
    abandon. Each omission (release, closure, epoch bump, requeue) is asserted separately
    (plan §3.1)."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe(alive={(100, "start-100")})  # a LIVE worker — the pause has to kill it
    hub = FakeHub()
    hub.chunks["ch_1"] = _paused_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=probe)

    tick(ctx)

    assert probe.killed == [100]  # the worker is stopped — that is what a pause means
    assert store.pause_parked_lease_ids() == {"lease_1"}
    # Everything else survives: claim, route, epoch, session, environments, retry budget.
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.epoch == 1 and lease.session_id == "sess-a"
    assert _closure_reasons(store) == []
    assert store.held_environment_ids() == ["e1"]
    assert provider.released == []
    assert store.latest_epoch("ch_1") == 1
    assert store.attempt_count("ch_1", "nd_build") == 1
    assert harness.spawns == [] and harness.judged == []


def test_pull_parks_a_standing_pause_only_once_across_many_ticks(tmp_path):  # type: ignore[no-untyped-def]
    """The park is idempotent: a standing pause does not append a park row every tick —
    the loop-level twin of the store-level ``runner:one-open-pause-park-per-lease`` fence."""
    db_url = f"sqlite:///{tmp_path / 'runner.db'}"
    store = make_store(db_url)
    _seed_running_lease(store)
    probe = FakeProbe(alive={(100, "start-100")})
    hub = FakeHub()
    hub.chunks["ch_1"] = _paused_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    ctx = _make_ctx(store, hub, FakeHarness(handle=_HANDLE, verdict="pass"), probe)

    for _ in range(3):
        tick(ctx)

    assert store.pause_parked_lease_ids() == {"lease_1"}
    # The kill happened once, on the tick that discovered the pause; the later ticks saw an
    # already-parked lease and did nothing at all.
    assert probe.killed == [100]
    # Counted over the real rows, because the accessor above is a set: it cannot see a
    # duplicate, which is exactly what an unguarded park would produce.
    engine = create_engine_from_url(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(select(runner_schema.pause_parks.c.chunk_id)).all()
    finally:
        engine.dispose()
    assert rows == [("ch_1",)], f"a standing pause appended {len(rows)} park rows — the idempotency guard is gone"


# --------------------------------------------------------------------------- #
# Row 10 — REAP never reaps a pause-park, however long it stands.


def test_reap_never_reaps_a_pause_parked_lease_however_long_it_stands(tmp_path):  # type: ignore[no-untyped-def]
    """A chunk may sit paused for hours and cost nothing: the reap clock is stopped, and
    ADVANCE's pause-park routing keeps the dead pid from being read as a done declaration
    (D-055)."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe(alive={(100, "start-100")})
    hub = FakeHub()
    hub.chunks["ch_1"] = _paused_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    clock = FixedClock(_NOW)
    ctx = _make_ctx(store, hub, harness, probe, clock=clock)

    tick(ctx)  # discovers the pause: kills + parks
    assert store.pause_parked_lease_ids() == {"lease_1"}

    # Tick well past the staleness threshold, then keep going: the lease's last heartbeat
    # recedes further with every tick, which is exactly what REAP's stall signal keys on.
    clock.advance(HEARTBEAT_STALENESS_THRESHOLD + timedelta(minutes=1))
    tick(ctx)
    for _ in range(2):
        clock.advance(timedelta(hours=1))
        tick(ctx)

    assert store.active_lease("lease_1") is not None  # never reaped
    assert _closure_reasons(store) == []  # no `reaped` closure, no escalation, no failure
    assert store.attempt_count("ch_1", "nd_build") == 1  # no retry consumed
    assert [f for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED] == []
    assert harness.judged == []  # the killed worker was never mistaken for a finished one
    assert store.held_environment_ids() == ["e1"]
    assert store.pause_parked_lease_ids() == {"lease_1"}  # still parked, still waiting


# --------------------------------------------------------------------------- #
# Row 12 — a pause landing between two ticks: marked, unreconciled.


def test_a_pause_landing_between_two_ticks_is_reconciled_on_the_next_one(tmp_path):  # type: ignore[no-untyped-def]
    """The operator pauses while the runner is up and mid-flight: the first tick leaves an
    unpaused, live chunk alone; the pause lands between ticks; the second tick discovers
    it on PULL's sweep."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe(alive={(100, "start-100")})
    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()  # not paused yet
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _make_ctx(store, hub, harness, probe)

    tick(ctx)

    # Untouched: a live worker on an unpaused chunk is simply left running.
    assert probe.killed == [] and store.pause_parked_lease_ids() == set()
    assert store.active_lease("lease_1") is not None

    hub.chunks["ch_1"] = _paused_chunk()  # the operator pauses it between the two ticks
    tick(ctx)

    assert probe.killed == [100]
    assert store.pause_parked_lease_ids() == {"lease_1"}
    assert _closure_reasons(store) == []
    assert store.held_environment_ids() == ["e1"]


# --------------------------------------------------------------------------- #
# Row 13 — resume-in-place: same lease/epoch/session, new pid, no retry.


def test_resuming_the_chunk_restarts_the_same_session_under_the_same_lease(tmp_path):  # type: ignore[no-untyped-def]
    """Unpausing resumes the same session — the point of keeping the claim (D-082): same
    lease, epoch, and session, only the pid rewritten."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe(alive={(100, "start-100")})
    hub = FakeHub()
    hub.chunks["ch_1"] = _paused_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _make_ctx(store, hub, harness, probe)

    tick(ctx)
    assert store.pause_parked_lease_ids() == {"lease_1"}

    # Still paused: a tick against a standing pause resumes nothing.
    tick(ctx)
    assert harness.resumed == []

    hub.chunks["ch_1"] = _running_chunk()  # the operator resumes it
    tick(ctx)

    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The operator resumed this chunk; continue your task where you left off.")
    ]
    assert store.pause_parked_lease_ids() == set()  # the park is closed
    lease = store.active_lease("lease_1")
    assert lease is not None
    assert (lease.lease_id, lease.epoch, lease.session_id) == ("lease_1", 1, "sess-a")
    assert lease.pid == 4321  # only the pid was rewritten
    assert store.attempt_count("ch_1", "nd_build") == 1  # no retry consumed
    assert store.latest_epoch("ch_1") == 1
    assert _closure_reasons(store) == []


# --------------------------------------------------------------------------- #
# Row 15 — the overlap: an answer does not un-pause a chunk.


def test_an_ask_parked_and_paused_lease_does_not_resume_on_the_answer(tmp_path):  # type: ignore[no-untyped-def]
    """Pause dominates the ask: answering a paused chunk's question does not restart it.
    While paused, the answer sits unclaimed; when the pause lifts, the answer — not the
    unpause — resumes the session, on the following tick."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe()  # the asking worker exited (ask-and-exit)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="q_1", parked_at=_NOW)
    hub = FakeHub()
    # Paused AND ask-parked: the status hides the pause, the `pause` field is the only witness.
    hub.chunks["ch_1"] = _paused_chunk(status=ChunkStatus.WAITING_ON_HUMAN)
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.questions["q_1"] = QuestionView(
        question_id="q_1",
        chunk_id="ch_1",
        runner_id="r1",
        epoch=1,
        question="which way?",
        options=[],
        asked_at="2026-07-16T12:00:00Z",
        answered=True,
        answer="go left",
        answered_by="operator",
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _make_ctx(store, hub, harness, probe)

    tick(ctx)

    # The answer is sitting right there, and the chunk stays dormant: it is paused.
    assert harness.resumed == []
    assert store.pause_parked_lease_ids() == {"lease_1"}
    assert store.ask_parked_lease_ids() == {"lease_1"}  # the ask-park survives underneath
    assert _closure_reasons(store) == []

    hub.chunks["ch_1"] = _running_chunk()  # the operator resumes the chunk
    tick(ctx)

    # The unpause clears the pause-park but does NOT itself resume: the chunk is still dormant
    # on its question, and an answer is what restarts it.
    assert harness.resumed == []
    assert store.pause_parked_lease_ids() == set()
    assert store.ask_parked_lease_ids() == {"lease_1"}

    tick(ctx)

    # Now the ordinary answer-resume delivers it — with the answer, not the pause message.
    assert harness.resumed == [("/ws/e1", "sess-a", "# Answer from operator. Continue.\ngo left")]
    assert store.ask_parked_lease_ids() == set()
    assert store.attempt_count("ch_1", "nd_build") == 1


def test_a_suppressed_pause_resume_writes_no_fact_even_on_the_ask_park_path(tmp_path):  # type: ignore[no-untyped-def]
    """The local brake gates ``_resume_if_unpaused`` above its fact writes, not merely
    above its spawn: the ask-park's early return writes a fact before reaching the spawn,
    so a lower gate would clear the pause-park while declining to work."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe()  # the asking worker exited (ask-and-exit)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="q_1", parked_at=_NOW)
    hub = FakeHub()
    hub.paused = False  # the hub's *runner* brake (D-043) is off — not the lever under test
    hub.chunks["ch_1"] = _paused_chunk(status=ChunkStatus.WAITING_ON_HUMAN)  # paused AND asked
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.questions["q_1"] = QuestionView(
        question_id="q_1",
        chunk_id="ch_1",
        runner_id="r1",
        epoch=1,
        question="which way?",
        options=[],
        asked_at="2026-07-16T12:00:00Z",
        answered=True,
        answer="go left",
        answered_by="operator",
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _make_ctx(store, hub, harness, probe)
    _pause_locally(store, ctx, paused=True)

    tick(ctx)  # PULL parks the pause regardless of the local brake — a kill is not a spawn
    assert store.pause_parked_lease_ids() == {"lease_1"}
    assert store.ask_parked_lease_ids() == {"lease_1"}

    hub.chunks["ch_1"] = _running_chunk()  # the operator resumes the CHUNK; the brake stays on
    tick(ctx)

    # The gate fired first, so the step wrote nothing: a gate sitting any lower would have
    # taken the ask-park early return and cleared this park instead.
    assert store.pause_parked_lease_ids() == {"lease_1"}, (
        "a locally-paused runner cleared a pause-park — the brake gate sits below a fact write"
    )
    assert store.ask_parked_lease_ids() == {"lease_1"}
    assert harness.resumed == []

    _pause_locally(store, ctx, paused=False)  # the brake clears
    tick(ctx)

    # Only now does the pause-park clear — and the unpause still does not itself resume: the
    # answer does, on the following tick (pause dominates the ask, as ever).
    assert store.pause_parked_lease_ids() == set()
    assert harness.resumed == []

    tick(ctx)

    assert harness.resumed == [("/ws/e1", "sess-a", "# Answer from operator. Continue.\ngo left")]
    assert store.attempt_count("ch_1", "nd_build") == 1
    # The resume re-supplied the per-lease identity a resumed worker needs (`--resume`
    # inherits none of the spawn env) and re-minted the capability token.
    preamble, chunk_id = harness.resumed_identity[-1]
    assert preamble is not None and preamble.lease_id == "lease_1" and preamble.lease_token
    assert chunk_id == "ch_1"
    assert store.lease_token_hash("lease_1") is not None  # re-minted on resume


# --------------------------------------------------------------------------- #
# Row 16 — seams 11/16 both rest on the lease staying ACTIVE. Tested separately.


def test_fill_does_not_reconcile_a_pause_parked_chunk_as_an_interrupted_claim(tmp_path):  # type: ignore[no-untyped-def]
    """FILL's ``_reconcile_interrupted_claims`` skips a pause-parked chunk (seam 11): the
    active lease ``_kill_and_park_paused`` leaves behind is what keeps FILL from spawning
    a worker into it while paused."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe(alive={(100, "start-100")})
    hub = FakeHub()
    hub.chunks["ch_1"] = _paused_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _make_ctx(store, hub, harness, probe)

    tick(ctx)  # kills + parks
    Fill(ctx).run()  # the seam under test, driven again on the parked shape

    # The load-bearing fact: the lease is still ACTIVE, so the reconciler skips the chunk.
    assert store.active_lease_for_chunk("ch_1") is not None
    assert harness.spawns == []  # nothing adopted, nothing re-spawned into the paused chunk
    assert hub.claims == []
    assert store.latest_epoch("ch_1") == 1  # no epoch bumped out from under the pause


def test_advance_does_not_drive_a_pause_parked_chunk_as_a_held_chunk(tmp_path):  # type: ignore[no-untyped-def]
    """ADVANCE's ``_advance_held_chunk`` skips a pause-parked chunk (seam 16): its active
    lease keeps it unrouted, and its killed worker is never read as a done declaration
    (D-055)."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe(alive={(100, "start-100")})
    hub = FakeHub()
    hub.chunks["ch_1"] = _paused_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _make_ctx(store, hub, harness, probe)

    tick(ctx)  # kills + parks
    Advance(ctx).run()  # the seam under test, driven again on the parked shape

    assert store.active_lease_for_chunk("ch_1") is not None
    # No verdict elicited from the killed worker, and nothing buffered on its behalf.
    assert harness.judged == []
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    assert hub.completions == []
    assert store.attempt_count("ch_1", "nd_build") == 1


# --------------------------------------------------------------------------- #
# Row 17 — pausing a chunk this runner never held.


def test_pausing_an_unheld_ready_chunk_simply_keeps_it_out_of_the_queue(tmp_path):  # type: ignore[no-untyped-def]
    """A paused chunk nobody holds is a pure hub-side affair: given an empty queue, the
    runner produces no park, no claim, and no error path. The hub-side half is pinned in
    ``test_queue_shaping.py``."""
    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = []  # the hub filtered the paused chunk out of the ready queue
    hub.chunks["ch_1"] = _paused_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _make_ctx(store, hub, harness, FakeProbe())

    tick(ctx)

    assert hub.claims == []
    assert harness.spawns == []
    assert store.pause_parked_lease_ids() == set()  # nothing to park — no lease was ever held
    assert store.list_active_leases() == []
    assert store.held_environment_ids() == []
