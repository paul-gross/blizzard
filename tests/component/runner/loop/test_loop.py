"""The reconciliation step functions — the loop logic, component tier.

Each step is driven directly against a real tmp store with fakes at the seams
(``bzh:steppable-loop``): resume sessions, the prompt-layer fingerprint, and the judgement cwd."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import ESCALATION_RECORDED
from blizzard.foundation.node_steps import ApplyOutcome, SessionMode
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.preamble import (
    RESUME_BLIZZARD_UNCHANGED,
    RESUME_STANDING_UNCHANGED,
    RESUME_UPDATED_NOTICE,
    RESUME_WORKSPACE_UNCHANGED,
    resume_cross_node,
)
from blizzard.runner.hub.client import (
    QueueEntry,
)
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import Advance, Fill, Pull, Resume, ResumeIntents
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.node_steps.envelope import Envelope
from blizzard.runner.node_steps.submissions import ApplyReply
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
    make_stores,
)

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path: Path):
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _build_envelope(chunk: str = "ch_1"):
    return make_envelope(chunk, "build", node_id="nd_build", choices=_CHOICES)


def _seed_running_lease(
    store: SqlAlchemyRunnerStore,
    *,
    chunk: str = "ch_1",
    lease: str = "lease_1",
    pid: int = 100,
    start: str = "start-100",
    session: str = "sess-a",
    epoch: int = 1,
):
    """A build lease already spawned into env e1, plus its binding."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
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
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")


# NODE-ENTRY RESUME: session modes across a build -> review -> build cycle
# — component tier, real store, doubles only at the hub/harness/provider/probe seams.


def test_targeted_resume_returns_to_its_own_node_not_the_reviewers_fresh_session(tmp_path: Path):
    """`build` carries `resume:build` (targeted), `review` is `fresh`. Covers three
    session-mode assertions: fresh-node-always-new-sid, first-arrival-falls-back-fresh,
    and build's re-entry resuming its OWN prior session, not the reviewer's (plan Q4)."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})

    build_env = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, session=SessionMode.RESUME, session_source="build"
    )
    review_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES, session=SessionMode.FRESH)

    # (FILL): first arrival at `build` — no session exists yet, so the targeted
    # `resume:build` lookup comes back empty and the spawn falls back to fresh.
    hub.queue = [QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    harness1 = FakeHarness(
        handle=WorkerHandle(session_id="sess-build-1", pid=100, process_start_time="start-100", pgid=100),
        verdict="pass",
    )
    ctx1 = make_context(store, hub=hub, provider=provider, harness=harness1, probe=FakeProbe(), clock=FixedClock(_NOW))
    Fill(ctx1).run()

    assert harness1.resume_froms == [None]  # (e) first arrival falls back to fresh
    build_lease_1 = store.active_lease_for_chunk("ch_1")
    assert build_lease_1 is not None and build_lease_1.session_id == "sess-build-1"

    # --- (ADVANCE + PULL): build passes; apply-response NEXT hands the chunk
    # to `review` (fresh) — always a new sid (assertion c).
    hub.envelopes["ch_1"] = build_env  # `_advance_exited_worker`'s own idempotent re-read
    hub.apply_responses = [
        ApplyReply(outcome=ApplyOutcome.NEXT, next_envelope=review_env),
        ApplyReply(outcome=ApplyOutcome.NEXT, next_envelope=build_env),
    ]
    harness2 = FakeHarness(
        handle=WorkerHandle(session_id="sess-review-1", pid=200, process_start_time="start-200", pgid=200),
        verdict="fail",
    )
    ctx2 = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness2,
        probe=FakeProbe(),
        clock=FixedClock(_NOW + timedelta(minutes=1)),
    )
    Advance(ctx2).run()  # build worker "exited" (empty alive set) -> launches the elicitation
    Advance(ctx2).run()  # collects it -> judged pass -> buffered
    Pull(ctx2).run()  # flush -> apply-response NEXT -> spawn review

    assert harness2.resume_froms == [None]  # (c) fresh review always gets a new sid
    review_lease = store.active_lease_for_chunk("ch_1")
    assert (
        review_lease is not None and review_lease.node_name == "review" and review_lease.session_id == "sess-review-1"
    )

    # (ADVANCE + PULL): review fails; apply-response routes back into build's
    # targeted `resume:build` — must resume BUILD's own session, not the reviewer's.
    hub.envelopes["ch_1"] = review_env  # `_advance_exited_worker`'s own idempotent re-read
    harness3 = FakeHarness(
        handle=WorkerHandle(session_id="sess-should-not-be-used", pid=300, process_start_time="start-300", pgid=300),
        verdict="pass",
    )
    ctx3 = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness3,
        probe=FakeProbe(),
        clock=FixedClock(_NOW + timedelta(minutes=2)),
    )
    Advance(ctx3).run()  # review worker "exited" -> launches the elicitation
    Advance(ctx3).run()  # collects it -> judged fail -> buffered
    Pull(ctx3).run()  # flush -> apply-response NEXT -> spawn build, resuming in place

    assert harness3.resume_froms == ["sess-build-1"]  # (b) resumes build's own session
    build_lease_2 = store.active_lease_for_chunk("ch_1")
    assert (
        build_lease_2 is not None and build_lease_2.node_name == "build" and build_lease_2.session_id == "sess-build-1"
    )


def test_bare_resume_uses_the_chunks_most_recent_session_not_the_nodes_own(tmp_path: Path):
    """The contrast case: a node entered with plain `session: resume` (no target)
    resumes the chunk's most-recent session-bearing lease overall — here the reviewer's,
    not build's own prior one, the "wrong inheritance" plan Q4's `resume:build` avoids."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})

    build_env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES, session=SessionMode.RESUME)
    review_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES, session=SessionMode.FRESH)
    build_reentry_env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES, session=SessionMode.RESUME)

    hub.queue = [QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    harness1 = FakeHarness(
        handle=WorkerHandle(session_id="sess-build-1", pid=100, process_start_time="start-100", pgid=100),
        verdict="pass",
    )
    ctx1 = make_context(store, hub=hub, provider=provider, harness=harness1, probe=FakeProbe(), clock=FixedClock(_NOW))
    Fill(ctx1).run()

    hub.envelopes["ch_1"] = build_env  # `_advance_exited_worker`'s own idempotent re-read
    hub.apply_responses = [
        ApplyReply(outcome=ApplyOutcome.NEXT, next_envelope=review_env),
        ApplyReply(outcome=ApplyOutcome.NEXT, next_envelope=build_reentry_env),
    ]
    harness2 = FakeHarness(
        handle=WorkerHandle(session_id="sess-review-1", pid=200, process_start_time="start-200", pgid=200),
        verdict="fail",
    )
    ctx2 = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness2,
        probe=FakeProbe(),
        clock=FixedClock(_NOW + timedelta(minutes=1)),
    )
    Advance(ctx2).run()  # launches the detached elicitation
    Advance(ctx2).run()  # collects it — the fake pid reads dead by default
    Pull(ctx2).run()

    hub.envelopes["ch_1"] = review_env  # `_advance_exited_worker`'s own idempotent re-read
    harness3 = FakeHarness(
        handle=WorkerHandle(session_id="sess-should-not-be-used", pid=300, process_start_time="start-300", pgid=300),
        verdict="pass",
    )
    ctx3 = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness3,
        probe=FakeProbe(),
        clock=FixedClock(_NOW + timedelta(minutes=2)),
    )
    Advance(ctx3).run()  # launches the detached elicitation
    Advance(ctx3).run()  # collects it — the fake pid reads dead by default
    Pull(ctx3).run()

    assert harness3.resume_froms == ["sess-review-1"]  # (a) chunk-most-recent, not build's own


def test_within_node_retry_stays_fresh_even_when_the_node_is_resume(tmp_path: Path):
    """Q3: `session:` governs node ENTRY only. A within-node retry after a
    verdict-less exit re-mints fresh, never resolving a resume target, even when the
    node's own mode is `resume` and a prior session exists for it to (wrongly) find."""
    store = _store(tmp_path)
    _seed_running_lease(store, session="sess-a")
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, session=SessionMode.RESUME
    )
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=201, process_start_time="start-201", pgid=201), verdict=None
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — no parseable <Choice> -> failure -> requeue in place, fresh

    assert harness.resume_froms == [None]  # (d) a retry never resolves a resume target
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.epoch == 2 and lease.session_id == "sess-b"


# RESUME-TIME PREAMBLE ELISION: component tier, proving the WIRING (store
# read -> renderer -> adapter prefix) the renderer's own unit tier cannot.


def _preamble_config(*, runner_prompt: str = "BLIZZARD-FRAMING"):
    return LoopConfig(
        runner_name="r1",
        runner_id="rn_1",
        runner_prompt=runner_prompt,
    )


def _first_build_spawn(
    store: SqlAlchemyRunnerStore,
    hub: FakeHub,
    provider: FakeProvider,
    env: Envelope,
    *,
    session: str,
    at: datetime,
    config: LoopConfig | None = None,
):
    """FILL's first arrival at `build` — a fresh session, nothing to resume."""
    hub.queue = [QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    harness = FakeHarness(
        handle=WorkerHandle(session_id=session, pid=100, process_start_time="start-100", pgid=100), verdict="pass"
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(at),
        config=config if config is not None else _preamble_config(),
        spawn_root="/ws",
        workspace_prompt="WORKSPACE-POLICY",
    )
    Fill(ctx).run()
    return harness


def _reenter_node(
    store: SqlAlchemyRunnerStore,
    hub: FakeHub,
    provider: FakeProvider,
    env: Envelope,
    *,
    session: str,
    pid: int,
    at: datetime,
    config: LoopConfig | None = None,
):
    """One ADVANCE + PULL cycle re-entering the envelope's node on its own prior session."""
    hub.envelopes["ch_1"] = env
    hub.apply_responses = [ApplyReply(outcome=ApplyOutcome.NEXT, next_envelope=env)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id=session, pid=pid, process_start_time=f"start-{pid}", pgid=pid), verdict="pass"
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(at),
        config=config if config is not None else _preamble_config(),
        spawn_root="/ws",
        workspace_prompt="WORKSPACE-POLICY",
    )
    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default
    Pull(ctx).run()
    return harness


def _resuming_build_env():
    return make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, session=SessionMode.RESUME, session_source="build"
    )


def test_fresh_spawn_sends_all_three_layers_and_records_a_fingerprint(tmp_path: Path):
    """Scenario 1 (AC4): first arrival resumes nothing, so the prefix is exactly today's
    three-layer composition — and the spawn leaves behind the baseline its first resume
    will compare against."""
    store = _store(tmp_path)
    hub = FakeHub()
    env = _resuming_build_env()

    harness = _first_build_spawn(store, hub, FakeProvider({"e1": "/ws/e1"}), env, session="sess-build-1", at=_NOW)

    assert harness.resume_froms == [None]
    prefix = harness.spawns[0][1].prompt_prefix
    spawned = store.active_lease_for_chunk("ch_1")
    assert spawned is not None
    assert prefix == (
        "BLIZZARD-FRAMING\n\n"
        "WORKSPACE-POLICY\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| runner id | `r1` |\n"
        "| chunk id | `ch_1` |\n"
        "| lease id | " + f"`{spawned.lease_id}` |\n"
        "| environment name | `e1` |\n"
        "| environment workdir | `/ws/e1` |"
    )
    assert store.session_preamble_fingerprint(SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-build-1")) is not None


def test_resume_with_unchanged_prose_elides_and_keeps_eliding(tmp_path: Path):
    """Scenario 2 (AC2, AC6): two node re-entries on one session with nothing changed.
    The **third** spawn is load-bearing — a fingerprint recorded off the collapse
    banner only misfires on the spawn *after* the first elision."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    env = _resuming_build_env()

    _first_build_spawn(store, hub, provider, env, session="sess-build-1", at=_NOW)
    first_lease = store.active_lease_for_chunk("ch_1")
    assert first_lease is not None

    second = _reenter_node(store, hub, provider, env, session="sess-build-1", pid=200, at=_NOW + timedelta(minutes=1))
    assert second.resume_froms == ["sess-build-1"]
    second_prefix = second.spawns[0][1].prompt_prefix
    assert RESUME_STANDING_UNCHANGED in second_prefix
    assert "BLIZZARD-FRAMING" not in second_prefix
    assert "WORKSPACE-POLICY" not in second_prefix
    assert RESUME_UPDATED_NOTICE not in second_prefix
    # AC6's real hazard is a STALE table surviving into a resumed spawn — "contains the new
    # id" alone would not catch that, so the prior attempt's lease id must be absent too.
    second_lease = store.active_lease_for_chunk("ch_1")
    assert second_lease is not None
    assert f"| lease id | `{second_lease.lease_id}` |" in second_prefix
    assert first_lease.lease_id not in second_prefix
    assert "| environment workdir | `/ws/e1` |" in second_prefix

    third = _reenter_node(store, hub, provider, env, session="sess-build-1", pid=300, at=_NOW + timedelta(minutes=2))
    third_prefix = third.spawns[0][1].prompt_prefix
    assert RESUME_STANDING_UNCHANGED in third_prefix
    assert RESUME_UPDATED_NOTICE not in third_prefix  # nothing changed — nothing announced
    assert "BLIZZARD-FRAMING" not in third_prefix
    assert "WORKSPACE-POLICY" not in third_prefix


def test_resume_after_a_live_workspace_prompt_replace_announces_the_new_prose(tmp_path: Path):
    """Scenario 3 — the correctness half of the issue. `PUT /api/host/workspaces/{id}/prompt` between
    two spawns of one session: the replacement reaches the worker announced, not disguised
    as the block it was handed a spawn ago."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    env = _resuming_build_env()

    _first_build_spawn(store, hub, provider, env, session="sess-build-1", at=_NOW)

    # The local API's write, landing between the two spawns.
    store.set_workspace_prompt("ws1", prompt="REPLACED-POLICY", at=_NOW + timedelta(seconds=30))

    second = _reenter_node(store, hub, provider, env, session="sess-build-1", pid=200, at=_NOW + timedelta(minutes=1))

    prefix = second.spawns[0][1].prompt_prefix
    assert prefix.startswith(RESUME_UPDATED_NOTICE)
    assert "REPLACED-POLICY" in prefix
    assert "WORKSPACE-POLICY" not in prefix  # the superseded prose is gone
    assert RESUME_BLIZZARD_UNCHANGED in prefix  # layer 1 did not move, so it stays collapsed
    assert "BLIZZARD-FRAMING" not in prefix


def test_resume_after_a_runner_prompt_change_announces_and_re_sends_layer_one(tmp_path: Path):
    """Scenario 4 — layer 1's door. `runner_prompt` is a startup knob, so this is reachable
    only across a runner restart, but a resumed session outlives one."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    env = _resuming_build_env()

    _first_build_spawn(store, hub, provider, env, session="sess-build-1", at=_NOW)

    # The restarted runner's config — same workspace prompt, new blizzard framing.
    restarted = _preamble_config(runner_prompt="REFRAMED-BLIZZARD")
    second = _reenter_node(
        store, hub, provider, env, session="sess-build-1", pid=200, at=_NOW + timedelta(minutes=1), config=restarted
    )

    prefix = second.spawns[0][1].prompt_prefix
    assert prefix.startswith(f"{RESUME_UPDATED_NOTICE}\n\nREFRAMED-BLIZZARD\n\n")
    assert "BLIZZARD-FRAMING" not in prefix
    assert RESUME_WORKSPACE_UNCHANGED in prefix  # layer 2 held, so it collapses
    assert "WORKSPACE-POLICY" not in prefix


def test_a_resume_with_message_between_node_entries_does_not_disturb_the_fingerprint(tmp_path: Path):
    """Scenario 5: a restart-resume re-records the SAME session id via `record_spawn`
    sending no `prompt_prefix`. The fingerprint write is reachable only from
    `Spawner.spawn`, so that path can't touch it and the next entry still elides."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    env = _resuming_build_env()

    _first_build_spawn(store, hub, provider, env, session="sess-build-1", at=_NOW)

    # --- The graceful-restart re-attach, interleaved: mark, then RESUME in place.
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW + timedelta(seconds=30))
    hub.chunks["ch_1"] = ChunkState(
        chunk_id="ch_1",
        status=ChunkStatus.RUNNING,
        latest_epoch=1,
        route_runner_id="r1",
    )
    resume_harness = FakeHarness(handle=_HANDLE, verdict="pass")
    Resume(
        make_context(
            store,
            hub=hub,
            provider=provider,
            harness=resume_harness,
            probe=FakeProbe(),
            clock=FixedClock(_NOW + timedelta(seconds=45)),
            config=_preamble_config(),
            spawn_root="/ws",
            workspace_prompt="WORKSPACE-POLICY",
        )
    ).run()
    assert resume_harness.resumed  # the resume-with-message really ran

    # --- The next node entry on the same session still finds an honest fingerprint.
    second = _reenter_node(store, hub, provider, env, session="sess-build-1", pid=200, at=_NOW + timedelta(minutes=1))

    prefix = second.spawns[0][1].prompt_prefix
    assert RESUME_STANDING_UNCHANGED in prefix
    assert RESUME_UPDATED_NOTICE not in prefix
    assert "BLIZZARD-FRAMING" not in prefix


def test_an_announced_change_is_announced_once_and_then_elided(tmp_path: Path):
    """Scenario 6 — the fingerprint is re-recorded on a **resumed** spawn, not just a
    fresh one. Three spawns, one workspace-prompt replace between the first and second:
    the third must elide, since the second spawn recorded what it actually sent."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    env = _resuming_build_env()

    _first_build_spawn(store, hub, provider, env, session="sess-build-1", at=_NOW)
    store.set_workspace_prompt("ws1", prompt="REPLACED-POLICY", at=_NOW + timedelta(seconds=30))

    second = _reenter_node(store, hub, provider, env, session="sess-build-1", pid=200, at=_NOW + timedelta(minutes=1))
    third = _reenter_node(store, hub, provider, env, session="sess-build-1", pid=300, at=_NOW + timedelta(minutes=2))

    p2 = second.spawns[0][1].prompt_prefix
    p3 = third.spawns[0][1].prompt_prefix

    # The replace is announced exactly once, on the spawn that first carries it.
    assert RESUME_UPDATED_NOTICE in p2
    assert "REPLACED-POLICY" in p2

    # ...and never again, because spawn 2 recorded the prose it sent.
    assert RESUME_UPDATED_NOTICE not in p3, (
        "a settled change was re-announced — the resumed spawn's fingerprint write was skipped"
    )
    assert RESUME_STANDING_UNCHANGED in p3
    assert "REPLACED-POLICY" not in p3

    # The store-level fact behind it: the newest row for the session is what spawn 2 sent.
    settled = store.session_preamble_fingerprint(SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-build-1"))
    assert settled is not None
    assert settled.workspace == hashlib.sha256(b"REPLACED-POLICY").hexdigest()


def test_a_cross_node_resume_names_the_node_transition_from_recorded_state(tmp_path: Path):
    """Scenario 7 — the wiring the renderer's unit tier cannot pin: the previous
    turn's node reaches `Preamble.of` from the recorded lease rows, so `verify` resuming the
    session `build` minted opens with the role-change line naming both nodes."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})

    _first_build_spawn(store, hub, provider, _resuming_build_env(), session="sess-build-1", at=_NOW)

    verify_env = make_envelope(
        "ch_1", "verify", node_id="nd_verify", choices=_CHOICES, session=SessionMode.RESUME, session_source="build"
    )
    second = _reenter_node(
        store, hub, provider, verify_env, session="sess-build-1", pid=200, at=_NOW + timedelta(minutes=1)
    )

    assert second.resume_froms == ["sess-build-1"]
    prefix = second.spawns[0][1].prompt_prefix
    assert prefix.startswith(f"{resume_cross_node(node='verify', prior_node='build')}\n\n{RESUME_STANDING_UNCHANGED}")
    assert "`build` node-step" in prefix


def test_a_node_reentered_through_an_edge_starts_its_retry_budget_afresh(tmp_path: Path):
    """Build spent a retry and then passed; review failed back to build. The second visit's first
    verdict-less exit is its own first attempt — a retry, not an exhaustion of the earlier visit's budget."""
    store = _store(tmp_path)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()  # retries_max = 2
    # Visit one: a failed attempt, a passing retry; then review passed back to build.
    _seed_running_lease(store, lease="lease_1", epoch=1)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="failed", closed_at=_NOW)
    _seed_running_lease(store, lease="lease_2", epoch=2)
    store.record_closure(lease_id="lease_2", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    store.record_lease(
        NewLease(
            lease_id="lease_3",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_review",
            node_name="review",
            epoch=3,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_closure(
        lease_id="lease_3", chunk_id="ch_1", node_id="nd_review", reason="transitioned", closed_at=_NOW
    )
    # Visit two: the build re-entry exits verdict-less.
    _seed_running_lease(store, lease="lease_4", epoch=4, pid=300, start="start-0", session="sess-4")
    assert store.attempt_count("ch_1", "nd_build") == 1

    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-4", pid=304, process_start_time="start-4", pgid=304), verdict=None
    )
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())
    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — verdict-less -> fails -> retries

    assert [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED] == []
    assert store.open_escalation_for_chunk("ch_1") is None


@pytest.mark.parametrize(("workspace_root", "expected_cwd"), [("/ws", "/ws"), ("", "/ws/e1")])
def test_judgement_runs_in_the_sessions_own_spawn_cwd(tmp_path: Path, workspace_root: str, expected_cwd: str):
    """A judgement resumes the held session from its spawn cwd — the workspace root when one
    is configured, never the environment's own workdir beneath it."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="pass"
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(
            runner_name="r1",
            runner_id=FIXTURE_RUNNER_ID,
        ),
        spawn_root=workspace_root,
    )

    Advance(ctx).run()  # launches the detached elicitation

    assert [judged[0] for judged in harness.judged] == [expected_cwd]
