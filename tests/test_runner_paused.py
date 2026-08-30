"""The runner adheres to the hub's pause brake — loop component tier.

The declarative pause brake lives at the hub; the runner reads it on PULL, mirrors it
to its store, and FILL adheres: paused = no new claims, in-flight chunks run on. When
the hub is unreachable the runner keeps its last-mirrored directive. Driven against a
real (tmp sqlite) runner store with fakes standing in only at the seams."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.hub import HubClientError, RouteClaimOutcome
from blizzard.runner.loop.steps import Advance, Fill, Pull, Reap, Resume, ResumeIntents, SpendCeiling
from blizzard.runner.loop.tick import tick
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, PauseView, RouteView
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.facts import (
    ANSWER_DELIVERED,
    ESCALATION_RECORDED,
    LEASE_MINTED,
    RUNNER_LOCALLY_PAUSED,
    RUNNER_LOCALLY_RESUMED,
)
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekEntry
from blizzard.wire.route import RouteClaimPausedDenial
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


class _BlipOnceHub(FakeHub):
    """A ``FakeHub`` whose first ``get_chunk`` raises, then serves normally.

    ``FakeHub.down`` is all-or-nothing, which cannot express RESUME's ownership check
    failing while ADVANCE's envelope fetch, a moment later, succeeds."""

    def __init__(self) -> None:
        super().__init__()
        self.get_chunk_calls = 0

    def get_chunk(self, chunk_id: str) -> ChunkDetail:
        self.get_chunk_calls += 1
        if self.get_chunk_calls == 1:
            raise HubClientError("transient blip during the ownership check")
        return super().get_chunk(chunk_id)


def _pause_locally(store, ctx, *, paused: bool):  # type: ignore[no-untyped-def]
    """Set the runner's own brake, the way `PATCH /runner` does — fact + report, one write."""
    store.record_local_pause(
        "r1",
        paused=paused,
        at=ctx.clock.now(),
        by="operator",
        report_kind=RUNNER_LOCALLY_PAUSED if paused else RUNNER_LOCALLY_RESUMED,
        report_payload=json.dumps({"runner_id": "r1", "by": "operator"}),
    )


def _ctx_with_a_claimable_chunk(tmp_path, *, paused: bool):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    hub.paused = paused
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )
    return ctx, hub, store


def test_pull_mirrors_the_hub_pause_brake_and_registers(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    Pull(ctx).run()
    # PULL registered the runner (liveness heartbeat) and mirrored the brake locally.
    assert hub.registered == [("r1", "ws1")]
    assert store.hub_paused("r1") is True


def test_pull_reports_the_configured_env_capacity(tmp_path):  # type: ignore[no-untyped-def]
    """PULL's registration (the heartbeat) carries the runner's env-pool size (issue #69) so
    the board's slot bar has a `total`; a changed pool converges on the next pull."""
    ctx, hub, _store = _ctx_with_a_claimable_chunk(tmp_path, paused=False)

    # env_capacity is mirrored from len(workspace_envs) at build; frozen config, so replace.
    Pull(replace(ctx, config=replace(ctx.config, env_capacity=4))).run()
    assert hub.registered_capacities == [4]

    # The operator grew workspace_envs and the runner re-synced — the new count converges.
    Pull(replace(ctx, config=replace(ctx.config, env_capacity=6))).run()
    assert hub.registered_capacities == [4, 6]


def test_fill_claims_nothing_while_paused(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    Pull(ctx).run()  # mirror paused=True
    Fill(ctx).run()
    # No claim was attempted and no lease was minted — the queue is untouched.
    assert hub.claims == []
    assert store.list_active_leases() == []


def test_fill_claims_again_after_resume(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    Pull(ctx).run()
    Fill(ctx).run()
    assert store.list_active_leases() == []

    # The operator resumes the runner; the next PULL mirrors it and FILL claims.
    hub.paused = False
    Pull(ctx).run()
    Fill(ctx).run()
    assert len(hub.claims) == 1
    assert len(store.list_active_leases()) == 1


def test_in_flight_chunk_runs_on_while_paused(tmp_path):  # type: ignore[no-untyped-def]
    """Pausing stops new claims; an already-claimed chunk is untouched by FILL."""
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=False)
    Pull(ctx).run()
    Fill(ctx).run()  # claims ch_1
    assert len(store.list_active_leases()) == 1

    # Now pause; FILL must not tear down or re-claim — the in-flight lease persists.
    hub.paused = True
    Pull(ctx).run()
    Fill(ctx).run()
    assert len(store.list_active_leases()) == 1


def test_unreachable_hub_keeps_last_mirrored_brake(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    Pull(ctx).run()  # mirror paused=True
    assert store.hub_paused("r1") is True

    # The hub goes unreachable; PULL cannot refresh, so the last-known brake holds.
    hub.down = True
    Pull(ctx).run()
    assert store.hub_paused("r1") is True
    Fill(ctx).run()
    assert hub.claims == []  # still adhering to the last directive


# The runner's own local brake (issue #43), independent of the hub's; effective paused
# is the OR of both, each cleared only where it was set.


def test_fill_claims_nothing_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=False)
    Pull(ctx).run()  # the hub's brake is off — only the local one stops this claim
    _pause_locally(store, ctx, paused=True)
    Fill(ctx).run()
    assert hub.claims == []
    assert store.list_active_leases() == []


def test_fill_claims_again_after_a_local_start(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=False)
    Pull(ctx).run()
    _pause_locally(store, ctx, paused=True)
    Fill(ctx).run()
    assert hub.claims == []

    # Facts append and the flag derives from the newest — no row is mutated.
    _pause_locally(store, ctx, paused=False)
    Fill(ctx).run()
    assert len(hub.claims) == 1
    assert len(store.list_active_leases()) == 1


def test_a_local_start_does_not_clear_the_hubs_brake(tmp_path):  # type: ignore[no-untyped-def]
    """Each brake is cleared only on the surface that set it — the OR still holds."""
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    Pull(ctx).run()  # mirror the hub's brake on
    _pause_locally(store, ctx, paused=False)
    Fill(ctx).run()
    # Locally started, but the hub still says paused — so nothing is claimed.
    assert store.local_paused("r1") is False
    assert store.hub_paused("r1") is True
    assert hub.claims == []


def test_in_flight_chunk_runs_on_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    """Pausing drains rather than kills — the same contract the hub's brake honors."""
    store = _store(tmp_path)
    hub = FakeHub()
    hub.paused = False
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    probe = FakeProbe()
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=probe,
    )
    Pull(ctx).run()
    Fill(ctx).run()
    assert len(store.list_active_leases()) == 1

    _pause_locally(store, ctx, paused=True)
    Fill(ctx).run()
    assert len(store.list_active_leases()) == 1  # untouched — only new claims stop
    assert probe.killed == []  # a live worker already running is not killed


# The local brake reaches every spawn site, not just FILL's claim (issue #45): also
# restart-resume, answer-resume, and every ``_spawn_attempt`` caller.


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


def _seed_exited_lease(store):  # type: ignore[no-untyped-def]
    """A build lease spawned into env e1; the worker has already exited (no probe entry)."""
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
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _answered_question(question_id="qn_1") -> QuestionView:  # type: ignore[no-untyped-def]
    return QuestionView(
        question_id=question_id,
        chunk_id="ch_1",
        runner_id="r1",
        epoch=1,
        question="Which API?",
        asked_at="t",
        answered=True,
        answer="rest",
        answered_by="alice",
        answered_at="t2",
    )


def test_restart_resume_suppressed_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(store).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    # RESUME's precondition is a lease whose worker is dead; pid 100 is not in the
    # alive set, matching what RESUME actually recovers.
    probe = FakeProbe(alive={(4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    _pause_locally(store, ctx, paused=True)

    Resume(ctx).run()

    # Suppressed before the kill: no survivor killed, no resume delivered, the intent stays open.
    assert probe.killed == []
    assert harness.resumed == []
    assert store.resume_intent_lease_ids() == {"lease_1"}
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100  # untouched

    # Unpause; RESUME re-asks the same open intent and resumes it.
    _pause_locally(store, ctx, paused=False)
    Resume(ctx).run()

    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The supervisor restarted; continue your task where you left off.")
    ]
    assert store.resume_intent_lease_ids() == set()


def test_restart_resume_suppressed_then_advance_does_not_judge_or_spawn(tmp_path):  # type: ignore[no-untyped-def]
    """A suppressed restart-resume must not leak the lease to ADVANCE (issue #45 review),
    which would otherwise spawn a harness process and judge a killed worker as done."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(store).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    # The restart-stranded worker (pid 100) is dead; pid 4321 reads alive once resumed,
    # so the post-unpause pass finds a running worker, not another exit to judge.
    probe = FakeProbe(alive={(4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    _pause_locally(store, ctx, paused=True)

    Resume(ctx).run()
    Advance(ctx).run()

    # No process was spawned by either step, and no verdict was elicited from the killed
    # session — the lease is left exactly as it was, waiting for RESUME to re-attach it.
    assert harness.resumed == []
    assert harness.judged == []
    assert probe.killed == []
    assert store.resume_intent_lease_ids() == {"lease_1"}
    lease = store.active_lease("lease_1")  # still active — a closed lease would read None here
    assert lease is not None and lease.pid == 100
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []

    # Unpause; RESUME re-attaches it in place, then ADVANCE leaves the now-live worker alone.
    _pause_locally(store, ctx, paused=False)
    Resume(ctx).run()
    Advance(ctx).run()

    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The supervisor restarted; continue your task where you left off.")
    ]
    assert harness.judged == []  # the resumed worker (pid 4321) reads as alive — still running
    assert store.resume_intent_lease_ids() == set()
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 4321


def test_answer_resume_suppressed_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Q",
        options=[],
        session_id="sess-a",
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    hub = FakeHub()
    hub.questions["qn_1"] = _answered_question()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())
    _pause_locally(store, ctx, paused=True)

    Advance(ctx).run()

    # Suppressed before the poll: nothing resumed, the park stays open, no answer.delivered.
    assert harness.resumed == []
    assert store.parked_lease_ids() == {"lease_1"}
    assert [f for f in store.pending_outbound() if f.kind == ANSWER_DELIVERED] == []

    # Unpause; ADVANCE re-polls the same open park and resumes it around the answer.
    _pause_locally(store, ctx, paused=False)
    Advance(ctx).run()

    assert harness.resumed == [("/ws/e1", "sess-a", "# Answer from alice. Continue.\nrest")]
    assert store.parked_lease_ids() == set()
    assert [f for f in store.pending_outbound() if f.kind == ANSWER_DELIVERED]


def test_exited_worker_judgement_suppressed_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    """ADVANCE's judgement resume is the fourth spawn primitive (issue #45 review): a
    worker that exits naturally while paused must not be judged, since judging it
    resumes its session headlessly."""
    store = _store(tmp_path)
    _seed_exited_lease(store)  # no resume intent — a plain, already-exited worker

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())
    _pause_locally(store, ctx, paused=True)

    Advance(ctx).run()

    # Suppressed before the judge call: no verdict elicited, no completion buffered, the
    # lease is left exactly as it was.
    assert harness.judged == []
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100

    # Unpause; ADVANCE re-drives the same exited worker and judges it this time.
    _pause_locally(store, ctx, paused=False)
    Advance(ctx).run()

    assert len(harness.judged) == 1
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"]


def test_apply_response_next_spawn_suppressed_then_adopted_at_unpause(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    next_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=next_env)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200"), verdict="pass"
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Advance(ctx).run()  # the worker exited (no probe entry) -> buffers the completion
    _pause_locally(store, ctx, paused=True)
    Pull(ctx).run()  # flushes the completion; the apply-response's next-node spawn is suppressed

    # The old attempt still closes normally — only the fresh spawn is suppressed, leaving
    # the chunk in the shape of an interrupted claim: a live binding, no active lease.
    assert store.active_lease_for_chunk("ch_1") is None
    assert harness.spawns == []
    assert store.held_environment_ids() == ["e1"]

    # Unpause; the next FILL's reconcile pass sees the same shape a crashed FILL
    # would leave and adopts it — no deferred-spawn state was needed.
    _pause_locally(store, ctx, paused=False)
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.RUNNING,
        current_node_id="nd_review",
        latest_epoch=2,
        route=RouteView(runner_id="r1", workspace_id="ws1", environment_ids=["e1"]),
    )
    hub.envelopes["ch_1"] = next_env
    Fill(ctx).run()

    assert len(harness.spawns) == 1
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "review"


def test_hub_paused_only_restart_resume_still_spawns(tmp_path):  # type: ignore[no-untyped-def]
    """The mirror image: hub brake on, local brake off — restart-resume is unaffected."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(store).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.paused = True
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    # Pid 100 is dead — RESUME's actual precondition; 4321 reads alive once resumed.
    probe = FakeProbe(alive={(4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    Pull(ctx).run()  # mirror the hub brake on; the local brake stays untouched
    assert store.hub_paused("r1") is True
    assert store.local_paused("r1") is False

    Resume(ctx).run()

    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The supervisor restarted; continue your task where you left off.")
    ]
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 4321


def test_hub_paused_only_requeue_still_spawns(tmp_path):  # type: ignore[no-untyped-def]
    """The mirror image: hub brake on, local brake off — a requeue respawn is unaffected."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.paused = True
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=201, process_start_time="start-201"), verdict=None
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())
    Pull(ctx).run()  # mirror the hub brake on; the local brake stays untouched
    assert store.hub_paused("r1") is True
    assert store.local_paused("r1") is False

    Advance(ctx).run()  # no parseable verdict -> failure -> requeue in place

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.epoch == 2  # a fresh attempt was spawned
    assert store.attempt_count("ch_1", "nd_build") == 2


def test_suppression_logged_once_per_lease_per_tick_per_site(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store, chunk="ch_1", lease="lease_1")
    _seed_running_lease(store, chunk="ch_2", lease="lease_2", pid=101, start="start-101", session="sess-b")
    ResumeIntents(store).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk("ch_1")
    hub.chunks["ch_2"] = _running_chunk("ch_2")
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    # Both original workers are dead — RESUME's actual precondition.
    probe = FakeProbe(alive=set())
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    _pause_locally(store, ctx, paused=True)

    with capture_logs() as logs:
        Resume(ctx).run()

    suppressed = [entry for entry in logs if entry["event"] == "spawn suppressed — locally paused"]
    assert len(suppressed) == 2  # one line per lease this tick — no dedupe state, no repeats
    by_lease = {entry["lease_id"]: entry for entry in suppressed}
    assert by_lease.keys() == {"lease_1", "lease_2"}
    for entry in suppressed:
        assert entry["via"] == "resume"
        assert entry["runner_id"] == "r1"
        assert entry["chunk_id"] == by_lease[entry["lease_id"]]["chunk_id"]


# REAP's own guard (issue #45): local_paused guards only the stall case's kill, while
# the orphan case (no process to kill) self-defers both branches.


def _seed_orphan_lease(store, *, chunk="ch_1", lease="lease_1", retries_max=2, epoch=1):  # type: ignore[no-untyped-def]
    """A lease minted at FILL but never spawned (no pid/session) — REAP's orphan case."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            runner_id="r1",
            retries_max=retries_max,
            created_at=_NOW,
        )
    )
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def test_reap_orphan_requeue_respawn_suppressed_then_adopted_at_unpause(tmp_path):  # type: ignore[no-untyped-def]
    """REAP's orphan case reaps and requeues as always; only the respawn is suppressed,
    leaving the chunk shaped like an interrupted claim that FILL adopts once unpaused."""
    store = _store(tmp_path)
    _seed_orphan_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=202, process_start_time="start-202"), verdict="pass"
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())
    _pause_locally(store, ctx, paused=True)

    Reap(ctx).run()

    # Reaped and requeued as always, but the requeue's respawn is suppressed: the old
    # lease is closed and the chunk holds its binding with no active lease.
    assert store.active_lease("lease_1") is None
    assert store.active_lease_for_chunk("ch_1") is None
    assert store.attempt_count("ch_1", "nd_build") == 1
    assert [f for f in store.pending_outbound() if f.kind == LEASE_MINTED] == []
    assert store.held_environment_ids() == ["e1"]

    # Unpause; FILL's reconcile pass sees the same shape a crashed FILL would
    # leave and adopts it — no deferred-spawn state was needed.
    _pause_locally(store, ctx, paused=False)
    hub.chunks["ch_1"] = _running_chunk("ch_1")
    Fill(ctx).run()

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.pid == 202


def test_hub_paused_only_reap_still_requeues(tmp_path):  # type: ignore[no-untyped-def]
    """The mirror image: hub brake on, local brake off — REAP reaps/requeues as today."""
    store = _store(tmp_path)
    _seed_orphan_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=202, process_start_time="start-202"), verdict="pass"
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())
    store.set_hub_paused("r1", paused=True, at=_NOW)  # mirrors what PULL would mirror
    assert store.hub_paused("r1") is True
    assert store.local_paused("r1") is False

    Reap(ctx).run()

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.lease_id != "lease_1"  # a fresh lease replaced the orphan
    assert lease.pid == 202


def test_reap_orphan_at_exhausted_retries_defers_escalation_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    """Issue #45 review: REAP's orphan case reaches `Attempt.fail` even while paused,
    and an exhausted budget lands on the escalate branch, where the deferral lives."""
    store = _store(tmp_path)
    _seed_orphan_lease(store, retries_max=0)  # exhausted on the very first attempt
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=202, process_start_time="start-202"), verdict="pass"
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())
    _pause_locally(store, ctx, paused=True)

    Reap(ctx).run()

    # No closure, no escalation: the orphan lease waits exactly as it was, its retry
    # budget unmoved.
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid is None
    assert store.attempt_count("ch_1", "nd_build") == 1
    assert [f for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED] == []

    # Unpause; the next REAP escalates it exactly as it would have.
    _pause_locally(store, ctx, paused=False)
    Reap(ctx).run()

    assert store.active_lease("lease_1") is None  # closed — escalated, not requeued
    assert [f for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED]


def test_reap_at_exhausted_retries_does_not_escalate_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)  # retries_max=2
    provider = FakeProvider({"e1": "/ws/e1"})
    # Two verdict-less exits requeue in place, leaving attempt 3 with the retry budget
    # exhausted: REAP reaping it next would ordinarily escalate.
    for i in range(1, 3):
        handle = WorkerHandle(session_id=f"sess-{i}", pid=300 + i, process_start_time=f"start-{i}")
        harness = FakeHarness(handle=handle, verdict=None)
        ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())
        if i == 1:
            _seed_running_lease(store, pid=300, start="start-0")
        Advance(ctx).run()

    exhausted = store.active_lease_for_chunk("ch_1")
    assert exhausted is not None and exhausted.pid is not None and exhausted.process_start_time is not None
    store.record_heartbeat(lease_id=exhausted.lease_id, beat_at=_NOW)
    later = _NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(minutes=5)
    probe = FakeProbe(alive={(exhausted.pid, exhausted.process_start_time)})
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=probe,
        clock=FixedClock(later),
    )
    _pause_locally(store, ctx, paused=True)

    with capture_logs() as logs:
        Reap(ctx).run()

    assert probe.killed == []  # reap never reached this lease — no best-effort kill either
    survivor = store.active_lease_for_chunk("ch_1")
    assert survivor is not None and survivor.lease_id == exhausted.lease_id
    assert store.attempt_count("ch_1", "nd_build") == 3  # unmoved — no requeue, no escalation
    assert [f for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED] == []

    # The deferral is not silent (issue #45 review) — one line, naming the runner and how
    # many leases it held off on this tick.
    deferred = [entry for entry in logs if entry["event"] == "reap deferred — locally paused"]
    assert len(deferred) == 1
    assert deferred[0]["runner_id"] == "r1"
    assert deferred[0]["count"] == 1


# The whole tick, not hand-picked steps (issue #45 review): a bug in the hand-off
# between two steps that are each green alone needs the composed pass to surface.


def test_full_tick_while_locally_paused_spawns_no_process_by_any_path(tmp_path):  # type: ignore[no-untyped-def]
    """Driven as a full tick (REAP -> RESUME -> PULL -> FILL -> ADVANCE), not hand-picked
    steps. Hub brake off: the local brake alone must stop all four spawn primitives."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(store).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.paused = False  # the hub's brake is off — the local brake is the only one on
    hub.chunks["ch_1"] = _running_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    # A claimable chunk in the queue too, so FILL's own gate is exercised by the same pass
    # (capacity is not what stops it: max_agents=2 with one lease held).
    hub.queue = [QueuePeekEntry(chunk_id="ch_2", graph_id="gr_1", position=0)]
    ch_2_env = make_envelope("ch_2", "build", node_id="nd_build", choices=_CHOICES)
    hub.claim_outcome = claimed_outcome("ch_2", ch_2_env)
    hub.envelopes["ch_2"] = ch_2_env
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    # The restart-stranded worker (pid 100) is dead — RESUME's real precondition, and the
    # exact shape ADVANCE's exited-worker judge selects on.
    probe = FakeProbe()
    ctx = make_context(
        store,
        hub=hub,
        # A second free env, so nothing but the brake can explain FILL not claiming ch_2.
        provider=FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"}),
        harness=harness,
        probe=probe,
        config=LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=2),
    )
    _pause_locally(store, ctx, paused=True)

    tick(ctx)

    # No harness process started by ANY path — fresh spawn, restart-resume, answer-resume,
    # or the judgement resume.
    assert harness.spawns == []
    assert harness.judged == []
    assert harness.resumed == []
    assert probe.killed == []  # and nothing killed: a pause is not a drain
    # The chunk does not transition on a phantom verdict, and the session is not consumed.
    assert hub.completions == []
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    assert [f for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED] == []
    assert store.attempt_count("ch_1", "nd_build") == 1  # unmoved — no retry burned
    # The lease is left exactly as it was, still RESUME's to own.
    assert store.resume_intent_lease_ids() == {"lease_1"}
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100 and lease.session_id == "sess-a"
    assert hub.claims == []  # FILL claimed nothing either

    # Unpause; the very next full tick re-drives all of it — RESUME re-attaches the marked
    # lease in place and FILL claims the waiting chunk. Nothing was lost, only deferred.
    _pause_locally(store, ctx, paused=False)
    tick(ctx)

    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The supervisor restarted; continue your task where you left off.")
    ]
    assert store.resume_intent_lease_ids() == set()
    resumed = store.active_lease("lease_1")
    assert resumed is not None and resumed.pid == 4321
    assert len(hub.claims) == 1  # FILL is claiming again


def test_advance_does_not_judge_a_lease_resume_left_open_after_a_hub_blip(tmp_path):  # type: ignore[no-untyped-def]
    """ADVANCE's resume-intent skip is a general correctness rule, not a pause artifact,
    proven here with no pause anywhere: a transient hub blip leaves an intent open on a
    dead-pid lease that only the resume-intent skip stops from being judged."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(store).mark_graceful(now=_NOW)

    hub = _BlipOnceHub()
    hub.chunks["ch_1"] = _running_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    probe = FakeProbe()  # pid 100 dead — RESUME's precondition
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    # Deliberately no pause of either kind.

    Resume(ctx).run()

    # The blip hit the ownership check: nothing resumed, the intent stays open.
    assert harness.resumed == []
    assert store.resume_intent_lease_ids() == {"lease_1"}

    Advance(ctx).run()

    # The hub is reachable again, so ADVANCE's own envelope fetch succeeds — the skip, not
    # a hub error, is what has to stop the judgement here.
    assert harness.judged == []
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    assert store.attempt_count("ch_1", "nd_build") == 1  # no verdict-less failure either
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100

    # The next tick's RESUME re-asks the same open intent and re-attaches it in place.
    Resume(ctx).run()

    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The supervisor restarted; continue your task where you left off.")
    ]
    assert store.resume_intent_lease_ids() == set()


def test_pull_rejection_at_exhausted_retries_defers_escalation_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    """The escalate gate's third `Attempt.fail` caller (issue #45): a completion
    buffered just before pause flushes during it, the hub rejects it as stale, and the
    exhausted budget must not open the escalate door while paused."""
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=0,  # exhausted on the first attempt — a rejection escalates
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()  # still ours — not the reassigned/detached branch
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    # Two rejections: one for the flush during the pause, one for the flush after it.
    hub.apply_responses = [
        ApplyResponse(outcome=ApplyOutcome.FAILURE, detail="stale epoch — fenced"),
        ApplyResponse(outcome=ApplyOutcome.FAILURE, detail="stale epoch — fenced"),
    ]
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Advance(ctx).run()  # the worker exited; judged, completion buffered (not paused yet)
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"]

    _pause_locally(store, ctx, paused=True)
    Pull(ctx).run()  # flushes it; the hub rejects; the exhausted budget reaches the escalate branch

    # The one-way door stayed shut: nothing handed to a human, the lease left open.
    assert [f for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED] == []
    assert hub.escalations == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.lease_id == "lease_1"  # not closed

    # Unpause; the deferral self-drives to the same end — ADVANCE re-judges the still-exited
    # worker, PULL re-flushes, the hub rejects again, and this time it escalates.
    _pause_locally(store, ctx, paused=False)
    Advance(ctx).run()
    Pull(ctx).run()

    assert [f for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED]
    assert store.active_lease("lease_1") is None  # closed — escalated


# Two brakes at once (issue #46 row 14): the runner's own, and the hub's per-chunk pause.
# --------------------------------------------------------------------------- #


def test_a_chunk_paused_on_a_locally_paused_runner_resumes_for_neither_brake_alone(tmp_path):  # type: ignore[no-untyped-def]
    """The two brakes are independent authorities (issue #46): the chunk resumes only
    when both clear. `_kill_and_park_paused` is ungated (a kill is not a spawn);
    `_resume_if_unpaused` is gated, since its resume is a real spawn primitive."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    probe = FakeProbe(alive={(100, "start-100")})  # a live worker for the pause to kill
    hub = FakeHub()
    hub.paused = False  # the hub's *runner* brake (D-043) is off — not the lever under test
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.PAUSED,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id="r1", workspace_id="ws1", environment_ids=["e1"]),
        pause=PauseView(by="operator", set_at="2026-07-13T12:00:00Z"),
    )
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    _pause_locally(store, ctx, paused=True)

    tick(ctx)

    # The hub's instruction about this chunk is honored despite the local brake: killed, parked.
    assert probe.killed == [100]
    assert store.pause_parked_lease_ids() == {"lease_1"}
    assert store.active_lease("lease_1") is not None  # and the claim is kept, as ever

    # Brake 1 clears: the operator resumes the CHUNK, but the runner is still locally paused.
    hub.chunks["ch_1"] = _running_chunk()
    tick(ctx)

    assert harness.resumed == []  # the local brake still forbids the spawn
    # The gate sits above the fact: a suppressed resume writes nothing, so the park stays open
    # and the next tick re-asks it cleanly.
    assert store.pause_parked_lease_ids() == {"lease_1"}

    # The other order proves independence rather than luck: re-pause the chunk, clear the LOCAL
    # brake instead, and it must still not resume.
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.PAUSED,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id="r1", workspace_id="ws1", environment_ids=["e1"]),
        pause=PauseView(by="operator", set_at="2026-07-13T12:05:00Z"),
    )
    _pause_locally(store, ctx, paused=False)
    tick(ctx)

    assert harness.resumed == []  # the chunk's own pause forbids it now
    assert store.pause_parked_lease_ids() == {"lease_1"}

    # Both clear: the session resumes in place — same lease, epoch and session, no retry burned.
    hub.chunks["ch_1"] = _running_chunk()
    tick(ctx)

    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The operator resumed this chunk; continue your task where you left off.")
    ]
    assert store.pause_parked_lease_ids() == set()
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.epoch == 1 and lease.session_id == "sess-a" and lease.pid == 4321
    assert store.attempt_count("ch_1", "nd_build") == 1


# The hub backstops the advisory brake with an outright claim denial (issue #44).
# --------------------------------------------------------------------------- #


def test_fill_stops_on_hub_denial_in_the_tick_window_race(tmp_path):  # type: ignore[no-untyped-def]
    """The tick-window gap issue #44 closes: the hub pauses after PULL last mirrored
    ``paused=False`` but before FILL's claim lands, and the hub refuses it anyway."""
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=False)
    Pull(ctx).run()  # mirrors paused=False — the runner has not yet observed the pause
    assert store.hub_paused("r1") is False

    # The pause lands at the hub in the window between this PULL and FILL's claim.
    hub.claim_outcome = RouteClaimOutcome(denied_paused=RouteClaimPausedDenial(chunk_id="ch_1", runner_id="r1"))

    Fill(ctx).run()

    assert len(hub.claims) == 1  # local cache said "go" — the claim was actually attempted
    assert store.list_active_leases() == []  # but the hub refused it
    assert store.held_environment_ids() == []  # the binding was released, not left dangling


def test_fill_denial_logs_distinctly_from_a_race_conflict(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, _store = _ctx_with_a_claimable_chunk(tmp_path, paused=False)
    Pull(ctx).run()
    hub.claim_outcome = RouteClaimOutcome(denied_paused=RouteClaimPausedDenial(chunk_id="ch_1", runner_id="r1"))

    with capture_logs() as logs:
        Fill(ctx).run()

    denied = [e for e in logs if e["event"] == "route claim denied — runner paused at the hub"]
    lost_race = [e for e in logs if e["event"] == "route claim lost the race"]
    assert len(denied) == 1
    assert denied[0]["chunk_id"] == "ch_1"
    assert denied[0]["runner_id"] == "r1"
    assert lost_race == []  # the two outcomes are logged legibly apart, not conflated


# Runner spend ceiling (issue #61b): the tick-level kill-switch over the same local brake.
# --------------------------------------------------------------------------- #


def _ceiling_config(cap, *, window_hours=24.0, max_agents=1):  # type: ignore[no-untyped-def]
    return LoopConfig(
        runner_id="r1",
        workspace_id="ws1",
        max_agents=max_agents,
        runner_ceiling_usd=cap,
        runner_ceiling_window_hours=window_hours,
    )


def _sample(*, cost, kind: UsageKind = "spawn"):  # type: ignore[no-untyped-def]
    return UsageSample(
        kind=kind,
        model="claude-x",
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=cost,
    )


def _record_usage(store, *, lease_id="lease_1", chunk_id="ch_1", cost, recorded_at):  # type: ignore[no-untyped-def]
    store.record_usage(
        lease_id=lease_id,
        chunk_id=chunk_id,
        node_id="nd_build",
        epoch=1,
        generation=1,
        sample=_sample(cost=cost),
        recorded_at=recorded_at,
    )


@pytest.mark.unit
def test_ceiling_crossing_engages_the_local_brake_and_logs_ceiling_and_spend(tmp_path):  # type: ignore[no-untyped-def]
    """Crossing `runner_ceiling_usd` engages the SAME local pause brake `blizzard runner
    pause` sets, and the log line names both the configured ceiling and the spend that
    tripped it (the escalation the plan calls for — issue #61b)."""
    store = _store(tmp_path)
    _record_usage(store, cost=7.0, recorded_at=_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=_ceiling_config(5.0),
    )

    assert store.local_paused("r1") is False
    with capture_logs() as logs:
        SpendCeiling(ctx).run()

    assert store.local_paused("r1") is True
    reports = [f for f in store.pending_outbound() if f.kind == RUNNER_LOCALLY_PAUSED]
    assert len(reports) == 1
    payload = json.loads(reports[0].payload)
    assert payload["by"] == "runner-ceiling"
    assert "5.00" in payload["reason"] and "7.00" in payload["reason"]
    warnings = [e for e in logs if "runner locally paused" in e["event"]]
    assert len(warnings) == 1
    assert warnings[0]["ceiling_usd"] == 5.0
    assert warnings[0]["spend_usd"] == 7.0


@pytest.mark.unit
def test_ceiling_absent_never_engages_regardless_of_spend(tmp_path):  # type: ignore[no-untyped-def]
    """`runner_ceiling_usd` unset (today's default) never engages the brake, however much
    this runner has spent — mirroring the per-chunk cap's identical absent-means-off rule."""
    store = _store(tmp_path)
    _record_usage(store, cost=9999.0, recorded_at=_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    SpendCeiling(ctx).run()

    assert store.local_paused("r1") is False
    assert [f for f in store.pending_outbound() if f.kind == RUNNER_LOCALLY_PAUSED] == []


@pytest.mark.unit
def test_ceiling_under_cap_does_not_engage(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _record_usage(store, cost=1.0, recorded_at=_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=_ceiling_config(5.0),
    )

    SpendCeiling(ctx).run()

    assert store.local_paused("r1") is False


@pytest.mark.unit
def test_ceiling_partial_total_trips_the_lower_bound_and_flags_partial(tmp_path):  # type: ignore[no-untyped-def]
    """A cost-absent row contributes tokens but $0 to the cost sum; the ceiling trips
    on that lower bound, and both the log line and report say partial (issue #61)."""
    store = _store(tmp_path)
    _record_usage(store, cost=None, recorded_at=_NOW)  # $0 lower bound, cost_partial=True
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=_ceiling_config(0.0),  # any spend at all trips it
    )

    with capture_logs() as logs:
        SpendCeiling(ctx).run()

    assert store.local_paused("r1") is True
    warnings = [e for e in logs if "runner locally paused" in e["event"]]
    assert warnings[0]["cost_partial"] is True
    assert "PARTIAL" in warnings[0]["event"]
    reports = [f for f in store.pending_outbound() if f.kind == RUNNER_LOCALLY_PAUSED]
    assert "PARTIAL" in json.loads(reports[0].payload)["reason"]


@pytest.mark.unit
def test_ceiling_engages_once_no_thrash_on_later_ticks(tmp_path):  # type: ignore[no-untyped-def]
    """Once engaged, a later tick's check must neither re-engage nor re-log — engaging is a
    one-time transition, not a per-tick assertion, even while the window's sum stays over
    the ceiling for as long as it holds (issue #61b's engage-once requirement)."""
    store = _store(tmp_path)
    _record_usage(store, cost=7.0, recorded_at=_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=_ceiling_config(5.0),
    )

    SpendCeiling(ctx).run()
    assert store.local_paused("r1") is True
    assert len([f for f in store.pending_outbound() if f.kind == RUNNER_LOCALLY_PAUSED]) == 1

    with capture_logs() as logs:
        SpendCeiling(ctx).run()  # a second, and later a third, tick's check
        SpendCeiling(ctx).run()

    assert [e for e in logs if "runner locally paused" in e["event"]] == []  # not re-logged
    # Still exactly one pause report ever buffered — no re-engage fact either.
    assert len([f for f in store.pending_outbound() if f.kind == RUNNER_LOCALLY_PAUSED]) == 1


@pytest.mark.unit
def test_ceiling_does_not_auto_lift_when_the_window_rolls_the_spend_back_under_cap(tmp_path):  # type: ignore[no-untyped-def]
    """Once engaged, the brake stays engaged even after the rolling window later excludes
    the very usage fact that tripped it (the sum genuinely drops back under the cap) —
    `blizzard runner start` is the ONLY conscious clear (issue #61's locked design)."""
    store = _store(tmp_path)
    _record_usage(store, cost=7.0, recorded_at=_NOW)
    clock = FixedClock(_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=clock,
        config=_ceiling_config(5.0, window_hours=1.0),
    )

    SpendCeiling(ctx).run()
    assert store.local_paused("r1") is True

    # Move the clock two hours on — the 1h window now excludes the tripping fact entirely,
    # so a fresh, unpaused check of the same config would find $0 spend, well under cap.
    clock.advance(timedelta(hours=2))
    assert store.usage_since(clock.now() - timedelta(hours=1)).cost_usd == 0.0  # confirms the rollover

    SpendCeiling(ctx).run()

    assert store.local_paused("r1") is True  # still engaged — nothing lifts it automatically


@pytest.mark.unit
def test_ceiling_engaged_defers_reap_kill_and_suppresses_fill_in_the_same_tick(tmp_path):  # type: ignore[no-untyped-def]
    """Driven as a full tick: a live worker whose runner just crossed its ceiling is left
    running untouched, and a second, otherwise-claimable chunk is not spawned into either."""
    store = _store(tmp_path)
    _seed_running_lease(store)  # lease_1 / ch_1, a live worker (pid 100)
    _record_usage(store, cost=7.0, recorded_at=_NOW)
    hub = FakeHub()
    hub.paused = False
    hub.chunks["ch_1"] = _running_chunk()
    hub.queue = [QueuePeekEntry(chunk_id="ch_2", graph_id="gr_1", position=0)]
    ch_2_env = make_envelope("ch_2", "build", node_id="nd_build", choices=_CHOICES)
    hub.claim_outcome = claimed_outcome("ch_2", ch_2_env)
    hub.envelopes["ch_2"] = ch_2_env
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    probe = FakeProbe(alive={(100, "start-100")})  # the ch_1 worker is still running
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"}),
        harness=harness,
        probe=probe,
        clock=FixedClock(_NOW),
        config=_ceiling_config(5.0, max_agents=2),
    )

    tick(ctx)

    assert store.local_paused("r1") is True  # the ceiling engaged this very tick
    assert probe.killed == []  # not a drain — the live worker was left alone
    assert harness.spawns == []  # and nothing new was spawned into the free second env either
    assert store.attempt_count("ch_1", "nd_build") == 1  # no retry consumed
    assert hub.claims == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100  # untouched


@pytest.mark.unit
def test_runner_start_clears_the_ceiling_brake_exactly_like_a_manual_pause(tmp_path):  # type: ignore[no-untyped-def]
    """`blizzard runner start` clears a ceiling-engaged brake exactly as it clears an
    operator's own pause, with no ceiling-specific code path."""
    store = _store(tmp_path)
    _record_usage(store, cost=7.0, recorded_at=_NOW)
    clock = FixedClock(_NOW)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.claim_outcome = claimed_outcome("ch_1", env)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=clock,
        config=_ceiling_config(5.0, window_hours=1.0),
    )

    SpendCeiling(ctx).run()
    assert store.local_paused("r1") is True
    Fill(ctx).run()
    assert hub.claims == []  # suppressed while engaged

    # `blizzard runner start` — the exact `record_local_pause(paused=False)` write
    # `PATCH /api/runner` makes, with no ceiling-aware code anywhere in that path.
    _pause_locally(store, ctx, paused=False)
    assert store.local_paused("r1") is False

    # The window has since moved past the tripping fact, so a fresh ceiling check does not
    # immediately re-engage the brake out from under the operator's own clear.
    clock.advance(timedelta(hours=2))
    SpendCeiling(ctx).run()
    assert store.local_paused("r1") is False

    Fill(ctx).run()

    assert store.local_paused("r1") is False
    assert len(hub.claims) == 1  # FILL claims again — work resumed
