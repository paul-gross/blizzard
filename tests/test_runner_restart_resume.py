"""Runner restart-resume — the graceful-restart re-attach (issue #12, unit tier).

A graceful shutdown marks every in-flight lease for restart-resume, and the startup
RESUME step re-attaches each marked session in place — or abandons a chunk the hub
reassigned/detached while the runner was down. Drives the marking hook and RESUME
directly against a real tmp store with fakes at the seams."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.loop.steps import Resume, ResumeIntents
from blizzard.runner.loop.tick import tick
from blizzard.wire.chunk import ChunkStatusView
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
    make_stores,
)

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)


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
    store.record_spawn(
        lease,
        pid=pid,
        process_start_time=start,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, session),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _running_chunk(chunk="ch_1", *, runner_id="r1"):  # type: ignore[no-untyped-def]
    return ChunkStatusView(
        chunk_id=chunk,
        status=ChunkStatus.RUNNING,
        latest_epoch=1,
        route_runner_id=runner_id,
    )


# --- Marking — the graceful-shutdown hook ---


@pytest.mark.unit
def test_marks_active_session_bearing_lease(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)

    marked = ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

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
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
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

    marked = ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    assert marked == 0
    assert store.resume_intent_lease_ids() == set()


@pytest.mark.unit
def test_marking_skips_a_lease_with_an_in_flight_elicitation(tmp_path):  # type: ignore[no-untyped-def]
    """D6 (blizzard#443 review, F1/F2/F5): a lease whose worker already exited into a
    detached verdict elicitation is neither parked nor pending, but resuming it would wake a
    SECOND process on the same session — the elicitation's own collect pass is what must
    claim it next, not a restart-resume re-attach."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_elicitation_launch("lease_1", 1, output_path="/tmp/lease_1.1.0.elicitation", at=_NOW)

    marked = ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    assert marked == 0
    assert store.resume_intent_lease_ids() == set()


@pytest.mark.unit
def test_remark_across_two_restarts_reopens_the_intent(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    # First restart: mark then clear (RESUME consumed it).
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)
    store.record_resume_clear(lease_id="lease_1", cleared_at=_NOW)
    assert store.resume_intent_lease_ids() == set()
    # Second graceful restart while the same lease is still in flight — re-marked strictly later.
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW + timedelta(minutes=5))
    assert store.resume_intent_lease_ids() == {"lease_1"}


# --- RESUME — resume in place ---


@pytest.mark.unit
def test_resume_in_place_keeps_lease_epoch_session_rewrites_pid(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    probe = FakeProbe(alive={(100, "start-100"), (4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    Resume(ctx).run()

    # Same session resumed in place with the restart message; the survivor pid was killed first.
    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The supervisor restarted; continue your task where you left off.")
    ]
    assert probe.killed == [100]
    # Same lease/epoch/session, only the pid rewritten to the resumed process.
    lease = store.active_lease("lease_1")
    assert lease is not None
    assert (lease.lease_id, lease.epoch, lease.session_id, lease.pid) == ("lease_1", 1, "sess-a", 4321)
    # The resumed process's own group is durable too (D3) — every launch, resume included,
    # gets a fresh session/group leader, so the resumed pid IS its own pgid.
    assert lease.pgid == 4321
    # No retry consumed — no new lease minted, no closure recorded.
    assert store.attempt_count("ch_1", "nd_build") == 1
    # Intent consumed — a second RESUME pass is a no-op.
    assert store.resume_intent_lease_ids() == set()


@pytest.mark.unit
def test_restart_resume_records_the_launchers_own_start_time_without_reprobing(tmp_path):  # type: ignore[no-untyped-def]
    """F4/F14: `_wake` carries the resumed launch's own recorded `process_start_time`
    straight through into `record_spawn` — never re-probing `/proc` a second time, which
    would race a pid-reuse window opening between the launch and this call."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass", resume_process_start_time="start-4321")
    harness.resume_pid = 4321
    probe = FakeProbe(alive={(100, "start-100"), (4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    Resume(ctx).run()

    assert probe.start_time_calls == []  # never re-probed — the launcher's own stamp is used
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.process_start_time == "start-4321"
    assert harness.resume_confirm_durable_calls == 1  # disarmed once the record landed


@pytest.mark.unit
def test_a_record_spawn_write_that_raises_kills_the_still_unconfirmed_resume(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """F4's mirror of F1/F3 for a resume launch: a plain exception writing the durable
    `record_spawn` row must not leave the trampoline parked forever with nothing durable
    for REAP/ADVANCE to re-adopt — `_wake` kills the group itself, never disarming."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    harness.resume_pgid = 4321
    probe = FakeProbe(alive={(100, "start-100"), (4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    def _raise(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("durable write failed")

    monkeypatch.setattr(store, "record_spawn", _raise)

    with pytest.raises(RuntimeError, match="durable write failed"):
        Resume(ctx).run()

    assert probe.killed_groups == [4321]
    assert harness.resume_confirm_durable_calls == 0  # never disarmed — no durable record ever landed


@pytest.mark.unit
def test_restart_resume_skips_the_kill_when_the_recorded_pid_was_reused(tmp_path):  # type: ignore[no-untyped-def]
    """The same pid/pgid-reuse hazard `Attempt._kill_process` guards against: a LIVE pid
    whose start time no longer matches the recorded one is not this lease's survivor any
    more, and a bare, unchecked kill would hit whoever the OS gave that pid to instead."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    # pid 100 is alive, but under a DIFFERENT start time — the OS recycled it.
    probe = FakeProbe(alive={(100, "some-other-processes-start-time"), (4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    Resume(ctx).run()

    assert probe.killed == []
    assert probe.killed_groups == []
    # The resume still proceeds — the skipped kill is best-effort hygiene, not a gate.
    assert harness.resumed != []


@pytest.mark.unit
def test_restart_resume_group_kills_the_survivor_when_a_pgid_is_recorded(tmp_path):  # type: ignore[no-untyped-def]
    """A survivor with a durable recorded pgid (D3) is killed by GROUP, never by bare
    pid — the same preference `Attempt._kill_process` applies, reached here through the
    same shared, liveness-checked helper."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_spawn(  # this generation's own group is durable (D3)
        "lease_1",
        pid=100,
        process_start_time="start-100",
        pgid=100,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    probe = FakeProbe(alive={(100, "start-100"), (4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    Resume(ctx).run()

    assert probe.killed_groups == [100]
    assert probe.killed == []  # the group kill covers it — no redundant bare-pid kill


@pytest.mark.unit
def test_resume_records_its_own_pgid_rather_than_clobbering_a_prior_one_with_null(tmp_path):  # type: ignore[no-untyped-def]
    """A prior generation's own recorded pgid must never be silently nulled by a resume that
    forgot its own — each generation's group-kill target is always the CURRENT one."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_spawn(  # a prior generation already recorded a real group
        "lease_1",
        pid=100,
        process_start_time="start-100",
        pgid=100,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    probe = FakeProbe(alive={(100, "start-100"), (4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    Resume(ctx).run()

    lease = store.active_lease("lease_1")
    assert lease is not None
    assert lease.pgid == 4321  # this generation's own group, never left None


@pytest.mark.unit
def test_resume_records_the_launchers_real_pgid_rather_than_inferring_it_from_the_pid(tmp_path):  # type: ignore[no-untyped-def]
    """D3, the same "recorded, not inferred" contract a fresh spawn already keeps: a resumed
    process's real group can differ from its own pid. A call site that still wrote
    ``pgid=pid`` would record 4321 here instead of the launcher's actual 9999."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    harness.resume_pgid = 9999  # deliberately distinct from resume_pid
    probe = FakeProbe(alive={(100, "start-100"), (4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    Resume(ctx).run()

    lease = store.active_lease("lease_1")
    assert lease is not None
    assert lease.pid == 4321
    assert lease.pgid == 9999  # the launcher's own real group, never re-derived from the pid


@pytest.mark.unit
def test_resumed_lease_is_not_judged_by_advance(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass", resume_process_start_time="start-4321")
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


@pytest.mark.unit
def test_resumed_lease_with_a_durable_session_end_is_still_resumed_not_judged(tmp_path):  # type: ignore[no-untyped-def]
    """A durable `SessionEnd` for the marked lease's own generation must not route it to
    ADVANCE as exited work — the open resume intent is checked first, so RESUME still
    re-attaches it in place, same lease/epoch/session, no retry consumed."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_session_end(lease_id="lease_1", ended_at=_NOW)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass", resume_process_start_time="start-4321")
    harness.resume_pid = 4321
    probe = FakeProbe(alive={(100, "start-100"), (4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    tick(ctx)

    # No verdict elicited, no completion buffered — the stale SessionEnd never reached
    # ADVANCE's judging path at all.
    assert harness.judged == []
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 4321 and lease.epoch == 1  # same lease, no retry, no epoch bump
    assert store.resume_intent_lease_ids() == set()


# --- RESUME — abandon a reassigned / detached chunk (no epoch bump) ---


@pytest.mark.unit
def test_resume_abandons_chunk_reassigned_to_another_runner(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk(runner_id="r2")  # another runner holds it now
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=probe)

    Resume(ctx).run()

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
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkStatusView(  # detached: re-derived ready, route released
        chunk_id="ch_1",
        status=ChunkStatus.READY,
        latest_epoch=1,
        route_runner_id=None,
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict=None), probe=FakeProbe()
    )

    Resume(ctx).run()

    assert provider.released == ["e1"]
    assert store.active_lease("lease_1") is None
    assert store.resume_intent_lease_ids() == set()


@pytest.mark.unit
def test_resume_abandons_chunk_unknown_at_the_hub(tmp_path):  # type: ignore[no-untyped-def]
    """A 404 (``ChunkNotFoundError``) is terminal, not deferred like the generic
    ``HubClientError`` below — ``_resume_marked_lease`` abandons on it directly
    (blizzard#9)."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.not_found = {"ch_1"}
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict=None), probe=FakeProbe()
    )

    Resume(ctx).run()

    assert provider.released == ["e1"]
    assert store.active_lease("lease_1") is None
    assert store.resume_intent_lease_ids() == set()


# --- RESUME — resilience ---


@pytest.mark.unit
def test_resume_defers_when_hub_unreachable(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.down = True  # get_chunk cannot be reached — ownership unverifiable this tick
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Resume(ctx).run()

    # The intent stays open (retry next tick), the environment stays held, nothing resumed.
    assert store.resume_intent_lease_ids() == {"lease_1"}
    assert harness.resumed == []
    assert store.held_environment_ids() == ["e1"]


@pytest.mark.unit
@pytest.mark.parametrize("known_but_unavailable", [False, True], ids=["unknown", "unavailable"])
def test_resume_owner_failure_escalates_in_place(tmp_path, known_but_unavailable):  # type: ignore[no-untyped-def]
    """An unservable exact owner escalates the chunk in place — no other
    runner can resume this exact session, so RESUME kills the stale survivor, closes the
    lease escalated, and clears the resume intent rather than leaving either open forever."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    owner = "other" if known_but_unavailable else "missing"
    with store._engine.begin() as conn:
        conn.exec_driver_sql(f"UPDATE leases SET harness_id = '{owner}' WHERE lease_id = 'lease_1'")
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    bindings = {"claude_code": HarnessBinding(adapter=harness, transcript_source=harness.transcript_source())}
    if known_but_unavailable:
        bindings[owner] = HarnessBinding(transcript_source=harness.transcript_source())
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    ctx = replace(ctx, harnesses=HarnessRegistry(bindings))

    Resume(ctx).run()

    assert harness.resumed == []
    assert probe.killed == [100]  # best-effort hygiene; nothing is live behind the closed lease
    assert store.resume_intent_lease_ids() == set()
    assert store.active_lease("lease_1") is None
    escalations = [e for e in store.open_escalations() if e.chunk_id == "ch_1"]
    assert len(escalations) == 1


@pytest.mark.unit
def test_resume_is_a_noop_without_intents(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)  # in flight, but no graceful-shutdown mark

    hub = FakeHub()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Resume(ctx).run()

    assert harness.resumed == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100  # untouched


@pytest.mark.unit
def test_resume_clears_intent_for_lease_closed_while_down(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)
    # The lease closed while the runner was down (unusual) — RESUME just clears the intent.
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="failed", closed_at=_NOW)

    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
    )

    Resume(ctx).run()

    assert store.resume_intent_lease_ids() == set()
