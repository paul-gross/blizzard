"""The reconciliation step functions — the walking-skeleton loop logic.

Each step is driven directly against a real tmp store with fakes at the seams
(``bzh:steppable-loop``): FILL claims and spawns, ADVANCE judges an exited worker
and moves the chunk, a hub-node hold polls to release, REAP expires an orphan, and
the retry budget requeues then escalates. The full happy path is exercised as a
sequence of ticks at the end.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import advance, fill, reap
from blizzard.runner.loop.tick import tick
from blizzard.runner.loop.worktree import GitArtifact
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.queue import QueuePeekEntry
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_ALIVE = (100, "start-100")  # (pid, start_time) for a running worker
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _build_envelope(chunk="ch_1"):  # type: ignore[no-untyped-def]
    return make_envelope(chunk, "build", node_id="nd_build", choices=_CHOICES)


def _seed_running_lease(store, *, chunk="ch_1", lease="lease_1", pid=100, start="start-100", session="sess-a", epoch=1):  # type: ignore[no-untyped-def]
    """A build lease already spawned into env e1, plus its binding."""
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
    store.record_spawn(lease, pid=pid, process_start_time=start, session_id=session)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


# --------------------------------------------------------------------------- #
# FILL
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_fill_claims_acquires_binds_and_spawns(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    env = _build_envelope()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())

    fill(ctx)

    assert len(hub.claims) == 1
    assert hub.claims[0].environment_ids == ["e1"]
    assert len(harness.spawns) == 1
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.pid == 100 and lease.session_id == "sess-a"
    assert store.held_environment_ids() == ["e1"]


@pytest.mark.unit
def test_fill_reports_lease_mint_to_hub(tmp_path):  # type: ignore[no-untyped-def]
    """Every node-step spawn reports its lease.minted so the hub's fence tracks it (D-044)."""
    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", _build_envelope())
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )

    fill(ctx)

    assert hub.leases == [("ch_1", 1, "r1")]  # the first node-step's epoch reported up


@pytest.mark.unit
def test_fill_conflict_releases_and_does_not_bind(tmp_path):  # type: ignore[no-untyped-def]
    from blizzard.runner.loop.hub import RouteClaimOutcome
    from blizzard.wire.route import RouteClaimConflict

    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = RouteClaimOutcome(conflict=RouteClaimConflict(chunk_id="ch_1", held_by_runner_id="r2"))
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())

    fill(ctx)

    assert provider.released == ["e1"]  # released the acquired-but-unclaimed env
    assert store.held_environment_ids() == []
    assert store.list_active_leases() == []
    assert harness.spawns == []


@pytest.mark.unit
def test_fill_env_bound_skips(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    provider = FakeProvider({}, refuse=True)
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict="pass"), probe=FakeProbe()
    )

    fill(ctx)

    assert hub.claims == []
    assert store.list_active_leases() == []


@pytest.mark.unit
def test_fill_respects_max_agents(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)  # one active lease already occupies the single slot
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_2", graph_id="gr_1", position=0)]
    provider = FakeProvider({"e2": "/ws/e2"})
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(alive={_ALIVE}),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1),
    )

    fill(ctx)

    assert hub.claims == []  # no free slot


# --------------------------------------------------------------------------- #
# ADVANCE — exited worker
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_advance_exited_worker_pass_enters_hub_node(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.HUB_NODE_TAKEN)]
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    wt = FakeWorktreeGit(
        [GitArtifact(repo="toy-api", branch_name="e1", commit_hash="abc123", repo_workdir="/ws/e1/toy-api")]
    )
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(), worktree_git=wt)

    advance(ctx)  # probe reports the worker dead (empty alive set) -> exit-is-done

    assert wt.pushed == [("/ws/e1/toy-api", "e1")]  # branch pushed BEFORE submit
    assert len(hub.completions) == 1
    chunk_id, submission = hub.completions[0]
    assert chunk_id == "ch_1"
    assert submission.choice == "pass"
    assert submission.epoch == 1
    assert submission.artifacts[0].commit_hash == "abc123"
    assert store.active_lease_for_chunk("ch_1") is None  # build lease closed
    assert store.held_environment_ids() == ["e1"]  # envs held for the hub node
    assert provider.released == []


@pytest.mark.unit
def test_advance_next_spawns_next_node_in_place(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    next_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=next_env)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200"), verdict="pass"
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    advance(ctx)

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "review" and lease.epoch == 2  # fresh epoch, same env
    assert store.held_environment_ids() == ["e1"]
    # The review node-step's fresh epoch is reported up so the hub's fence advances (D-044).
    assert ("ch_1", 2, "r1") in hub.leases


@pytest.mark.unit
def test_advance_review_harvests_findings_asset_from_assessment(tmp_path):  # type: ignore[no-untyped-def]
    """A node that `produces` a name no git commit covers emits the assessment as an asset (D-026)."""
    from blizzard.hub.domain.artifacts import ArtifactKind
    from tests.runner_fakes import make_envelope

    store = _store(tmp_path)
    # Seed a review lease (produces review-findings) already spawned into e1.
    store.record_lease(
        NewLease(
            lease_id="lease_r",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_review",
            node_name="review",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_r", pid=100, process_start_time="start-100", session_id="sess-a")
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=_build_envelope())]
    # Review is read-only: no git commit produced, but the judgement carries findings.
    harness = FakeHarness(handle=_HANDLE, verdict="fail", assessment="BLOCKING: guard the empty input")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit([]),
    )

    advance(ctx)

    _, submission = hub.completions[0]
    findings = [a for a in submission.artifacts if a.name == "review-findings"]
    assert len(findings) == 1
    assert findings[0].kind is ArtifactKind.ASSET
    assert findings[0].content == "BLOCKING: guard the empty input"


@pytest.mark.unit
def test_advance_done_releases_environments(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict="pass"), probe=FakeProbe()
    )

    advance(ctx)

    assert provider.released == ["e1"]
    assert store.held_environment_ids() == []


@pytest.mark.unit
def test_advance_skips_running_worker(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(alive={_ALIVE}),
    )

    advance(ctx)  # worker alive -> nothing judged, nothing polled

    assert hub.completions == []
    assert store.active_lease_for_chunk("ch_1") is not None


# --------------------------------------------------------------------------- #
# ADVANCE — hub-node poll
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_poll_hub_node_releases_on_done(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    # A chunk held at a hub node: a binding but no active lease.
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1", graph_id="gr_1", status=ChunkStatus.DONE, current_node_id="deliver", latest_epoch=1
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict="pass"), probe=FakeProbe()
    )

    advance(ctx)

    assert provider.released == ["e1"]
    assert store.held_environment_ids() == []


@pytest.mark.unit
def test_poll_hub_node_waits_while_delivering(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1", graph_id="gr_1", status=ChunkStatus.DELIVERING, current_node_id="deliver", latest_epoch=1
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict="pass"), probe=FakeProbe()
    )

    advance(ctx)

    assert provider.released == []  # still delivering — hold
    assert store.held_environment_ids() == ["e1"]


# --------------------------------------------------------------------------- #
# Failure, requeue, escalation
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_verdict_less_exit_fails_and_requeues(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=201, process_start_time="start-201"), verdict=None
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    advance(ctx)  # no parseable <Choice> -> failure -> requeue in place

    assert hub.completions == []  # never submitted a completion
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.epoch == 2  # a fresh attempt was spawned
    assert store.attempt_count("ch_1", "nd_build") == 2


@pytest.mark.unit
def test_reap_orphan_requeues(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    # A lease minted but never spawned (pid None) with its binding already recorded.
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
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=202, process_start_time="start-202"), verdict="pass"
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    reap(ctx)

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.lease_id != "lease_1"  # a fresh lease replaced the orphan
    assert lease.pid == 202


@pytest.mark.unit
def test_retries_exhausted_escalates_and_holds_envs(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()  # retries_max = 2
    # Three verdict-less attempts: attempt 1 & 2 requeue, attempt 3 escalates.
    # (_seed_running_lease records the binding on the first attempt.)
    provider = FakeProvider({"e1": "/ws/e1"})
    for i in range(1, 4):
        handle = WorkerHandle(session_id=f"sess-{i}", pid=300 + i, process_start_time=f"start-{i}")
        harness = FakeHarness(handle=handle, verdict=None)
        ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())
        if i == 1:
            # bootstrap the first attempt as a spawned-but-dead worker
            _seed_running_lease(store, pid=300, start="start-0")
        advance(ctx)

    assert store.active_lease_for_chunk("ch_1") is None  # no more retries
    pending = store.pending_outbound()
    assert len(pending) == 1 and pending[0].kind == "escalation.needs_human"
    assert store.held_environment_ids() == ["e1"]  # envs held for takeover
    assert provider.released == []
    # The escalation is reported up to the hub with a pasteable takeover command (D-009/D-035).
    assert len(hub.escalations) == 1
    esc_chunk, _esc_epoch, esc_runner, takeover = hub.escalations[0]
    assert esc_chunk == "ch_1" and esc_runner == "r1"
    assert takeover.startswith("cd /ws/e1 &&") and "--resume" in takeover


# --------------------------------------------------------------------------- #
# Full happy path across ticks
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_full_happy_path_across_ticks(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    env = _build_envelope()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    hub.envelopes["ch_1"] = env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.HUB_NODE_TAKEN)]
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    wt = FakeWorktreeGit(
        [GitArtifact(repo="toy-api", branch_name="e1", commit_hash="abc123", repo_workdir="/ws/e1/toy-api")]
    )
    probe = FakeProbe(alive={_ALIVE})  # worker alive during tick 1
    clock = FixedClock(_NOW)
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=probe, worktree_git=wt, clock=clock)

    # Tick 1: FILL claims + spawns; the worker is alive, so ADVANCE leaves it.
    tick(ctx)
    assert store.active_lease_for_chunk("ch_1") is not None
    assert hub.completions == []

    # The worker finishes and exits.
    probe.alive.clear()

    # Tick 2: ADVANCE judges the exited worker -> pass -> deliver hub node; envs held.
    tick(ctx)
    assert len(hub.completions) == 1
    assert store.active_lease_for_chunk("ch_1") is None
    assert store.held_environment_ids() == ["e1"]

    # The hub's merge queue lands the delivery.
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1", graph_id="gr_1", status=ChunkStatus.DONE, current_node_id="deliver", latest_epoch=1
    )
    # Nothing left to peek.
    hub.queue = []

    # Tick 3: the hub-node poll sees `done` and releases the environment.
    tick(ctx)
    assert provider.released == ["e1"]
    assert store.held_environment_ids() == []
    assert store.live_tenure_chunk_ids() == []
