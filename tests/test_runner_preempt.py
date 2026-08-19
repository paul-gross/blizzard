"""Preempting an attempt an operator's restart fenced out (issue #370).

``Pull._reconcile_leases`` reads each active lease against the hub's fence; a restart raised
it, so the attempt is preempted — killed and closed with route, tenure and environments kept
— then the node re-entered. "Preempt" is the runner-side name for the whole move;
``test_runner_restart_resume.py`` next door is the unrelated daemon-restart re-attach."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.graph import SessionMode
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.outbound import COMPLETION_KIND
from blizzard.runner.loop.steps import Pull
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, PauseView, RestartView, RouteView
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.facts import RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_envelope,
    make_store,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")
_ISO_NOW = "2026-07-13T12:00:00Z"


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
            runner_id="r1",
            retries_max=2,
            session_name="main",
            created_at=_NOW,
        )
    )
    store.record_spawn(lease, pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _moved_chunk(*, node_id="nd_build", epoch=2, chunk="ch_1"):  # type: ignore[no-untyped-def]
    """The hub's view after a restart: still routed here, at a strictly higher epoch."""
    return ChunkDetail(
        chunk_id=chunk,
        graph_id="gr_1",
        status=ChunkStatus.RUNNING,
        current_node_id=node_id,
        latest_epoch=epoch,
        route=RouteView(runner_id="r1", workspace_id="ws1", environment_ids=["e1"]),
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
        "r1",
        paused=paused,
        at=_NOW,
        by="operator",
        report_kind=RUNNER_LOCALLY_PAUSED if paused else RUNNER_LOCALLY_RESUMED,
        report_payload=json.dumps({"runner_id": "r1", "by": "operator"}),
    )


def test_pull_preempts_a_lease_the_hub_fenced_out_and_re_enters_the_node(tmp_path):  # type: ignore[no-untyped-def]
    """The worker dies and its lease closes, but route, tenure and envs are all kept —
    a fresh lease above the hub's own fence is spawned into the same environment."""
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
    """The envelope the hub serves after a restart declares ``fresh``, so the pool head the
    preempted lease left behind is not resumed — a new head is minted in its place."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    handle = WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200")
    harness = FakeHarness(handle=handle, verdict=None)
    ctx = _ctx(store, _restarted_hub(), harness=harness, probe=FakeProbe(alive={(100, "start-100")}))

    Pull(ctx).run()

    assert harness.resume_froms == [None]  # no pool head resumed
    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.session_id == "sess-b"


def test_pull_preempts_a_lease_whose_chunk_was_moved_to_another_node(tmp_path):  # type: ignore[no-untyped-def]
    """``--node`` aims the move somewhere else; the re-entry follows the envelope, not the
    lease's own stale node."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = _restarted_hub(node_id="nd_plan", node_name="plan")
    ctx = _ctx(store, hub)

    Pull(ctx).run()

    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.node_name == "plan"


def test_pull_leaves_a_lease_at_the_hubs_own_epoch_untouched(tmp_path):  # type: ignore[no-untyped-def]
    """The ordinary steady state — the hub knows this attempt's epoch and nothing moved."""
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
    """A forced takeover raises the epoch exactly as a restart does; preempting on it would
    kill the operator's own terminal."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_takeover(
        takeover_id="tk_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess-a",
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
    """A queued completion buys the attempt no reprieve: reconciliation runs ahead of the
    drain, so the lease closes ``preempted`` and the stale-epoch rejection lands on a lease
    already gone — never on ``fail``, which would spend the budget. It is still delivered."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    submission = CompletionSubmission(choice="pass", epoch=1, runner_id="r1", from_node_id="nd_build")
    store.enqueue_outbound(
        kind=COMPLETION_KIND,
        chunk_id="ch_1",
        lease_id="lease_1",
        payload=json.dumps({"submission": submission.model_dump(mode="json")}),
        created_at=_NOW,
    )
    hub = _restarted_hub()
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.FAILURE, detail="stale epoch 1; chunk is at 2")]
    ctx = _ctx(store, hub)

    Pull(ctx).run()

    assert hub.completions  # the submission reached the hub rather than being discarded
    closed = {record.lease.lease_id: record.reason for record in store.list_closed_leases(10)}
    assert closed["lease_1"] == "preempted"
    assert store.attempt_count("ch_1", "nd_build") == 1  # the rejection spent nothing


def test_a_local_pause_defers_the_preempt_rather_than_killing_the_worker(tmp_path):  # type: ignore[no-untyped-def]
    """The runner's own brake means "start no processes here", and the re-entry is a start:
    preempting under it would kill the worker with nothing able to replace it. The lease,
    its worker and the fence all stand until the brake clears."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    _brake(store, paused=True)
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, _restarted_hub(), probe=probe)

    Pull(ctx).run()

    assert probe.killed == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100
    assert store.attempt_count("ch_1", "nd_build") == 1


def test_the_preempt_resumes_after_the_local_brake_clears(tmp_path):  # type: ignore[no-untyped-def]
    """The deferral is not a skip — the fence is still up on the next tick past the brake."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    _brake(store, paused=True)
    ctx = _ctx(store, _restarted_hub())
    Pull(ctx).run()

    _brake(store, paused=False)
    Pull(_ctx(store, _restarted_hub())).run()

    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.lease_id != "lease_1"


def test_a_crash_between_the_kill_and_the_closure_still_costs_no_retry(tmp_path):  # type: ignore[no-untyped-def]
    """``preempt.after-kill.before-closure``: the worker is dead and the lease still active.
    The fence is durable at the hub, so the next PULL simply preempts again — the lease closes
    ``preempted`` rather than being reaped or failed, and the node's budget is untouched."""
    store = _store(tmp_path)
    _seed_running_lease(store)  # the crash state: an active lease whose pid no longer exists
    ctx = _ctx(store, _restarted_hub(), probe=FakeProbe(alive=set()))

    Pull(ctx).run()

    closed = {record.lease.lease_id: record.reason for record in store.list_closed_leases(10)}
    assert closed["lease_1"] == "preempted"
    assert store.attempt_count("ch_1", "nd_build") == 1


def test_an_operator_pause_outranks_a_restart(tmp_path):  # type: ignore[no-untyped-def]
    """Paused keeps the lease, route and epoch, so the worker parks rather than re-entering;
    the move is honored on the tick after the pause lifts."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = _restarted_hub()
    hub.chunks["ch_1"] = _moved_chunk().model_copy(
        update={"pause": PauseView(by="operator", set_at="2026-07-13T12:00:00Z")}
    )
    ctx = _ctx(store, hub)

    Pull(ctx).run()

    assert store.active_lease("lease_1") is not None
    assert "lease_1" in store.pause_parked_lease_ids()


def test_a_preempted_ask_park_is_retired_with_the_lease(tmp_path):  # type: ignore[no-untyped-def]
    """The move already answered the question at the hub, so the local park must not outlive
    the lease it belonged to."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="?",
        options=[],
        session_id="sess-a",
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    ctx = _ctx(store, _restarted_hub())

    Pull(ctx).run()

    assert store.open_park("lease_1") is None


def test_a_restart_does_not_spend_the_nodes_retry_budget(tmp_path):  # type: ignore[no-untyped-def]
    """Restarting a node repeatedly never escalates the chunk the operator is rescuing:
    the budget counts the attempts it *spent*, and a preempted one was superseded instead."""
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
        handle = WorkerHandle(session_id=f"sess-{round_}", pid=pid, process_start_time=f"start-{pid}")
        live = store.active_lease_for_chunk("ch_1")
        assert live is not None
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
    """The hub mints the move one above the newest epoch IT knows, so a lease whose own
    ``lease.minted`` fact is still buffered here can be moved onto level ground. The move is
    still a fence — otherwise a restart issued in the tick a chunk starts never lands."""
    store = _store(tmp_path)
    _seed_running_lease(store)  # epoch 1, its mint not yet drained to the hub
    hub = _restarted_hub()
    hub.chunks["ch_1"] = _moved_chunk(epoch=1).model_copy(
        update={
            "restarts": [
                RestartView(to_node_id="nd_build", graph_id="gr_1", epoch=1, restarted_by="op", recorded_at=_ISO_NOW)
            ]
        }
    )
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, probe=probe)

    Pull(ctx).run()

    assert probe.killed == [100]
    closed = {record.lease.lease_id: record.reason for record in store.list_closed_leases(10)}
    assert closed["lease_1"] == "preempted"
    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.epoch > 1  # strictly above the move — never re-fenced


def test_an_older_restart_never_re_fences_the_lease_it_already_produced(tmp_path):  # type: ignore[no-untyped-def]
    """The re-entry mints above the move, so the move's own fact must not preempt it again —
    otherwise one restart would loop the chunk forever."""
    store = _store(tmp_path)
    _seed_running_lease(store, epoch=2)  # the lease a restart at epoch 1 already produced
    hub = _restarted_hub()
    hub.chunks["ch_1"] = _moved_chunk(epoch=2).model_copy(
        update={
            "restarts": [
                RestartView(to_node_id="nd_build", graph_id="gr_1", epoch=1, restarted_by="op", recorded_at=_ISO_NOW)
            ]
        }
    )
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = _ctx(store, hub, probe=probe)

    Pull(ctx).run()

    assert probe.killed == []
    assert store.active_lease("lease_1") is not None
