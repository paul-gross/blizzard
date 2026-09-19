"""Transcript invocation boundaries (blizzard#437 D6/D11, component tier).

A real FILL/RESUME/ADVANCE tick against a tmp store, proving each of the four fleet-driven
invocations — spawn, resume, judge, nudge — opens its own durable boundary before it
launches, and that closing a lease closes every boundary it opened."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.transcript import TranscriptPosition
from blizzard.runner.loop.attempt import Attempt
from blizzard.runner.loop.spawn import Spawner
from blizzard.runner.loop.steps import Advance, Fill, Resume, ResumeIntents
from blizzard.wire.chunk import ChunkStatusView
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekEntry
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    FakeWorktreeGit,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
    make_stores,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 18, 12, 0, 0, tzinfo=UTC)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path: Path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_exited_lease(store, *, lease="lease_r", chunk="ch_1", node="nd_review", epoch=1, session="sess-a"):  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id=node,
            node_name="review",
            epoch=epoch,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease,
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, session),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def test_fresh_spawn_opens_a_spawn_boundary_at_generation_one(tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="pass"
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    Fill(ctx).run()

    lease = store.list_active_leases()[0]
    boundary = ctx.stores.invocation_boundaries.boundary(lease.lease_id, 1, "spawn")
    assert boundary is not None
    assert boundary.kind == "spawn"
    assert boundary.generation == 1
    # No session existed yet when this boundary opened — the beginning sentinel, never a read.
    assert boundary.start_position is None
    assert boundary.closed_at is None


def test_graceful_restart_resume_opens_a_resume_boundary_from_the_tail(tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store, node="nd_build")
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkStatusView(
        chunk_id="ch_1", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1"
    )
    tail = TranscriptPosition(token='{"main": 4096, "sidecars": {}}')
    transcript_source = FakeTranscriptSource(tail_positions_by_session={"sess-a": tail})
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100),
        verdict="pass",
        transcript_source=transcript_source,
    )
    harness.resume_pid = 4321
    probe = FakeProbe(alive={(100, "start-100"), (4321, "start-4321")})
    ctx = make_context(
        store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe, clock=FixedClock(_NOW)
    )

    Resume(ctx).run()

    boundary = ctx.stores.invocation_boundaries.boundary("lease_r", 2, "resume")
    assert boundary is not None
    assert boundary.kind == "resume"
    assert boundary.start_position == tail.token
    assert ctx.stores.invocation_boundaries.boundary("lease_r", 2, "nudge") is None


def test_nudge_opens_a_nudge_boundary_not_a_resume_boundary(tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"]
    )
    tail = TranscriptPosition(token='{"main": 128, "sidecars": {}}')
    transcript_source = FakeTranscriptSource(tail_positions_by_session={"sess-a": tail})
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100),
        verdict=None,
        assessment="",
        transcript_source=transcript_source,
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit(),
        clock=FixedClock(_NOW),
    )

    Advance(ctx).run()

    # The upcoming resume is generation 2 (the lease's own generation 1 spawn already exists).
    nudge_boundary = ctx.stores.invocation_boundaries.boundary("lease_r", 2, "nudge")
    assert nudge_boundary is not None
    assert nudge_boundary.start_position == tail.token
    # D5: the nudge's own wake must not also open a plain `resume` boundary at that generation.
    assert ctx.stores.invocation_boundaries.boundary("lease_r", 2, "resume") is None


def test_judgement_launch_opens_a_judge_boundary_at_the_current_generation(tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store, node="nd_build")

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    tail = TranscriptPosition(token='{"main": 256, "sidecars": {}}')
    transcript_source = FakeTranscriptSource(tail_positions_by_session={"sess-a": tail})
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100),
        verdict="pass",
        assessment="fine",
        transcript_source=transcript_source,
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit(),
        clock=FixedClock(_NOW),
    )

    Advance(ctx).run()  # launches the detached elicitation

    boundary = ctx.stores.invocation_boundaries.boundary("lease_r", 1, "judge")
    assert boundary is not None
    assert boundary.kind == "judge"
    assert boundary.start_position == tail.token


def test_closing_a_lease_closes_every_boundary_it_opened(tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store, node="nd_build")
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(
            handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="pass"
        ),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )
    ctx.stores.invocation_boundaries.record_boundary_open(
        lease_id="lease_r",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        kind="spawn",
        start_position=None,
        opened_at=_NOW,
    )
    lease = store.active_lease("lease_r")
    assert lease is not None

    Attempt(ctx, lease).abandon(via="test")

    assert ctx.stores.invocation_boundaries.open_boundaries_for_lease("lease_r") == []
    boundary = ctx.stores.invocation_boundaries.boundary("lease_r", 1, "spawn")
    assert boundary is not None
    assert boundary.closed_at is not None
    assert boundary.closed_reason == "released"


def test_a_resumed_existing_session_spawn_opens_a_resume_boundary_from_its_own_tail(  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    """`Spawner.spawn`'s own ``resume_from`` branch (`enter_node`'s resume, not a fresh mint)
    opens a `resume` boundary reading that session's own tail, never the fresh-spawn
    sentinel (blizzard#437 F1)."""
    store = _store(tmp_path)
    tail = TranscriptPosition(token='{"main": 777, "sidecars": {}}')
    transcript_source = FakeTranscriptSource(tail_positions_by_session={"sess-prior": tail})
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-prior", pid=100, process_start_time="start-100", pgid=100),
        verdict="pass",
        transcript_source=transcript_source,
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    Spawner(ctx).spawn(
        "ch_1",
        make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES),
        [AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        via="test",
        resume_from=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-prior"),
    )

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    boundary = ctx.stores.invocation_boundaries.boundary(lease.lease_id, 1, "resume")
    assert boundary is not None
    assert boundary.start_position == tail.token
    assert boundary.start_unreadable is False
    # Never ALSO a `spawn` boundary at the same generation (D5's exclusivity).
    assert ctx.stores.invocation_boundaries.boundary(lease.lease_id, 1, "spawn") is None


def test_a_resumed_existing_session_spawn_with_an_unreadable_tail_marks_it_unreadable(  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    """The companion case: the resumed session's tail read fails (unscripted here) — the
    boundary still opens, but flagged `start_unreadable`, never a silent fresh-session sentinel."""
    store = _store(tmp_path)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-prior", pid=100, process_start_time="start-100", pgid=100),
        verdict="pass",
        transcript_source=FakeTranscriptSource(),  # no tail scripted for "sess-prior"
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    Spawner(ctx).spawn(
        "ch_1",
        make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES),
        [AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")],
        via="test",
        resume_from=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-prior"),
    )

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    boundary = ctx.stores.invocation_boundaries.boundary(lease.lease_id, 1, "resume")
    assert boundary is not None
    assert boundary.start_position is None
    assert boundary.start_unreadable is True


def test_a_stranded_nudge_boundary_blocks_a_later_unrelated_wakes_own_resume_boundary(  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    """A nudge boundary can open and then strand (its own `_wake` never ran) — a LATER,
    unrelated wake reaching the same lease must self-determine a boundary is already open at
    that generation, never opening a second `resume` one (blizzard#437 F6)."""
    store = _store(tmp_path)
    _seed_exited_lease(store)  # generation 1's own spawn; node defaults to "nd_review"
    # The stranded nudge: its own boundary opened at generation 2, but its `_wake` never ran.
    store.record_boundary_open(
        lease_id="lease_r",
        chunk_id="ch_1",
        node_id="nd_review",
        epoch=1,
        generation=2,
        kind="nudge",
        start_position="tail-at-nudge",
        opened_at=_NOW,
    )
    store.record_ask(
        lease_id="lease_r",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Q",
        options=[],
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_r", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    hub = FakeHub()
    hub.questions["qn_1"] = QuestionView(
        question_id="qn_1",
        chunk_id="ch_1",
        runner_id="r1",
        epoch=1,
        question="Q",
        asked_at="t",
        answered=True,
        answer="go",
        answered_by="alice",
        answered_at="t2",
    )
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="pass"
    )
    harness.resume_pid = 4321
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    Advance(ctx).run()  # the parked lease's `on_answer` wakes it

    # The unrelated wake resumed the session (so the fix does not merely block the wake)...
    assert harness.resumed == [("/ws/e1", "sess-a", "# Answer from alice. Continue.\ngo")]
    # ...but opened no second `resume` boundary at the nudge's own generation.
    assert ctx.stores.invocation_boundaries.boundary("lease_r", 2, "resume") is None
    nudge_boundary = ctx.stores.invocation_boundaries.boundary("lease_r", 2, "nudge")
    assert nudge_boundary is not None
    assert nudge_boundary.start_position == "tail-at-nudge"  # untouched by the later wake
