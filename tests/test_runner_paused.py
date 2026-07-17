"""The runner adheres to the hub's pause brake — loop component tier.

The declarative pause brake lives at the hub; the runner reads it on PULL (a
``GET /runners/{id}`` behind the hub client), mirrors it to its store, and FILL adheres:
paused = no new claims, in-flight chunks run on. When the hub is unreachable the runner
keeps its last-mirrored directive. Driven directly against a real (tmp sqlite)
runner store with :class:`FakeHub`/:class:`FakeHarness`/:class:`FakeProvider`/
:class:`FakeProbe` standing in only at the seams — a domain slice over real internal
collaborators, the harness matrix's component definition, not its one-function unit one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.hub import HubClientError
from blizzard.runner.loop.steps import advance, fill, mark_resume_intents, pull, reap, resume
from blizzard.runner.loop.tick import tick
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, RouteView
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
    """A :class:`FakeHub` whose **first** ``get_chunk`` raises, then serves normally.

    `FakeHub.down` is all-or-nothing, which cannot express the interleaving that matters
    here: RESUME's ownership check failing while ADVANCE's envelope fetch, a moment later
    in the same tick, succeeds. That transient blip is the non-paused way RESUME leaves an
    intent open, so it needs a hub that recovers between two calls rather than one that is
    down for both.
    """

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
    pull(ctx)
    # PULL registered the runner (liveness heartbeat) and mirrored the brake locally.
    assert hub.registered == [("r1", "ws1")]
    assert store.hub_paused("r1") is True


def test_fill_claims_nothing_while_paused(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    pull(ctx)  # mirror paused=True
    fill(ctx)
    # No claim was attempted and no lease was minted — the queue is untouched.
    assert hub.claims == []
    assert store.list_active_leases() == []


def test_fill_claims_again_after_resume(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    pull(ctx)
    fill(ctx)
    assert store.list_active_leases() == []

    # The operator resumes the runner; the next PULL mirrors it and FILL claims.
    hub.paused = False
    pull(ctx)
    fill(ctx)
    assert len(hub.claims) == 1
    assert len(store.list_active_leases()) == 1


def test_in_flight_chunk_runs_on_while_paused(tmp_path):  # type: ignore[no-untyped-def]
    """Pausing stops new claims; an already-claimed chunk is untouched by FILL."""
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=False)
    pull(ctx)
    fill(ctx)  # claims ch_1
    assert len(store.list_active_leases()) == 1

    # Now pause; FILL must not tear down or re-claim — the in-flight lease persists.
    hub.paused = True
    pull(ctx)
    fill(ctx)
    assert len(store.list_active_leases()) == 1


def test_unreachable_hub_keeps_last_mirrored_brake(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    pull(ctx)  # mirror paused=True
    assert store.hub_paused("r1") is True

    # The hub goes unreachable; PULL cannot refresh, so the last-known brake holds.
    hub.down = True
    pull(ctx)
    assert store.hub_paused("r1") is True
    fill(ctx)
    assert hub.claims == []  # still adhering to the last directive


# --------------------------------------------------------------------------- #
# The runner's own brake (issue #43) — a second, independent surface
# --------------------------------------------------------------------------- #
#
# The local brake is the runner declining to claim ("I won't try"), set through its own
# local API and needing no hub. The hub's brake coerces it from the fleet side. Effective
# paused is the OR, and each is cleared only where it was set — so these assert both the
# gating and the independence.


def test_fill_claims_nothing_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=False)
    pull(ctx)  # the hub's brake is off — only the local one stops this claim
    _pause_locally(store, ctx, paused=True)
    fill(ctx)
    assert hub.claims == []
    assert store.list_active_leases() == []


def test_fill_claims_again_after_a_local_start(tmp_path):  # type: ignore[no-untyped-def]
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=False)
    pull(ctx)
    _pause_locally(store, ctx, paused=True)
    fill(ctx)
    assert hub.claims == []

    # Facts append and the flag derives from the newest — no row is mutated.
    _pause_locally(store, ctx, paused=False)
    fill(ctx)
    assert len(hub.claims) == 1
    assert len(store.list_active_leases()) == 1


def test_a_local_start_does_not_clear_the_hubs_brake(tmp_path):  # type: ignore[no-untyped-def]
    """Each brake is cleared only on the surface that set it — the OR still holds."""
    ctx, hub, store = _ctx_with_a_claimable_chunk(tmp_path, paused=True)
    pull(ctx)  # mirror the hub's brake on
    _pause_locally(store, ctx, paused=False)
    fill(ctx)
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
    pull(ctx)
    fill(ctx)
    assert len(store.list_active_leases()) == 1

    _pause_locally(store, ctx, paused=True)
    fill(ctx)
    assert len(store.list_active_leases()) == 1  # untouched — only new claims stop
    assert probe.killed == []  # a live worker already running is not killed


# --------------------------------------------------------------------------- #
# The local brake reaches every spawn site, not just FILL's claim (issue #45)
# --------------------------------------------------------------------------- #
#
# The hub brake keeps its D-043 claims-only meaning — checked in FILL alone. The local
# brake also blocks restart-resume, an answer-resume, and every ``_spawn_attempt`` caller
# (ADVANCE's next-node, a requeue, a claim-adopt/reclaim) via the shared
# ``_spawn_suppressed`` gate, always reached before its primitive's first mutation.


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
    mark_resume_intents(store, now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    # RESUME's precondition is a lease whose worker is *dead* (mark_crash_resume_intents
    # only marks a dead pid; the graceful marker's own worker dies on the way down too) —
    # pid 100 is not in the alive set, matching what RESUME actually recovers.
    probe = FakeProbe(alive={(4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    _pause_locally(store, ctx, paused=True)

    resume(ctx)

    # Suppressed before the kill: no survivor killed, no resume delivered, the intent stays open.
    assert probe.killed == []
    assert harness.resumed == []
    assert store.resume_intent_lease_ids() == {"lease_1"}
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100  # untouched

    # Unpause; RESUME re-asks the same open intent and resumes it.
    _pause_locally(store, ctx, paused=False)
    resume(ctx)

    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The supervisor restarted; continue your task where you left off.")
    ]
    assert store.resume_intent_lease_ids() == set()


def test_restart_resume_suppressed_then_advance_does_not_judge_or_spawn(tmp_path):  # type: ignore[no-untyped-def]
    """The headline regression (issue #45 review): a suppressed restart-resume must not
    leak the lease to ADVANCE. Left active with a dead pid and an open resume intent, the
    lease is exactly the shape ADVANCE would otherwise read as an exited worker to judge —
    and judging it both spawns a harness process (``ctx.harness.judge`` resumes the
    session) and reads a worker killed mid-work as a done declaration. Drives a
    full tick's worth of steps in RESUME-then-ADVANCE order, the shape a restart under a
    standing local pause actually produces.
    """
    store = _store(tmp_path)
    _seed_running_lease(store)
    mark_resume_intents(store, now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    # The restart-stranded worker (pid 100) is dead — RESUME's precondition. Pid 4321
    # reads alive once resumed, so the post-unpause pass finds a running worker, not
    # another exit to judge.
    probe = FakeProbe(alive={(4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    _pause_locally(store, ctx, paused=True)

    resume(ctx)
    advance(ctx)

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
    resume(ctx)
    advance(ctx)

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

    advance(ctx)

    # Suppressed before the poll: nothing resumed, the park stays open, no answer.delivered.
    assert harness.resumed == []
    assert store.parked_lease_ids() == {"lease_1"}
    assert [f for f in store.pending_outbound() if f.kind == ANSWER_DELIVERED] == []

    # Unpause; ADVANCE re-polls the same open park and resumes it around the answer.
    _pause_locally(store, ctx, paused=False)
    advance(ctx)

    assert harness.resumed == [("/ws/e1", "sess-a", "# Answer from alice. Continue.\nrest")]
    assert store.parked_lease_ids() == set()
    assert [f for f in store.pending_outbound() if f.kind == ANSWER_DELIVERED]


def test_exited_worker_judgement_suppressed_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    """ADVANCE's judgement resume is the fourth spawn primitive (issue #45 review): a
    worker that exits naturally while paused (no resume intent involved at all — it was
    running before the pause and finished during it) must not be judged, because judging
    it resumes its session headlessly. It waits, unjudged, for the brake to clear.
    """
    store = _store(tmp_path)
    _seed_exited_lease(store)  # no resume intent — a plain, already-exited worker

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())
    _pause_locally(store, ctx, paused=True)

    advance(ctx)

    # Suppressed before the judge call: no verdict elicited, no completion buffered, the
    # lease is left exactly as it was.
    assert harness.judged == []
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100

    # Unpause; ADVANCE re-drives the same exited worker and judges it this time.
    _pause_locally(store, ctx, paused=False)
    advance(ctx)

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

    advance(ctx)  # the worker exited (no probe entry) -> buffers the completion
    _pause_locally(store, ctx, paused=True)
    pull(ctx)  # flushes the completion; the apply-response's next-node spawn is suppressed

    # The old attempt still closes normally — only the fresh spawn is suppressed. No new
    # lease is minted, so the chunk is left in the shape of an interrupted claim: a live
    # binding, no active lease.
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
    fill(ctx)

    assert len(harness.spawns) == 1
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "review"


def test_hub_paused_only_restart_resume_still_spawns(tmp_path):  # type: ignore[no-untyped-def]
    """The mirror image: hub brake on, local brake off — restart-resume is unaffected."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    mark_resume_intents(store, now=_NOW)

    hub = FakeHub()
    hub.paused = True
    hub.chunks["ch_1"] = _running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    # Pid 100 is dead — RESUME's actual precondition; 4321 reads alive once resumed.
    probe = FakeProbe(alive={(4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    pull(ctx)  # mirror the hub brake on; the local brake stays untouched
    assert store.hub_paused("r1") is True
    assert store.local_paused("r1") is False

    resume(ctx)

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
    pull(ctx)  # mirror the hub brake on; the local brake stays untouched
    assert store.hub_paused("r1") is True
    assert store.local_paused("r1") is False

    advance(ctx)  # no parseable verdict -> failure -> requeue in place

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.epoch == 2  # a fresh attempt was spawned
    assert store.attempt_count("ch_1", "nd_build") == 2


def test_suppression_logged_once_per_lease_per_tick_per_site(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store, chunk="ch_1", lease="lease_1")
    _seed_running_lease(store, chunk="ch_2", lease="lease_2", pid=101, start="start-101", session="sess-b")
    mark_resume_intents(store, now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = _running_chunk("ch_1")
    hub.chunks["ch_2"] = _running_chunk("ch_2")
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    # Both original workers are dead — RESUME's actual precondition.
    probe = FakeProbe(alive=set())
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    _pause_locally(store, ctx, paused=True)

    with capture_logs() as logs:
        resume(ctx)

    suppressed = [entry for entry in logs if entry["event"] == "spawn suppressed — locally paused"]
    assert len(suppressed) == 2  # one line per lease this tick — no dedupe state, no repeats
    by_lease = {entry["lease_id"]: entry for entry in suppressed}
    assert by_lease.keys() == {"lease_1", "lease_2"}
    for entry in suppressed:
        assert entry["via"] == "resume"
        assert entry["runner_id"] == "r1"
        assert entry["chunk_id"] == by_lease[entry["lease_id"]]["chunk_id"]


# --------------------------------------------------------------------------- #
# REAP's own guard — killing a live worker is deferred, escalation is deferred,
# neither is blanket-suspended (issue #45)
# --------------------------------------------------------------------------- #
#
# REAP ends an attempt (requeue-or-escalate) structurally below the shared
# `_spawn_suppressed` gate — its exhausted-retries branch never calls a spawn
# primitive, so the chokepoint gate cannot see it. REAP's own local_paused check is
# narrower than the gate that once lived at its top: it guards only the stall case's
# kill (a live worker is never killed while paused); the orphan case runs unguarded
# because it has no process to kill and its own `_fail_attempt` call self-defers both
# branches (requeue via the suppressed respawn, escalate via `_fail_attempt`'s own
# local_paused gate — the one home every caller of it shares). These exercise both.


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
    """REAP's orphan case is not suspended by the local brake — it has no process to
    kill, so it reaps and requeues as always; only the requeue's respawn is suppressed
    (the same self-defer every :func:`_spawn_attempt` caller gets). That leaves the chunk
    shaped like an interrupted claim — bound, no active lease — which FILL's reconcile
    pass adopts once the brake clears, exactly as it does for a suppressed apply-response
    spawn."""
    store = _store(tmp_path)
    _seed_orphan_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=202, process_start_time="start-202"), verdict="pass"
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())
    _pause_locally(store, ctx, paused=True)

    reap(ctx)

    # Reaped and requeued as always (REAP itself is not suspended for an orphan) — but the
    # requeue's respawn is suppressed, so no new lease is minted and the retry budget is
    # untouched by construction: the old lease is closed, the chunk holds its binding with
    # no active lease, exactly the interrupted-claim shape.
    assert store.active_lease("lease_1") is None
    assert store.active_lease_for_chunk("ch_1") is None
    assert store.attempt_count("ch_1", "nd_build") == 1
    assert [f for f in store.pending_outbound() if f.kind == LEASE_MINTED] == []
    assert store.held_environment_ids() == ["e1"]

    # Unpause; FILL's reconcile pass sees the same shape a crashed FILL would
    # leave and adopts it — no deferred-spawn state was needed.
    _pause_locally(store, ctx, paused=False)
    hub.chunks["ch_1"] = _running_chunk("ch_1")
    fill(ctx)

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

    reap(ctx)

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.lease_id != "lease_1"  # a fresh lease replaced the orphan
    assert lease.pid == 202


def test_reap_orphan_at_exhausted_retries_defers_escalation_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    """The must-fix-2 scenario (issue #45 review): REAP's orphan case is not blanket-
    suspended — it has no process to kill, so it reaches `_fail_attempt` even while
    paused. At an exhausted budget that lands on the escalate branch, which is where the
    deferral actually lives now — the one gate every `_fail_attempt` caller (REAP,
    ADVANCE, PULL) shares, rather than three separate checks."""
    store = _store(tmp_path)
    _seed_orphan_lease(store, retries_max=0)  # exhausted on the very first attempt
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=202, process_start_time="start-202"), verdict="pass"
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())
    _pause_locally(store, ctx, paused=True)

    reap(ctx)

    # No closure, no escalation — the orphan lease waits exactly as it was, its retry
    # budget unmoved (REAP itself is not suspended for this case; only the escalate
    # branch it reaches is).
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid is None
    assert store.attempt_count("ch_1", "nd_build") == 1
    assert [f for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED] == []

    # Unpause; the next REAP escalates it exactly as it would have.
    _pause_locally(store, ctx, paused=False)
    reap(ctx)

    assert store.active_lease("lease_1") is None  # closed — escalated, not requeued
    assert [f for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED]


def test_reap_at_exhausted_retries_does_not_escalate_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)  # retries_max=2
    provider = FakeProvider({"e1": "/ws/e1"})
    # Two verdict-less exits requeue in place: attempt 1 -> 2, attempt 2 -> 3. The
    # active lease left behind — attempt 3 — has exhausted the retry budget: REAP reaping
    # it next would ordinarily escalate.
    for i in range(1, 3):
        handle = WorkerHandle(session_id=f"sess-{i}", pid=300 + i, process_start_time=f"start-{i}")
        harness = FakeHarness(handle=handle, verdict=None)
        ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())
        if i == 1:
            _seed_running_lease(store, pid=300, start="start-0")
        advance(ctx)

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
        reap(ctx)

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


# --------------------------------------------------------------------------- #
# The whole tick, not hand-picked steps (issue #45 review)
# --------------------------------------------------------------------------- #
#
# Every test above drives the one or two steps it reasons about. That is what let the
# original headline bug through: each step was green in isolation, and the bug lived in
# the *hand-off* between them. These two drive the composed pass instead.


def test_full_tick_while_locally_paused_spawns_no_process_by_any_path(tmp_path):  # type: ignore[no-untyped-def]
    """The headline regression driven as a **full tick** (REAP → RESUME → PULL → FILL →
    ADVANCE), not as the hand-picked RESUME-then-ADVANCE pair. This is the shape a real
    restart under a standing local pause produces, and the composed pass is the only
    driver that can catch a gap in the hand-off between two steps that are each green
    alone — the blind spot the original bug hid in. The hub brake is **off**: the local
    brake alone must stop every one of the four spawn primitives.
    """
    store = _store(tmp_path)
    _seed_running_lease(store)
    mark_resume_intents(store, now=_NOW)

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
    """ADVANCE's resume-intent skip is a **general** correctness rule, not a pause artifact
    — so it is proven here with **no pause anywhere**.

    RESUME leaves an intent open on either of two conditions: the local brake is on, or the
    hub was unreachable for its ownership check (:func:`_resume_marked_lease` returns early
    rather than resuming blind). The second needs no brake at all. A transient blip — the
    hub down for RESUME's ``get_chunk``, back up by ADVANCE's ``get_envelope`` a moment
    later in the same tick — leaves a lease that is active, session-bearing and dead-pid:
    exactly what ADVANCE reads as exited work. Judging it would elicit a verdict from a
    session RESUME never re-attached and read a worker killed mid-work as a done
    declaration. Only the resume-intent skip stops it; the judge's local-brake gate
    cannot, because nothing here is paused.
    """
    store = _store(tmp_path)
    _seed_running_lease(store)
    mark_resume_intents(store, now=_NOW)

    hub = _BlipOnceHub()
    hub.chunks["ch_1"] = _running_chunk()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    probe = FakeProbe()  # pid 100 dead — RESUME's precondition
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    # Deliberately no pause of either kind.

    resume(ctx)

    # The blip hit the ownership check: nothing resumed, the intent stays open.
    assert harness.resumed == []
    assert store.resume_intent_lease_ids() == {"lease_1"}

    advance(ctx)

    # The hub is reachable again, so ADVANCE's own envelope fetch succeeds — the skip, not
    # a hub error, is what has to stop the judgement here.
    assert harness.judged == []
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    assert store.attempt_count("ch_1", "nd_build") == 1  # no verdict-less failure either
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100

    # The next tick's RESUME re-asks the same open intent and re-attaches it in place.
    resume(ctx)

    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The supervisor restarted; continue your task where you left off.")
    ]
    assert store.resume_intent_lease_ids() == set()


def test_pull_rejection_at_exhausted_retries_defers_escalation_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    """The escalate gate's **third** caller (issue #45). ``_fail_attempt`` is shared by
    REAP's orphan case, ADVANCE's verdict-less exit and PULL's flush rejections, and the
    deferral sits in that one shared function — but "shared, so it must hold everywhere"
    is exactly the by-construction reasoning that missed the judgement primitive, so each
    *reachable* caller is proven rather than argued.

    Of the three, ADVANCE's is provably unreachable while locally paused (the judgement
    gate returns before its ``_fail_attempt`` can be reached at all), and REAP's orphan
    case is covered above. This is PULL's: a completion buffered just before the operator
    paused is flushed during the pause, the hub rejects it as stale (a zombie fenced at
    D-007), and the exhausted budget lands it on the escalate branch — the one-way door
    that must not open while the runner is paused.
    """
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

    advance(ctx)  # the worker exited; judged, completion buffered (not paused yet)
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"]

    _pause_locally(store, ctx, paused=True)
    pull(ctx)  # flushes it; the hub rejects; the exhausted budget reaches the escalate branch

    # The one-way door stayed shut: nothing handed to a human, the lease left open.
    assert [f for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED] == []
    assert hub.escalations == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.lease_id == "lease_1"  # not closed

    # Unpause; the deferral self-drives to the same end — ADVANCE re-judges the still-exited
    # worker, PULL re-flushes, the hub rejects again, and this time it escalates.
    _pause_locally(store, ctx, paused=False)
    advance(ctx)
    pull(ctx)

    assert [f for f in store.pending_outbound() if f.kind == ESCALATION_RECORDED]
    assert store.active_lease("lease_1") is None  # closed — escalated
