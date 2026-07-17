"""The runner loop honors a hub-side chunk pause (issue #46) — loop component tier.

An operator pauses a chunk at the hub; the runner holding it must **keep the claim** —
kill the worker, park the lease, hold its environments — and resume the *same* session
when the pause clears. That is the whole promise: a pause is not a detach.

Every test here drives the **full composed tick** (REAP → RESUME → PULL → FILL →
ADVANCE) rather than a hand-picked step, because this subsystem's bugs are
step-ordering bugs: the headline one (:func:`_resume_marked_lease` abandoning a paused
chunk, plan §0.2 B) is invisible to any test that drives PULL alone, since RESUME runs
*first* and gives the claim away before PULL's pause-park can ever see it. Issue #45's
bug shipped past three mutation-testing verify-finales for exactly this reason — every
test drove a single step in isolation with an unrealistic worker-alive shape.

:class:`FakeProbe` is that unreality trap: nothing makes a spawned pid alive
automatically, so every test below seeds ``alive`` **deliberately** for the shape it
reasons about.

Driven against a real (tmp sqlite) runner store with the fakes standing in only at the
seams — the harness matrix's component definition.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.work import DEFAULT_MODEL, ChunkStatus
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import advance, fill, mark_crash_resume_intents
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
    """A chunk the operator paused, still routed to us.

    ``status`` is overridable because the runner must key on the **pause fact**, not the
    derived status: PAUSED sits below the human-gated statuses in the derivation order,
    so a paused chunk that is also parked on a question derives ``waiting_on_human``
    while still carrying ``pause`` (P2's
    ``test_pause_view_is_carried_even_when_the_status_hides_the_pause`` fences it).
    """
    return ChunkDetail(
        chunk_id=chunk,
        graph_id="gr_1",
        status=status,
        current_node_id="nd_build",
        latest_epoch=1,
        model=DEFAULT_MODEL,
        route=RouteView(runner_id=runner_id, workspace_id="ws1", environment_ids=["e1"]),
        pause=PauseView(by="operator", set_at="2026-07-16T12:00:00Z"),
    )


def _running_chunk(chunk="ch_1", *, runner_id="r1"):  # type: ignore[no-untyped-def]
    """The same chunk unpaused — ``pause`` is None once the newest fact is a resume."""
    return ChunkDetail(
        chunk_id=chunk,
        graph_id="gr_1",
        status=ChunkStatus.RUNNING,
        current_node_id="nd_build",
        latest_epoch=1,
        model=DEFAULT_MODEL,
        route=RouteView(runner_id=runner_id, workspace_id="ws1", environment_ids=["e1"]),
    )


def _closure_reasons(store, lease_id="lease_1"):  # type: ignore[no-untyped-def]
    return [c.reason for c in store.list_closed_leases(10) if c.lease.lease_id == lease_id]


def _make_ctx(store, hub, harness, probe, **kw):  # type: ignore[no-untyped-def]
    return make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe, **kw)


def _pause_locally(store, ctx, *, paused: bool):  # type: ignore[no-untyped-def]
    """Set the runner's own brake, the way `PATCH /runner` does — fact + report, one write.

    The twin of `test_runner_paused.py`'s helper; kept local so this file stays readable as the
    pause subsystem's own story rather than importing across test modules.
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
# --------------------------------------------------------------------------- #


def test_restart_into_a_standing_pause_keeps_the_claim(tmp_path):  # type: ignore[no-untyped-def]
    """A runner restarted while one of its chunks is paused must **park** it, not abandon it.

    The plan's center of gravity (§0.2 B). Before the fix, ``_resume_marked_lease``
    branched on ``detail.status == ChunkStatus.RUNNING and ours`` — and a paused chunk
    derives ``PAUSED``, not ``RUNNING``, so a chunk *still routed to this runner* fell
    through to ``_abandon_reassigned``: kill + release **every** environment + close the
    lease ``released``. The claim, the route and the environments were all given up —
    pause silently degraded into detach, on every restart. RESUME runs before PULL, so
    PULL's pause-park never got a chance to see it.

    Driven as a full tick because that hand-off is the bug: RESUME alone and PULL alone
    were each defensible.
    """
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe()  # the worker died with the daemon — a real restart's shape
    # Startup crash-recovery marks the killed-mid-work lease (the ungraceful path, #13).
    assert mark_crash_resume_intents(store, process=probe, now=_NOW + timedelta(seconds=1)) == 1

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
    """Detach wins over pause — the ``ours`` conjunct on both pause branches.

    The keystone's fix must not overshoot. A chunk that was detached *and* paused is not ours
    to hold: the route is gone, and no amount of pausing makes it ours again. Parking it would
    keep environments bound to work another runner is free to claim. Both the RESUME branch
    (``ours and detail.pause is not None``) and PULL's sweep (detach checked first) exist to get
    this case right, so it is driven through RESUME — the path the keystone changed.
    """
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe()
    assert mark_crash_resume_intents(store, process=probe, now=_NOW + timedelta(seconds=1)) == 1

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
# --------------------------------------------------------------------------- #


def test_pull_kills_the_worker_and_parks_the_lease_keeping_everything_else(tmp_path):  # type: ignore[no-untyped-def]
    """A pause discovered on a live tick kills the worker and parks — the inverse of an abandon.

    ``_kill_and_park_paused`` is defined by what it does *not* do (plan §3.1): no
    ``_release_all``, no ``record_closure``, no epoch bump, no lease mint, no requeue. Each
    omission is asserted, because each is what separates "keep the claim" from a detach, and
    seams 11/16 rest silently on the closure one.
    """
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
    """The park is idempotent: a standing pause does not append a park row every tick.

    Without the ``pause_parked_lease_ids()`` guard, each tick of an unchanged pause would
    append another park — unbounded growth, and an ``open_pause_park`` whose answer depends on
    which duplicate it read. ``runner:one-open-pause-park-per-lease`` is the store-level fence;
    this is the loop-level one.
    """
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
# --------------------------------------------------------------------------- #


def test_reap_never_reaps_a_pause_parked_lease_however_long_it_stands(tmp_path):  # type: ignore[no-untyped-def]
    """A chunk may sit paused for hours and cost nothing: the reap clock is stopped.

    A pause is open-ended — an operator may leave a chunk paused overnight — so "nothing bad
    accumulates while parked" has to hold across many ticks, not just the one that parked it.
    Two mechanisms share the load once the worker is killed and the lease is parked, and this
    drives both by composing the whole tick:

    * REAP's skip (inherited for free through ``parked_lease_ids()``'s union) keeps the reap
      clock stopped, so no amount of elapsed time reaps the lease for inactivity;
    * ADVANCE's pause-park routing keeps the now-dead pid from being read as a done declaration
      (D-055) and judged into a verdict-less failure — which is what would actually burn the
      retries here, since a killed worker is not a finished one.
    """
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
# --------------------------------------------------------------------------- #


def test_a_pause_landing_between_two_ticks_is_reconciled_on_the_next_one(tmp_path):  # type: ignore[no-untyped-def]
    """The operator pauses while the runner is up and mid-flight: the next tick reconciles it.

    The ordinary path — no restart involved. The first tick sees an unpaused, live chunk and
    leaves it strictly alone (proving the pause branch does not fire on chunks that are not
    paused); the pause lands between the two; the second tick discovers it on PULL's sweep.
    """
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
# --------------------------------------------------------------------------- #


def test_resuming_the_chunk_restarts_the_same_session_under_the_same_lease(tmp_path):  # type: ignore[no-untyped-def]
    """Unpausing resumes the *same* session — the point of keeping the claim (D-082).

    The pause cost the chunk a process, not an attempt: same lease, same epoch, same session,
    only the pid rewritten, and the environment it resumes into is the one held throughout.
    """
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
# --------------------------------------------------------------------------- #


def test_an_ask_parked_and_paused_lease_does_not_resume_on_the_answer(tmp_path):  # type: ignore[no-untyped-def]
    """Pause dominates the ask: answering a paused chunk's question does **not** restart it.

    The overlap is real — pause is deliberately not refused on ``waiting_on_human`` — and it is
    where a status-keyed runner would fail silently: a chunk both paused and asked derives
    ``waiting_on_human``, *not* ``paused``, so the loop must key on the pause **fact**. Here the
    hub reports exactly that lossy status while carrying ``pause``.

    Two things must hold. While paused, an answer sits unclaimed. When the pause lifts, the
    answer — not the unpause — is what resumes the session, on the following tick.
    """
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
    """The local brake gates ``_resume_if_unpaused`` **above its fact writes**, not merely above its spawn.

    ``_spawn_suppressed``'s stated contract is that a suppressed spawn "writes no fact ... the
    lease is left exactly as it was — active, unmodified". For this primitive that is a claim
    about *where* the gate sits: at the very top of the function, above the hub poll, above the
    ask-park early return, and above ``record_pause_park_resume``.

    ``test_a_chunk_paused_on_a_locally_paused_runner_resumes_for_neither_brake_alone`` (row 14)
    asserts the park survives a suppressed resume, but it cannot fence the gate's *position*: on
    the ordinary path every fact write already sits below ``resume_with_message``, so a gate
    lowered to just above the spawn would satisfy it identically. The **ask-park overlap** is the
    one shape that separates them — its early return writes ``record_pause_park_resume`` and then
    returns *without* ever reaching the spawn, so a lowered gate would clear the pause-park of a
    locally-paused runner. This test is that discriminator, and it is why the two rows are
    written apart rather than folded together.
    """
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

    # The gate fired first, so the step wrote nothing at all: the pause-park is untouched and the
    # lease is left exactly as it was. A gate sitting any lower would have taken the ask-park
    # early return and cleared this park while the machine is declining to work.
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


# --------------------------------------------------------------------------- #
# Row 16 — seams 11/16 both rest on the lease staying ACTIVE. Tested separately.
# --------------------------------------------------------------------------- #


def test_fill_does_not_reconcile_a_pause_parked_chunk_as_an_interrupted_claim(tmp_path):  # type: ignore[no-untyped-def]
    """FILL's ``_reconcile_interrupted_claims`` skips a pause-parked chunk (seam 11).

    It skips chunks that have an active lease — which a pause-parked chunk does, precisely
    because ``_kill_and_park_paused`` records no closure. Were that omission ever "tidied up",
    this chunk would look exactly like an interrupted claim (a held binding, no active lease)
    and FILL would **spawn a worker into it while it is paused**.

    Asserted independently of its ADVANCE twin below: two seams resting on one property must be
    proven one at a time, or each can mask the other's regression.
    """
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe(alive={(100, "start-100")})
    hub = FakeHub()
    hub.chunks["ch_1"] = _paused_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _make_ctx(store, hub, harness, probe)

    tick(ctx)  # kills + parks
    fill(ctx)  # the seam under test, driven again on the parked shape

    # The load-bearing fact: the lease is still ACTIVE, so the reconciler skips the chunk.
    assert store.active_lease_for_chunk("ch_1") is not None
    assert harness.spawns == []  # nothing adopted, nothing re-spawned into the paused chunk
    assert hub.claims == []
    assert store.latest_epoch("ch_1") == 1  # no epoch bumped out from under the pause


def test_advance_does_not_drive_a_pause_parked_chunk_as_a_held_chunk(tmp_path):  # type: ignore[no-untyped-def]
    """ADVANCE's ``_advance_held_chunk`` skips a pause-parked chunk (seam 16).

    The same load-bearing omission from the other side: ``_advance_held_chunk`` is for chunks
    the runner holds with **no** active lease (a hub node, a resolved gate). A pause-parked
    chunk has one, so it is never routed there — and its exited worker is never read as a done
    declaration either (D-055): the worker was killed, it did not finish.
    """
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe(alive={(100, "start-100")})
    hub = FakeHub()
    hub.chunks["ch_1"] = _paused_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _make_ctx(store, hub, harness, probe)

    tick(ctx)  # kills + parks
    advance(ctx)  # the seam under test, driven again on the parked shape

    assert store.active_lease_for_chunk("ch_1") is not None
    # No verdict elicited from the killed worker, and nothing buffered on its behalf.
    assert harness.judged == []
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    assert hub.completions == []
    assert store.attempt_count("ch_1", "nd_build") == 1


# --------------------------------------------------------------------------- #
# Row 17 — pausing a chunk this runner never held.
# --------------------------------------------------------------------------- #


def test_pausing_an_unheld_ready_chunk_simply_keeps_it_out_of_the_queue(tmp_path):  # type: ignore[no-untyped-def]
    """A paused chunk nobody holds is a pure hub-side affair: the runner does nothing at all.

    There is no lease to park and no worker to kill — the chunk just never appears in
    ``list_ready()``, so FILL is never offered it (the hub's "free win", pinned at its own tier
    in ``test_queue_shaping.py``). This is the runner-side half: given an empty queue, a paused
    chunk produces no park, no claim, and no error path.
    """
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
