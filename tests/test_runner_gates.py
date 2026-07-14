"""Runner-side human gates — the loop's gate paths (unit tier, D-032/D-045).

Two behaviours, driven against a real tmp store with fakes at the seams
(``bzh:steppable-loop``): a **runner-config gate** submits a decision in place of a
transition for a node the operator gates by name, parking the chunk; and a chunk parked
on a **resolved decision** is advanced by the runner recording the resolving transition
and continuing in place from the returned envelope.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import advance, fill, pull
from blizzard.runner.loop.worktree import GitArtifact
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, RouteView
from blizzard.wire.decision import DecisionChoiceModel, DecisionView
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    make_context,
    make_envelope,
    make_store,
)

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_running_lease(store, *, node_id="nd_build", node_name="build"):  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id=node_id,
            node_name=node_name,
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a")
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


@pytest.mark.unit
def test_runner_config_gate_buffers_a_decision_not_a_completion(tmp_path):  # type: ignore[no-untyped-def]
    """A gated node's exited worker submits a decision (not a transition) and parks (D-032)."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    wt = FakeWorktreeGit(
        [GitArtifact(repo="toy-api", branch_name="e1", commit_hash="abc123", repo_workdir="/ws/e1/toy-api")]
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),  # worker exited
        worktree_git=wt,
        config=LoopConfig(runner_id="r1", workspace_id="ws1", gates=("build",)),
    )

    advance(ctx)  # gated -> the branch is pushed, a decision is buffered, no verdict elicited

    assert wt.pushed == [("/ws/e1/toy-api", "e1")]
    assert harness.judged == []  # the human judges — no verdict elicitation
    buffered = [b for b in store.pending_outbound() if b.kind == "decision.submitted"]
    assert len(buffered) == 1 and buffered[0].lease_id == "lease_1"
    assert hub.decisions_submitted == []  # not yet flushed
    assert store.active_lease_for_chunk("ch_1") is not None  # open until the flush parks it

    pull(ctx)  # the flusher submits the decision and parks the chunk

    assert len(hub.decisions_submitted) == 1
    chunk_id, submission = hub.decisions_submitted[0]
    assert chunk_id == "ch_1" and submission.from_node_id == "nd_build" and submission.epoch == 1
    assert submission.artifacts[0].commit_hash == "abc123"
    assert store.active_lease_for_chunk("ch_1") is None  # lease closed (parked)
    assert store.held_environment_ids() == ["e1"]  # envs held while waiting_on_human


@pytest.mark.unit
def test_gated_node_decision_elicited_exactly_once_while_flush_pending(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    wt = FakeWorktreeGit(
        [GitArtifact(repo="toy-api", branch_name="e1", commit_hash="abc123", repo_workdir="/ws/e1/toy-api")]
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        worktree_git=wt,
        config=LoopConfig(runner_id="r1", workspace_id="ws1", gates=("build",)),
    )

    advance(ctx)
    advance(ctx)  # decision already buffered -> the lease is skipped (pending_submission)

    buffered = [b for b in store.pending_outbound() if b.kind == "decision.submitted"]
    assert len(buffered) == 1
    assert len(wt.pushed) == 1  # not re-pushed


@pytest.mark.unit
def test_resolved_gate_is_advanced_by_the_resolving_transition(tmp_path):  # type: ignore[no-untyped-def]
    """A held chunk whose gate the human resolved: the runner records the resolving
    transition and continues in place from the returned envelope (D-027/D-045)."""
    store = _store(tmp_path)
    # A chunk parked at a gate: envs bound, no active lease.
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.RUNNING,  # resolved, awaiting the resolving transition
        current_node_id="nd_gate",
        latest_epoch=1,
        decision=DecisionView(
            decision_id="dec_1",
            chunk_id="ch_1",
            node_id="nd_gate",
            node_name="approve-gate",
            epoch=1,
            choices=[
                DecisionChoiceModel(name="approve", description="ship"),
                DecisionChoiceModel(name="reject", description="back"),
            ],
            submitted_at=_NOW.isoformat(),
            resolved_choice="approve",
            resolved_by="ada",
            transitioned=False,
        ),
    )
    # The resolving transition returns the next runner node to continue into.
    next_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=next_env)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200"), verdict="pass"
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    advance(ctx)  # the held-chunk poll picks up the resolved decision

    assert len(hub.completions) == 1
    chunk_id, submission = hub.completions[0]
    assert chunk_id == "ch_1"
    assert submission.decision_id == "dec_1"
    assert submission.choice == "approve"
    assert submission.epoch == 1
    assert submission.from_node_id == "nd_gate"
    # Continued in place: the next node was spawned in the warm environment.
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "review"
    assert store.held_environment_ids() == ["e1"]


@pytest.mark.unit
def test_unresolved_gate_keeps_waiting(tmp_path):  # type: ignore[no-untyped-def]
    """A parked chunk whose decision is not yet resolved advances nothing."""
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.WAITING_ON_HUMAN,
        current_node_id="nd_gate",
        latest_epoch=1,
        decision=DecisionView(
            decision_id="dec_1",
            chunk_id="ch_1",
            node_id="nd_gate",
            node_name="approve-gate",
            epoch=1,
            choices=[DecisionChoiceModel(name="approve", description="ship")],
            submitted_at=_NOW.isoformat(),
            resolved_choice=None,
            transitioned=False,
        ),
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )

    advance(ctx)

    assert hub.completions == []  # nothing to resolve yet
    assert store.active_lease_for_chunk("ch_1") is None
    assert store.held_environment_ids() == ["e1"]  # still parked, envs held


@pytest.mark.unit
def test_fill_leaves_a_resolved_gate_to_advance(tmp_path):  # type: ignore[no-untyped-def]
    """FILL's interrupted-claim reconciler must not adopt a chunk parked on a resolved gate.

    A resolved-but-not-transitioned gate keeps its route live, so it derives ``running``
    with a bound env and no active lease — the *same* shape FILL's crash reconciler
    (``_reconcile_interrupted_claims``) recovers. Without the gate guard, FILL would
    "adopt" it by spawning a worker on the human-judged node, minting a fresh-epoch lease
    that strands the human's resolving transition as stale (D-045/D-027). FILL must skip
    it and leave the resolving transition to ADVANCE.
    """
    store = _store(tmp_path)
    # A chunk parked at a gate the human just resolved: env bound, no active lease, RUNNING.
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.RUNNING,  # resolved, awaiting the resolving transition
        current_node_id="nd_gate",
        latest_epoch=1,
        # The route is still live and held by THIS runner — the fact that makes a resolved
        # gate look exactly like an interrupted claim to the reconciler (route ours, RUNNING).
        route=RouteView(runner_id="r1", workspace_id="ws1", environment_ids=["e1"]),
        decision=DecisionView(
            decision_id="dec_1",
            chunk_id="ch_1",
            node_id="nd_gate",
            node_name="approve-gate",
            epoch=1,
            choices=[DecisionChoiceModel(name="approve", description="ship")],
            submitted_at=_NOW.isoformat(),
            resolved_choice="approve",
            resolved_by="ada",
            transitioned=False,
        ),
    )
    # An envelope for the gate node exists — so an (incorrect) adopt would have something to
    # spawn; the guard must skip before ever reaching it.
    hub.envelopes["ch_1"] = make_envelope("ch_1", "approve-gate", node_id="nd_gate", choices=_CHOICES)
    hub.queue = []  # nothing new to fill — the reconciler is the only path that could act
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    fill(ctx)  # the reconciler runs first; it must leave the resolved gate untouched

    assert harness.spawns == []  # no worker spawned on the human-judged node
    assert store.active_lease_for_chunk("ch_1") is None  # no fresh-epoch lease minted
    assert hub.claims == []  # the route was not re-claimed
    assert store.held_environment_ids() == ["e1"]  # env still held for the resolution

    # ADVANCE owns it: the resolving transition is recorded at the parked epoch.
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.HUB_NODE_TAKEN, detail="deliver took over")]
    advance(ctx)

    assert len(hub.completions) == 1
    _, submission = hub.completions[0]
    assert submission.decision_id == "dec_1" and submission.choice == "approve" and submission.epoch == 1
