"""Runner preemption of attempts fenced by an operator restart."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.fact_kinds import RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED
from blizzard.foundation.node_steps import ApplyOutcome, SessionMode
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.hub.node_steps import completion_submission
from blizzard.runner.hub.outbound import COMPLETION_KIND
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.steps import Advance, Pull, Reap, Resume, ResumeIntents
from blizzard.runner.loop.tick import tick
from blizzard.runner.node_steps.chunk_state import ChunkPause, ChunkState
from blizzard.runner.node_steps.submissions import ApplyReply, Completion
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_envelope,
    make_store,
    make_stores,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_running_lease(store, *, chunk="ch_1", lease="lease_1", epoch=1):  # type: ignore[no-untyped-def]
    """A build lease with a live worker and its env binding — the shape a restart preempts."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            retries_max=2,
            session_name="main",
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease,
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _moved_chunk(*, node_id="nd_build", epoch=2, chunk="ch_1"):  # type: ignore[no-untyped-def]
    """The hub's view after a restart: still routed here, at a strictly higher epoch."""
    del node_id  # no runner-loop-visible field carries the node id on ChunkState
    return ChunkState(
        chunk_id=chunk,
        status=ChunkStatus.RUNNING,
        latest_epoch=epoch,
        route_runner_id="r1",
    )


def _ctx(store, hub, *, provider=None, probe=None, harness=None):  # type: ignore[no-untyped-def]
    return make_context(
        store,
        hub=hub,
        provider=provider if provider is not None else FakeProvider({"e1": "/ws/e1"}),
        harness=harness if harness is not None else FakeHarness(handle=_HANDLE, verdict=None),
        probe=probe if probe is not None else FakeProbe(alive={(100, "start-100")}),
    )


def _restarted_hub(*, node_id="nd_build", node_name="build", session=SessionMode.FRESH):  # type: ignore[no-untyped-def]
    hub = FakeHub()
    hub.chunks["ch_1"] = _moved_chunk(node_id=node_id)
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", node_name, node_id=node_id, choices=[("pass", "ok")], epoch=2, session=session, session_name="main"
    )
    return hub


def _brake(store, *, paused):  # type: ignore[no-untyped-def]
    """Set the runner's own local brake, the way ``PATCH /runner`` does — fact + report."""
    store.record_local_pause(
        paused=paused,
        at=_NOW,
        by="operator",
        report_kind=RUNNER_LOCALLY_PAUSED if paused else RUNNER_LOCALLY_RESUMED,
        report_payload=json.dumps({"runner_id": "r1", "by": "operator"}),
    )


def test_pull_preempts_a_lease_the_hub_fenced_out_and_re_enters_the_node(tmp_path):  # type: ignore[no-untyped-def]
    """Preemption keeps the route and environments while minting above the fence."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    provider = FakeProvider({"e1": "/ws/e1"})
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, _restarted_hub(), provider=provider, probe=probe)

    Pull(ctx).run()

    assert probe.killed == [100]
    assert provider.released == []  # the environment is kept — this is not a detach
    assert store.active_lease("lease_1") is None
    assert store.bindings_for_chunk("ch_1")  # still bound
    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.lease_id != "lease_1"
    assert fresh.epoch == 3  # strictly above the hub's own fence at 2
    # One attempt, not two: the preempted lease was superseded, not spent.
    assert store.attempt_count("ch_1", "nd_build") == 1


def test_the_re_entry_mints_a_session_rather_than_resuming_the_pool_head(tmp_path):  # type: ignore[no-untyped-def]
    """The fresh envelope must not resume the preempted lease's pool head."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    handle = WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200", pgid=200)
    harness = FakeHarness(handle=handle, verdict=None)
    ctx = _ctx(store, _restarted_hub(), harness=harness, probe=FakeProbe(alive={(100, "start-100")}))

    Pull(ctx).run()

    assert harness.resume_froms == [None]  # no pool head resumed
    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.session_id == "sess-b"


def test_pull_preempts_a_lease_whose_chunk_was_moved_to_another_node(tmp_path):  # type: ignore[no-untyped-def]
    """Re-entry follows the envelope, not the stale lease node."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = _restarted_hub(node_id="nd_plan", node_name="plan")
    ctx = _ctx(store, hub)

    Pull(ctx).run()

    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.node_name == "plan"


def test_pull_leaves_a_lease_at_the_hubs_own_epoch_untouched(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = _moved_chunk(epoch=1)
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, probe=probe)

    Pull(ctx).run()

    assert probe.killed == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100


def test_pull_never_preempts_a_session_a_person_is_inside(tmp_path):  # type: ignore[no-untyped-def]
    """A forced takeover's epoch must not cause the operator's terminal to be killed."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_takeover(
        takeover_id="tk_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=2,
        opened_at=_NOW,
    )
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, _restarted_hub(), probe=probe)

    Pull(ctx).run()

    assert probe.killed == []
    assert store.active_lease("lease_1") is not None


def test_a_queued_submission_still_reaches_the_hub_behind_the_preempt(tmp_path):  # type: ignore[no-untyped-def]
    """Reconcile before draining the queued completion; its rejection spends no retry."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    submission = Completion(choice="pass", epoch=1, from_node_id="nd_build")
    store.enqueue_outbound(
        kind=COMPLETION_KIND,
        chunk_id="ch_1",
        lease_id="lease_1",
        payload=json.dumps({"submission": completion_submission(submission).model_dump(mode="json")}),
        created_at=_NOW,
    )
    hub = _restarted_hub()
    hub.apply_responses = [ApplyReply(outcome=ApplyOutcome.FAILURE, detail="stale epoch 1; chunk is at 2")]
    ctx = _ctx(store, hub)

    Pull(ctx).run()

    assert hub.completions  # the submission reached the hub rather than being discarded
    closed = {record.lease.lease_id: record.reason for record in store.list_closed_leases(10)}
    assert closed["lease_1"] == "preempted"
    assert store.attempt_count("ch_1", "nd_build") == 1  # the rejection spent nothing


def test_a_local_pause_still_kills_and_closes_the_fenced_out_worker(tmp_path):  # type: ignore[no-untyped-def]
    """The displaced worker is fenced out already, so the brake does not keep it running at a
    stale epoch: it is killed and closed now, and only the re-entry spawn waits."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    _brake(store, paused=True)
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, _restarted_hub(), probe=probe)

    Pull(ctx).run()

    assert probe.killed == [100]
    assert store.active_lease("lease_1") is None
    assert store.active_lease_for_chunk("ch_1") is None  # the re-entry spawn waits for the brake
    assert store.attempt_count("ch_1", "nd_build") == 0  # a preempted attempt spends no retry


def test_the_re_entry_spawns_once_the_local_brake_clears(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    _brake(store, paused=True)
    ctx = _ctx(store, _restarted_hub())
    Pull(ctx).run()

    _brake(store, paused=False)
    Advance(_ctx(store, _restarted_hub())).run()  # the held chunk's newer epoch is entered

    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.lease_id != "lease_1"


def test_a_crash_between_the_kill_and_the_closure_still_costs_no_retry(tmp_path):  # type: ignore[no-untyped-def]
    """A crash after the kill leaves an active lease; the next PULL closes it as preempted."""
    store = _store(tmp_path)
    _seed_running_lease(store)  # the crash state: an active lease whose pid no longer exists
    ctx = _ctx(store, _restarted_hub(), probe=FakeProbe(alive=set()))

    Pull(ctx).run()

    closed = {record.lease.lease_id: record.reason for record in store.list_closed_leases(10)}
    assert closed["lease_1"] == "preempted"
    assert store.attempt_count("ch_1", "nd_build") == 1


def test_an_operator_pause_outranks_a_restart(tmp_path):  # type: ignore[no-untyped-def]
    """The pause parks the lease without discarding the pending move."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = _restarted_hub()
    hub.chunks["ch_1"] = replace(_moved_chunk(), pause=ChunkPause(by="operator", set_at="2026-07-13T12:00:00Z"))
    ctx = _ctx(store, hub)

    Pull(ctx).run()

    assert store.active_lease("lease_1") is not None
    assert "lease_1" in store.pause_parked_lease_ids()


def test_a_preempted_ask_park_is_retired_with_the_lease(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="?",
        options=[],
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    ctx = _ctx(store, _restarted_hub())

    Pull(ctx).run()

    assert store.open_park("lease_1") is None


def test_a_restart_does_not_spend_the_nodes_retry_budget(tmp_path):  # type: ignore[no-untyped-def]
    """Repeated preempts do not consume the node's retry budget."""
    store = _store(tmp_path)
    _seed_running_lease(store)  # retries_max=2

    for round_ in range(3):  # one more than the budget
        hub = FakeHub()
        hub.chunks["ch_1"] = _moved_chunk(epoch=2 + round_ * 2)
        hub.envelopes["ch_1"] = make_envelope(
            "ch_1",
            "build",
            node_id="nd_build",
            choices=[("pass", "ok")],
            epoch=2,
            session=SessionMode.FRESH,
            session_name="main",
        )
        pid = 100 + round_
        handle = WorkerHandle(session_id=f"sess-{round_}", pid=pid, process_start_time=f"start-{pid}", pgid=pid)
        live = store.active_lease_for_chunk("ch_1")
        assert live is not None and live.pid is not None and live.process_start_time is not None
        ctx = _ctx(
            store,
            hub,
            harness=FakeHarness(handle=handle, verdict=None),
            probe=FakeProbe(alive={(live.pid, live.process_start_time)}),
        )
        Pull(ctx).run()

    # Only the one live attempt counts — `fail` reads `attempt_count - 1` against
    # `retries_max`, so the chunk still has its full budget rather than having escalated.
    assert store.attempt_count("ch_1", "nd_build") == 1


def test_a_restart_level_with_the_lease_still_fences_it(tmp_path):  # type: ignore[no-untyped-def]
    """A buffered lease mint can be level with the hub's restart, which still fences it."""
    store = _store(tmp_path)
    _seed_running_lease(store)  # epoch 1, its mint not yet drained to the hub
    hub = _restarted_hub()
    hub.chunks["ch_1"] = replace(_moved_chunk(epoch=1), restart_epochs=[1])
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, probe=probe)

    Pull(ctx).run()

    assert probe.killed == [100]
    closed = {record.lease.lease_id: record.reason for record in store.list_closed_leases(10)}
    assert closed["lease_1"] == "preempted"
    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.epoch > 1  # strictly above the move — never re-fenced


def test_an_older_restart_never_re_fences_the_lease_it_already_produced(tmp_path):  # type: ignore[no-untyped-def]
    """The move must not re-fence a lease already minted above it."""
    store = _store(tmp_path)
    _seed_running_lease(store, epoch=2)  # the lease a restart at epoch 1 already produced
    hub = _restarted_hub()
    hub.chunks["ch_1"] = replace(_moved_chunk(epoch=2), restart_epochs=[1])
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, probe=probe)

    Pull(ctx).run()

    assert probe.killed == []
    assert store.active_lease("lease_1") is not None


def test_lifting_an_operator_pause_preempts_and_re_enters_on_the_next_tick(tmp_path):  # type: ignore[no-untyped-def]
    """After the pause lifts, the pending move mints a fresh lease and session."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    paused = _restarted_hub()
    paused.chunks["ch_1"] = replace(_moved_chunk(), pause=ChunkPause(by="operator", set_at="2026-07-13T12:00:00Z"))
    Pull(_ctx(store, paused)).run()
    assert "lease_1" in store.pause_parked_lease_ids()
    assert store.active_lease("lease_1") is not None  # parked, not yet preempted

    handle = WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200", pgid=200)
    harness = FakeHarness(handle=handle, verdict=None)
    probe = FakeProbe(alive={(100, "start-100")})
    Pull(_ctx(store, _restarted_hub(), harness=harness, probe=probe)).run()

    assert probe.killed == [100]
    closed = {record.lease.lease_id: record.reason for record in store.list_closed_leases(10)}
    assert closed["lease_1"] == "preempted"
    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.lease_id != "lease_1"
    assert fresh.epoch == 3 and fresh.session_id == "sess-b"
    assert harness.resume_froms == [None]


def test_a_second_lease_at_the_forced_node_is_fresh_too(tmp_path):  # type: ignore[no-untyped-def]
    """The forced visit stays fresh even after its first re-entry is orphaned."""
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_2",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=3,  # the first re-entry, minted above the restart at 2; never spawned
            retries_max=2,
            session_name="main",
            created_at=_NOW,
        )
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = _restarted_hub()
    hub.chunks["ch_1"] = replace(_moved_chunk(epoch=3), restart_epochs=[2])
    handle = WorkerHandle(session_id="sess-c", pid=300, process_start_time="start-300", pgid=300)
    harness = FakeHarness(handle=handle, verdict=None)

    Reap(_ctx(store, hub, harness=harness, probe=FakeProbe())).run()

    latest = store.active_lease_for_chunk("ch_1")
    assert latest is not None and latest.lease_id != "lease_2"
    assert latest.session_id == "sess-c"
    assert harness.resume_froms == [None]  # minted again, not resumed off a pool head


def test_a_full_tick_under_the_local_brake_preempts_but_spawns_nothing_and_the_next_past_it_re_enters(tmp_path):  # type: ignore[no-untyped-def]
    """A full braked tick kills and closes the fenced-out worker but starts nothing; the re-entry
    waits until the brake lifts."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    _brake(store, paused=True)
    handle = WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200", pgid=200)
    harness = FakeHarness(handle=handle, verdict=None)
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, _restarted_hub(), harness=harness, probe=probe)

    tick(ctx)

    assert probe.killed == [100]
    assert harness.spawns == []
    assert store.active_lease("lease_1") is None

    _brake(store, paused=False)
    tick(ctx)

    assert probe.killed == [100]
    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.lease_id != "lease_1" and fresh.session_id == "sess-b"


def _marked_for_restart_resume(store):  # type: ignore[no-untyped-def]
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)
    assert store.resume_intent_lease_ids() == {"lease_1"}


def test_restart_resume_after_downtime_preempts_a_fenced_out_session_rather_than_waking_it(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    _marked_for_restart_resume(store)
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, _restarted_hub(), harness=harness, probe=probe)

    Resume(ctx).run()

    assert harness.resumed == []  # the stale session is never woken
    assert probe.killed == [100]
    assert store.active_lease("lease_1") is None
    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.lease_id != "lease_1"
    assert fresh.epoch == 3  # re-entered above the hub's own fence at 2
    assert len(harness.spawns) == 1
    assert store.attempt_count("ch_1", "nd_build") == 1  # preempted, not spent
    assert store.resume_intent_lease_ids() == set()


def test_the_local_brake_does_not_defer_a_restart_resume_preempt(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    _marked_for_restart_resume(store)
    _brake(store, paused=True)
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, _restarted_hub(), harness=harness, probe=probe)

    Resume(ctx).run()

    assert probe.killed == [100]  # killed and closed now, the brake notwithstanding
    assert store.active_lease("lease_1") is None
    assert store.resume_intent_lease_ids() == set()
    assert harness.resumed == []
    assert harness.spawns == []  # only the re-entry spawn waits for the brake
    assert store.active_lease_for_chunk("ch_1") is None
    assert store.attempt_count("ch_1", "nd_build") == 0
