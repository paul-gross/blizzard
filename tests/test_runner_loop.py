"""The reconciliation step functions — the loop logic (unit tier).

Each step is driven directly against a real tmp store with fakes at the seams
(``bzh:steppable-loop``): FILL claims and spawns, ADVANCE judges and buffers a
completion, PULL flushes and applies, a hub-node hold polls to release, REAP expires
an orphan or stalled worker, and the retry budget requeues then escalates."""

from __future__ import annotations

import hashlib
import json
import shlex
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.enrollment import TokenHash
from blizzard.hub.domain.graph import SessionMode
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.preamble import (
    DEFAULT_BLIZZARD_PREAMBLE,
    RESUME_BLIZZARD_UNCHANGED,
    RESUME_STANDING_UNCHANGED,
    RESUME_UPDATED_NOTICE,
    RESUME_WORKSPACE_UNCHANGED,
    PreambleFingerprint,
)
from blizzard.runner.loop.attempt import Attempt
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.produces import ProducesReconciler
from blizzard.runner.loop.spawn import Spawner
from blizzard.runner.loop.steps import Advance, Fill, Pull, Reap, Resume, ResumeIntents
from blizzard.runner.loop.tick import tick
from blizzard.runner.loop.worktree import IWorktreeGit
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import NewLease
from blizzard.runner.store.schema import metadata as runner_metadata
from blizzard.wire.chunk import ChunkDetail, ChunkUsageTotalView, RouteView
from blizzard.wire.completion import SubmittedArtifact
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.facts import ESCALATION_RECORDED, LEASE_MINTED
from blizzard.wire.graph import ProducesEntry
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
    store.record_spawn(lease, pid=pid, process_start_time=start, session_id=session, spawned_at=_NOW)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _chunk_with_cost(  # type: ignore[no-untyped-def]
    chunk_id="ch_1",
    *,
    cost_usd,
    cost_partial=False,
    status=ChunkStatus.RUNNING,
    route_runner_id="r1",
    epoch=1,
):
    """A hub-derived ``ChunkDetail`` carrying a scripted usage/cost total (issue #61a)."""
    return ChunkDetail(
        chunk_id=chunk_id,
        graph_id="gr_1",
        status=status,
        current_node_id="nd_build",
        latest_epoch=epoch,
        route=RouteView(runner_id=route_runner_id, workspace_id="ws1", environment_ids=["e1"]),
        cost=ChunkUsageTotalView(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=cost_usd,
            cost_partial=cost_partial,
        ),
    )


# FILL


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

    Fill(ctx).run()

    assert len(hub.claims) == 1
    assert hub.claims[0].environment_ids == ["e1"]
    assert len(harness.spawns) == 1
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.pid == 100 and lease.session_id == "sess-a"
    assert store.held_environment_ids() == ["e1"]
    # The spawn buffered a lease.minted fact for the flusher, naming the lease
    # and carrying its epoch — the fence input the hub consumes.
    buffered = store.pending_outbound()
    assert [b.kind for b in buffered] == [LEASE_MINTED]
    assert buffered[0].lease_id == lease.lease_id


@pytest.mark.unit
def test_spawn_preamble_carries_lease_and_local_api(tmp_path):  # type: ignore[no-untyped-def]
    """The worker preamble carries the runner-minted identity the heartbeat hook needs."""
    store = _store(tmp_path)
    hub = FakeHub()
    env = _build_envelope()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", local_api_url="http://127.0.0.1:9999"),
    )

    Fill(ctx).run()

    _, preamble = harness.spawns[0]
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    assert preamble.lease_id == lease.lease_id
    assert preamble.local_api_url == "http://127.0.0.1:9999"


@pytest.mark.unit
def test_route_token_never_reaches_the_worker_preamble_or_prompt(tmp_path):  # type: ignore[no-untyped-def]
    """Containment (issue #84a): the route token lives only in the runner store and
    stamped outbound payloads — never in ``WorkerPreamble``, so it can never reach the
    worker's environment or its rendered prompt."""
    store = _store(tmp_path)
    hub = FakeHub()
    env = _build_envelope()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env, route_token="super-secret-route-token")
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Fill(ctx).run()

    assert store.route_token("ch_1") == "super-secret-route-token"  # stashed locally, as expected
    _, preamble = harness.spawns[0]
    for field_value in (
        preamble.lease_id,
        preamble.local_api_url,
        preamble.workspace_root,
        preamble.prompt_prefix,
        preamble.stdout_path,
    ):
        assert "super-secret-route-token" not in field_value
    assert not hasattr(preamble, "route_token")


@pytest.mark.unit
def test_fill_reports_lease_mint_to_hub(tmp_path):  # type: ignore[no-untyped-def]
    """Every node-step spawn reports its lease.minted so the hub's fence tracks it."""
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

    Fill(ctx).run()

    # The lease.minted rides the outbound buffer (store-and-forward); PULL's
    # flusher reports it up to POST /events, so it is not pushed inline at spawn.
    buffered = [b for b in store.pending_outbound() if b.kind == LEASE_MINTED]
    assert len(buffered) == 1
    assert json.loads(buffered[0].payload) == {"chunk_id": "ch_1", "epoch": 1, "route_token": "rtok_test"}
    Pull(ctx).run()
    assert [(f.kind, f.payload["epoch"]) for f in hub.pushed] == [(LEASE_MINTED, 1)]


@pytest.mark.unit
def test_fill_stashes_the_claims_route_token(tmp_path):  # type: ignore[no-untyped-def]
    """A won claim's plaintext route token (issue #84a) is stashed locally, keyed by
    chunk — the read the store's own :meth:`route_token` serves back."""
    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", _build_envelope(), route_token="rtok-abc123")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )

    Fill(ctx).run()

    assert store.route_token("ch_1") == "rtok-abc123"


@pytest.mark.unit
def test_fill_mints_a_lease_capability_token_and_carries_its_plaintext_to_spawn(tmp_path):  # type: ignore[no-untyped-def]
    """A per-lease capability token (issue #113, Phase 1) is minted alongside the
    lease: its sha256 hash lands durably, and its plaintext rides only the spawn
    preamble — never the store — for ``BLIZZARD_LEASE_TOKEN`` to carry into the env."""
    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", _build_envelope())
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Fill(ctx).run()

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    _, preamble = harness.spawns[0]
    assert preamble.lease_token
    assert store.lease_token_hash(lease.lease_id) == TokenHash(preamble.lease_token).hex


@pytest.mark.unit
def test_completion_and_decision_submissions_carry_the_stashed_route_token(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", _build_envelope(), route_token="rtok-abc123")
    hub.envelopes["ch_1"] = _build_envelope()
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )

    Fill(ctx).run()  # claims and stashes the token, spawns the worker
    Advance(ctx).run()  # worker "exited" (FakeProbe reports it dead) — judged and buffered

    buffered = [b for b in store.pending_outbound() if b.kind == "completion.submitted"]
    assert len(buffered) == 1
    submission = json.loads(buffered[0].payload)["submission"]
    assert submission["route_token"] == "rtok-abc123"


@pytest.mark.unit
def test_same_runner_requeue_after_failure_reuses_the_same_route_token(tmp_path):  # type: ignore[no-untyped-def]
    """A same-runner requeue re-spawns under the route already held — no fresh claim —
    so it must keep presenting the token that claim minted (issue #84a)."""
    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", _build_envelope(), route_token="rtok-abc123")
    hub.envelopes["ch_1"] = _build_envelope()
    harness = FakeHarness(handle=_HANDLE, verdict=None)  # no parseable <Choice> -> fail -> requeue
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Fill(ctx).run()  # claims (epoch 1), stashes the token
    Advance(ctx).run()  # verdict-less exit -> fail attempt -> requeue in place (fresh lease, epoch 2)

    lease_mints = [json.loads(b.payload) for b in store.pending_outbound() if b.kind == LEASE_MINTED]
    assert [m["epoch"] for m in lease_mints] == [1, 2]
    assert all(m["route_token"] == "rtok-abc123" for m in lease_mints)  # same token both times


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

    Fill(ctx).run()

    assert provider.released == ["e1"]  # released the acquired-but-unclaimed env
    assert store.held_environment_ids() == []
    assert store.list_active_leases() == []


@pytest.mark.unit
def test_fill_paused_denial_releases_and_stops_filling(tmp_path):  # type: ignore[no-untyped-def]
    """A 403 (issue #44) is distinct from a 409 conflict: the claim was refused outright,
    so FILL releases the binding and stops trying further slots this tick rather than
    keep racing claims that will be refused the same way."""
    from blizzard.runner.loop.hub import RouteClaimOutcome
    from blizzard.wire.route import RouteClaimPausedDenial

    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = RouteClaimOutcome(denied_paused=RouteClaimPausedDenial(chunk_id="ch_1", runner_id="r1"))
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())

    Fill(ctx).run()

    assert len(hub.claims) == 1  # the claim was actually attempted
    assert provider.released == ["e1"]  # released the acquired-but-unclaimed env
    assert store.held_environment_ids() == []
    assert store.list_active_leases() == []
    assert harness.spawns == []


@pytest.mark.unit
def test_fill_terminal_denial_releases_and_keeps_filling(tmp_path):  # type: ignore[no-untyped-def]
    """The must-fix-1 claim guard (issue #118): the chunk was stopped between this
    runner's peek and its claim POST — not a fleet-wide brake, so FILL releases the
    binding and keeps trying its remaining slots, same as a race-loss conflict."""
    from blizzard.runner.loop.hub import RouteClaimOutcome
    from blizzard.wire.route import RouteClaimTerminalDenial

    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = RouteClaimOutcome(denied_terminal=RouteClaimTerminalDenial(chunk_id="ch_1", status="stopped"))
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())

    Fill(ctx).run()

    assert len(hub.claims) == 1  # the claim was actually attempted
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

    Fill(ctx).run()

    assert hub.claims == []
    assert store.list_active_leases() == []


@pytest.mark.unit
def test_fill_preparation_failure_skips_without_claiming(tmp_path):  # type: ignore[no-untyped-def]
    """A reset-on-acquire step failure aborts the fill — no bind, no claim, no spawn."""
    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    provider = FakeProvider({"e1": "/ws/e1"}, prepare_fail=True)
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict="pass"), probe=FakeProbe()
    )

    Fill(ctx).run()

    assert hub.claims == []
    assert store.list_active_leases() == []
    assert store.held_environment_ids() == []


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

    Fill(ctx).run()

    assert hub.claims == []  # no free slot


@pytest.mark.unit
def test_fill_releases_a_binding_the_hub_reports_terminal_with_no_route(tmp_path):  # type: ignore[no-untyped-def]
    """blizzard#202: FILL's crash reconciler must not fall through silently on a held
    binding whose chunk the hub reports with no live route in a status that is neither
    ``ready`` nor ``running`` — an unmatched shape leaks the binding indefinitely."""
    store = _store(tmp_path)
    # A binding survives with no lease at all — the chunk's last lease already closed
    # (e.g. `transitioned`), the observed-in-production shape.
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.DONE,
        current_node_id=None,
        latest_epoch=1,
        route=None,  # no live route — terminal, hub-side
    )
    hub.queue = []  # nothing new to fill — the reconciler is the only path that could act
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )

    Fill(ctx).run()

    assert store.held_environment_ids() == []
    assert store.live_tenure_chunk_ids() == []


# ADVANCE — exited worker (buffer) + PULL flush (deliver)


@pytest.mark.unit
def test_advance_buffers_completion_then_flush_enters_hub_node(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_git_commit_declaration(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        environment_id="e1",
        repo="toy-api",
        branch="e1",
        commit="abc123",
        declared_at=_NOW,
    )
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.HUB_NODE_TAKEN)]
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    wt = FakeWorktreeGit()
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(), worktree_git=wt)

    Advance(ctx).run()  # probe reports the worker dead (empty alive set) -> exit-is-done

    # The declared commit was verified read-only and the completion is BUFFERED — not
    # yet submitted.
    assert wt.verified_calls == [("file:///origins/toy-api.git", "e1", "abc123")]
    assert hub.completions == []
    buffered = [b for b in store.pending_outbound() if b.kind == "completion.submitted"]
    assert len(buffered) == 1 and buffered[0].lease_id == "lease_1"
    assert store.active_lease_for_chunk("ch_1") is not None  # still open, awaiting flush

    Pull(ctx).run()  # the flusher delivers the completion and drives the apply-response

    assert len(hub.completions) == 1
    chunk_id, submission = hub.completions[0]
    assert chunk_id == "ch_1"
    assert submission.choice == "pass"
    assert submission.epoch == 1
    assert submission.artifacts[0].commit_hash == "abc123"
    assert submission.artifacts[0].forge == "file:///origins/toy-api.git"
    assert store.active_lease_for_chunk("ch_1") is None  # build lease closed on flush
    assert store.held_environment_ids() == ["e1"]  # envs held for the hub node
    assert provider.released == []


@pytest.mark.unit
def test_advance_reports_and_drops_a_declaration_whose_verify_is_false(tmp_path):  # type: ignore[no-untyped-def]
    """A declared git commit whose read-only ``verify`` returns ``False`` is treated as
    *not covered*: the completion carries **no** ``git_commit`` artifact (Phase-2
    nudge), and is also reported, since silent non-coverage would let a chunk land done."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_git_commit_declaration(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        environment_id="e1",
        repo="toy-api",
        branch="e1",
        commit="abc123",
        declared_at=_NOW,
    )
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.HUB_NODE_TAKEN)]
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    wt = FakeWorktreeGit(False)  # every declaration fails verification
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(), worktree_git=wt)

    Advance(ctx).run()

    # verify WAS called on the declaration, but the False verdict dropped it, so the
    # buffered completion names no git-commit artifact.
    assert wt.verified_calls == [("file:///origins/toy-api.git", "e1", "abc123")]

    Pull(ctx).run()

    assert len(hub.completions) == 1
    _chunk_id, submission = hub.completions[0]
    assert submission.artifacts == []  # unverified -> dropped -> uncovered


@pytest.mark.unit
def test_advance_drives_only_the_declared_branch_never_head_inference(tmp_path):  # type: ignore[no-untyped-def]
    """ADVANCE drives ``verify(forge, branch, commit)`` only against the worker's own
    DECLARED branch, never the worktree's ambient HEAD (issue #143). Pinned structurally
    too: no fake here carries a ``push``/``_current_branch`` method to infer from."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_git_commit_declaration(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        environment_id="e1",
        repo="toy-api",
        branch="feature/worker-declared",
        commit="deadbeef",
        declared_at=_NOW,
    )
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.HUB_NODE_TAKEN)]
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    wt = FakeWorktreeGit()
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(), worktree_git=wt)

    Advance(ctx).run()

    # Only the read-only verify ran, over the worker's own declared branch — no branch
    # was ever inferred off any local HEAD, detached or otherwise.
    assert wt.verified_calls == [("file:///origins/toy-api.git", "feature/worker-declared", "deadbeef")]

    Pull(ctx).run()

    _chunk_id, submission = hub.completions[0]
    assert submission.artifacts[0].branch_name == "feature/worker-declared"

    # Structural guard: neither the Protocol nor the injected fake carries the machinery.
    for missing_attr in ("push", "find_produced_artifacts", "_current_branch"):
        assert not hasattr(IWorktreeGit, missing_attr)
        assert not hasattr(wt, missing_attr)
        with pytest.raises(AttributeError):
            getattr(wt, missing_attr)


@pytest.mark.unit
def test_advance_elicits_verdict_exactly_once_while_flush_pending(tmp_path):  # type: ignore[no-untyped-def]
    """A second ADVANCE before the flush must not re-elicit the buffered completion."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Advance(ctx).run()
    Advance(ctx).run()  # completion already buffered -> the lease is skipped

    assert len(harness.judged) == 1  # judged once
    buffered = [b for b in store.pending_outbound() if b.kind == "completion.submitted"]
    assert len(buffered) == 1


@pytest.mark.unit
def test_flush_next_spawns_next_node_in_place(tmp_path):  # type: ignore[no-untyped-def]
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

    Advance(ctx).run()
    Pull(ctx).run()

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "review" and lease.epoch == 2  # fresh epoch, same env
    assert store.held_environment_ids() == ["e1"]
    # The review node-step's fresh epoch is buffered for the flusher to report up so the
    # hub's fence advances — it rides the store-and-forward buffer, not an inline push.
    review_mints = [
        b for b in store.pending_outbound() if b.kind == LEASE_MINTED and json.loads(b.payload)["epoch"] == 2
    ]
    assert len(review_mints) == 1


# NODE-ENTRY RESUME (issue #115): session modes across a build -> review -> build cycle
# — component tier, real store, doubles only at the hub/harness/provider/probe seams.


@pytest.mark.component
def test_targeted_resume_returns_to_its_own_node_not_the_reviewers_fresh_session(tmp_path):  # type: ignore[no-untyped-def]
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

    # Phase 1 (FILL): first arrival at `build` — no session exists yet, so the targeted
    # `resume:build` lookup comes back empty and the spawn falls back to fresh.
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    harness1 = FakeHarness(
        handle=WorkerHandle(session_id="sess-build-1", pid=100, process_start_time="start-100"), verdict="pass"
    )
    ctx1 = make_context(store, hub=hub, provider=provider, harness=harness1, probe=FakeProbe(), clock=FixedClock(_NOW))
    Fill(ctx1).run()

    assert harness1.resume_froms == [None]  # (e) first arrival falls back to fresh
    build_lease_1 = store.active_lease_for_chunk("ch_1")
    assert build_lease_1 is not None and build_lease_1.session_id == "sess-build-1"

    # --- Phase 2 (ADVANCE + PULL): build passes; apply-response NEXT hands the chunk
    # to `review` (fresh) — always a new sid (assertion c).
    hub.envelopes["ch_1"] = build_env  # `_advance_exited_worker`'s own idempotent re-read
    hub.apply_responses = [
        ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=review_env),
        ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=build_env),
    ]
    harness2 = FakeHarness(
        handle=WorkerHandle(session_id="sess-review-1", pid=200, process_start_time="start-200"), verdict="fail"
    )
    ctx2 = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness2,
        probe=FakeProbe(),
        clock=FixedClock(_NOW + timedelta(minutes=1)),
    )
    Advance(ctx2).run()  # build worker "exited" (empty alive set) -> judged pass -> buffered
    Pull(ctx2).run()  # flush -> apply-response NEXT -> spawn review

    assert harness2.resume_froms == [None]  # (c) fresh review always gets a new sid
    review_lease = store.active_lease_for_chunk("ch_1")
    assert (
        review_lease is not None and review_lease.node_name == "review" and review_lease.session_id == "sess-review-1"
    )

    # Phase 3 (ADVANCE + PULL): review fails; apply-response routes back into build's
    # targeted `resume:build` — must resume BUILD's own session, not the reviewer's.
    hub.envelopes["ch_1"] = review_env  # `_advance_exited_worker`'s own idempotent re-read
    harness3 = FakeHarness(
        handle=WorkerHandle(session_id="sess-should-not-be-used", pid=300, process_start_time="start-300"),
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
    Advance(ctx3).run()  # review worker "exited" -> judged fail -> buffered
    Pull(ctx3).run()  # flush -> apply-response NEXT -> spawn build, resuming in place

    assert harness3.resume_froms == ["sess-build-1"]  # (b) resumes build's own session
    build_lease_2 = store.active_lease_for_chunk("ch_1")
    assert (
        build_lease_2 is not None and build_lease_2.node_name == "build" and build_lease_2.session_id == "sess-build-1"
    )


@pytest.mark.component
def test_bare_resume_uses_the_chunks_most_recent_session_not_the_nodes_own(tmp_path):  # type: ignore[no-untyped-def]
    """The contrast case: a node entered with plain `session: resume` (no target)
    resumes the chunk's most-recent session-bearing lease overall — here the reviewer's,
    not build's own prior one, the "wrong inheritance" plan Q4's `resume:build` avoids."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})

    build_env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES, session=SessionMode.RESUME)
    review_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES, session=SessionMode.FRESH)
    build_reentry_env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES, session=SessionMode.RESUME)

    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    harness1 = FakeHarness(
        handle=WorkerHandle(session_id="sess-build-1", pid=100, process_start_time="start-100"), verdict="pass"
    )
    ctx1 = make_context(store, hub=hub, provider=provider, harness=harness1, probe=FakeProbe(), clock=FixedClock(_NOW))
    Fill(ctx1).run()

    hub.envelopes["ch_1"] = build_env  # `_advance_exited_worker`'s own idempotent re-read
    hub.apply_responses = [
        ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=review_env),
        ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=build_reentry_env),
    ]
    harness2 = FakeHarness(
        handle=WorkerHandle(session_id="sess-review-1", pid=200, process_start_time="start-200"), verdict="fail"
    )
    ctx2 = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness2,
        probe=FakeProbe(),
        clock=FixedClock(_NOW + timedelta(minutes=1)),
    )
    Advance(ctx2).run()
    Pull(ctx2).run()

    hub.envelopes["ch_1"] = review_env  # `_advance_exited_worker`'s own idempotent re-read
    harness3 = FakeHarness(
        handle=WorkerHandle(session_id="sess-should-not-be-used", pid=300, process_start_time="start-300"),
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
    Advance(ctx3).run()
    Pull(ctx3).run()

    assert harness3.resume_froms == ["sess-review-1"]  # (a) chunk-most-recent, not build's own


@pytest.mark.component
def test_within_node_retry_stays_fresh_even_when_the_node_is_resume(tmp_path):  # type: ignore[no-untyped-def]
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
        handle=WorkerHandle(session_id="sess-b", pid=201, process_start_time="start-201"), verdict=None
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Advance(ctx).run()  # no parseable <Choice> -> failure -> requeue in place, fresh

    assert harness.resume_froms == [None]  # (d) a retry never resolves a resume target
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.epoch == 2 and lease.session_id == "sess-b"


# RESUME-TIME PREAMBLE ELISION (issue #149): component tier, proving the WIRING (store
# read -> renderer -> adapter prefix) the renderer's own unit tier cannot.


def _preamble_config(*, workspace_prompt: str = "WORKSPACE-POLICY", runner_prompt: str = "BLIZZARD-FRAMING"):  # type: ignore[no-untyped-def]
    return LoopConfig(
        runner_id="r1",
        workspace_id="ws1",
        workspace_root="/ws",
        workspace_prompt=workspace_prompt,
        runner_prompt=runner_prompt,
    )


def _first_build_spawn(store, hub, provider, env, *, session, at, config=None):  # type: ignore[no-untyped-def]
    """FILL's first arrival at `build` — a fresh session, nothing to resume."""
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    harness = FakeHarness(
        handle=WorkerHandle(session_id=session, pid=100, process_start_time="start-100"), verdict="pass"
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(at),
        config=config if config is not None else _preamble_config(),
    )
    Fill(ctx).run()
    return harness


def _reenter_build(store, hub, provider, env, *, session, pid, at, config=None):  # type: ignore[no-untyped-def]
    """One ADVANCE + PULL cycle re-entering `build` on its own prior session."""
    hub.envelopes["ch_1"] = env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=env)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id=session, pid=pid, process_start_time=f"start-{pid}"), verdict="pass"
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(at),
        config=config if config is not None else _preamble_config(),
    )
    Advance(ctx).run()
    Pull(ctx).run()
    return harness


def _resuming_build_env():  # type: ignore[no-untyped-def]
    return make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, session=SessionMode.RESUME, session_source="build"
    )


@pytest.mark.component
def test_fresh_spawn_sends_all_three_layers_and_records_a_fingerprint(tmp_path):  # type: ignore[no-untyped-def]
    """Scenario 1 (AC4): first arrival resumes nothing, so the prefix is exactly today's
    three-layer composition — and the spawn leaves behind the baseline its first resume
    will compare against."""
    store = _store(tmp_path)
    hub = FakeHub()
    env = _resuming_build_env()

    harness = _first_build_spawn(store, hub, FakeProvider({"e1": "/ws/e1"}), env, session="sess-build-1", at=_NOW)

    assert harness.resume_froms == [None]
    prefix = harness.spawns[0][1].prompt_prefix
    assert (
        prefix
        == (
            "BLIZZARD-FRAMING\n\n"
            "WORKSPACE-POLICY\n\n"
            "| Field | Value |\n"
            "|-------|-------|\n"
            "| runner id | `r1` |\n"
            "| chunk id | `ch_1` |\n"
            "| lease id | " + f"`{store.active_lease_for_chunk('ch_1').lease_id}` |\n"  # type: ignore[union-attr]
            "| environment name | `e1` |\n"
            "| environment workdir | `/ws/e1` |"
        )
    )
    assert store.session_preamble_fingerprint("sess-build-1") is not None


@pytest.mark.component
def test_resume_with_unchanged_prose_elides_and_keeps_eliding(tmp_path):  # type: ignore[no-untyped-def]
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

    second = _reenter_build(store, hub, provider, env, session="sess-build-1", pid=200, at=_NOW + timedelta(minutes=1))
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

    third = _reenter_build(store, hub, provider, env, session="sess-build-1", pid=300, at=_NOW + timedelta(minutes=2))
    third_prefix = third.spawns[0][1].prompt_prefix
    assert RESUME_STANDING_UNCHANGED in third_prefix
    assert RESUME_UPDATED_NOTICE not in third_prefix  # nothing changed — nothing announced
    assert "BLIZZARD-FRAMING" not in third_prefix
    assert "WORKSPACE-POLICY" not in third_prefix


@pytest.mark.component
def test_resume_after_a_live_workspace_prompt_replace_announces_the_new_prose(tmp_path):  # type: ignore[no-untyped-def]
    """Scenario 3 — the correctness half of the issue. `PUT /api/workspace-prompt` between
    two spawns of one session: the replacement reaches the worker announced, not disguised
    as the block it was handed a spawn ago."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    env = _resuming_build_env()

    _first_build_spawn(store, hub, provider, env, session="sess-build-1", at=_NOW)

    # The local API's write, landing between the two spawns.
    store.set_workspace_prompt("ws1", prompt="REPLACED-POLICY", at=_NOW + timedelta(seconds=30))

    second = _reenter_build(store, hub, provider, env, session="sess-build-1", pid=200, at=_NOW + timedelta(minutes=1))

    prefix = second.spawns[0][1].prompt_prefix
    assert prefix.startswith(RESUME_UPDATED_NOTICE)
    assert "REPLACED-POLICY" in prefix
    assert "WORKSPACE-POLICY" not in prefix  # the superseded prose is gone
    assert RESUME_BLIZZARD_UNCHANGED in prefix  # layer 1 did not move, so it stays collapsed
    assert "BLIZZARD-FRAMING" not in prefix


@pytest.mark.component
def test_resume_after_a_runner_prompt_change_announces_and_re_sends_layer_one(tmp_path):  # type: ignore[no-untyped-def]
    """Scenario 4 — layer 1's door. `runner_prompt` is a startup knob, so this is reachable
    only across a runner restart, but a resumed session outlives one."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    env = _resuming_build_env()

    _first_build_spawn(store, hub, provider, env, session="sess-build-1", at=_NOW)

    # The restarted runner's config — same workspace prompt, new blizzard framing.
    restarted = _preamble_config(runner_prompt="REFRAMED-BLIZZARD")
    second = _reenter_build(
        store, hub, provider, env, session="sess-build-1", pid=200, at=_NOW + timedelta(minutes=1), config=restarted
    )

    prefix = second.spawns[0][1].prompt_prefix
    assert prefix.startswith(f"{RESUME_UPDATED_NOTICE}\n\nREFRAMED-BLIZZARD\n\n")
    assert "BLIZZARD-FRAMING" not in prefix
    assert RESUME_WORKSPACE_UNCHANGED in prefix  # layer 2 held, so it collapses
    assert "WORKSPACE-POLICY" not in prefix


@pytest.mark.component
def test_a_resume_with_message_between_node_entries_does_not_disturb_the_fingerprint(tmp_path):  # type: ignore[no-untyped-def]
    """Scenario 5: a restart-resume re-records the SAME session id via `record_spawn`
    sending no `prompt_prefix`. The fingerprint write is reachable only from
    `Spawner.spawn`, so that path can't touch it and the next entry still elides."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    env = _resuming_build_env()

    _first_build_spawn(store, hub, provider, env, session="sess-build-1", at=_NOW)

    # --- The graceful-restart re-attach, interleaved: mark, then RESUME in place.
    ResumeIntents(store).mark_graceful(now=_NOW + timedelta(seconds=30))
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.RUNNING,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id="r1", workspace_id="ws1", environment_ids=["e1"]),
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
        )
    ).run()
    assert resume_harness.resumed  # the resume-with-message really ran

    # --- The next node entry on the same session still finds an honest fingerprint.
    second = _reenter_build(store, hub, provider, env, session="sess-build-1", pid=200, at=_NOW + timedelta(minutes=1))

    prefix = second.spawns[0][1].prompt_prefix
    assert RESUME_STANDING_UNCHANGED in prefix
    assert RESUME_UPDATED_NOTICE not in prefix
    assert "BLIZZARD-FRAMING" not in prefix


@pytest.mark.component
def test_an_announced_change_is_announced_once_and_then_elided(tmp_path):  # type: ignore[no-untyped-def]
    """Scenario 6 — the fingerprint is re-recorded on a **resumed** spawn, not just a
    fresh one. Three spawns, one workspace-prompt replace between the first and second:
    the third must elide, since the second spawn recorded what it actually sent."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    env = _resuming_build_env()

    _first_build_spawn(store, hub, provider, env, session="sess-build-1", at=_NOW)
    store.set_workspace_prompt("ws1", prompt="REPLACED-POLICY", at=_NOW + timedelta(seconds=30))

    second = _reenter_build(store, hub, provider, env, session="sess-build-1", pid=200, at=_NOW + timedelta(minutes=1))
    third = _reenter_build(store, hub, provider, env, session="sess-build-1", pid=300, at=_NOW + timedelta(minutes=2))

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
    settled = store.session_preamble_fingerprint("sess-build-1")
    assert settled is not None
    assert settled.workspace == hashlib.sha256(b"REPLACED-POLICY").hexdigest()


@pytest.mark.unit
def test_advance_review_harvests_findings_asset_from_assessment(tmp_path):  # type: ignore[no-untyped-def]
    """A node that `produces` a name no git commit covers emits the assessment as an asset."""
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
    store.record_spawn("lease_r", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
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
        worktree_git=FakeWorktreeGit(),
    )

    Advance(ctx).run()  # buffers the completion (with the harvested findings asset)
    Pull(ctx).run()  # the flusher delivers it to the hub (store-and-forward)

    _, submission = hub.completions[0]
    findings = [a for a in submission.artifacts if a.name == "review-findings"]
    assert len(findings) == 1
    assert findings[0].kind is ArtifactKind.ASSET
    assert findings[0].content == "BLOCKING: guard the empty input"
    assert findings[0].attached is False


@pytest.mark.unit
def test_advance_review_node_drives_no_git_commit_verify_or_artifact(tmp_path):  # type: ignore[no-untyped-def]
    """A node whose `produces:` is `[review-findings]` drives **no** git-commit
    declaration or verify at all — ``verify`` is never invoked and no ``GIT_COMMIT``
    artifact is produced, while findings still ride the asset path (issue #143)."""
    store = _store(tmp_path)
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
    store.record_spawn("lease_r", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    # No `record_git_commit_declaration` call — a review-only worker declares nothing.

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"]
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=_build_envelope())]
    harness = FakeHarness(handle=_HANDLE, verdict="fail", assessment="BLOCKING: guard the empty input")
    wt = FakeWorktreeGit()
    ctx = make_context(
        store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe(), worktree_git=wt
    )

    Advance(ctx).run()
    Pull(ctx).run()

    assert wt.verified_calls == []  # no declaration for this lease -> verify never called
    _chunk_id, submission = hub.completions[0]
    assert [a.kind for a in submission.artifacts] == [ArtifactKind.ASSET]  # no GIT_COMMIT artifact
    assert submission.artifacts[0].name == "review-findings"


@pytest.mark.unit
def test_collect_asset_artifacts_prefers_an_attachment_over_the_assessment():  # type: ignore[no-untyped-def]
    """A `produces` name with a durable attachment wins over the assessment (issue #113)."""
    envelope = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"])

    submitted = ProducesReconciler(envelope).collect_assets(
        [], "the assessment", {"review-findings": "attached content"}
    )

    assert len(submitted) == 1
    assert submitted[0].name == "review-findings"
    assert submitted[0].kind is ArtifactKind.ASSET
    assert submitted[0].content == "attached content"
    assert submitted[0].attached is True


@pytest.mark.unit
def test_collect_asset_artifacts_falls_back_to_the_assessment_when_unattached():  # type: ignore[no-untyped-def]
    """A `produces` name with no attachment still emits the assessment, unattached."""
    envelope = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings"])

    submitted = ProducesReconciler(envelope).collect_assets([], "the assessment", {})

    assert len(submitted) == 1
    assert submitted[0].content == "the assessment"
    assert submitted[0].attached is False


@pytest.mark.unit
def test_collect_asset_artifacts_multi_asset_node_does_not_alias_attached_and_unattached_names():  # type: ignore[no-untyped-def]
    """The #90 aliasing bug: two `produces` names, only one attached, must now differ."""
    envelope = make_envelope(
        "ch_1", "review", node_id="nd_review", choices=_CHOICES, produces=["review-findings", "review-diary"]
    )

    submitted = ProducesReconciler(envelope).collect_assets(
        [], "the shared assessment", {"review-findings": "the real findings"}
    )

    by_name = {a.name: a for a in submitted}
    assert by_name["review-findings"].content == "the real findings"
    assert by_name["review-findings"].attached is True
    assert by_name["review-diary"].content == "the shared assessment"
    assert by_name["review-diary"].attached is False
    # The exact bug: the two names aliasing to the same assessment string.
    assert by_name["review-findings"].content != by_name["review-diary"].content


@pytest.mark.unit
def test_collect_asset_artifacts_git_commit_precedence_over_an_attachment():  # type: ignore[no-untyped-def]
    """A name already covered by a git-commit artifact is never re-emitted as an asset,
    even when an attachment exists for that same name."""
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES, produces=["toy-api"])
    git_artifacts = [
        SubmittedArtifact(
            name="toy-api", kind=ArtifactKind.GIT_COMMIT, repo="toy-api", branch_name="b", commit_hash="deadbeef"
        )
    ]

    submitted = ProducesReconciler(envelope).collect_assets(
        git_artifacts, "assessment", {"toy-api": "should be ignored"}
    )

    assert submitted == []


@pytest.mark.unit
def test_collect_asset_artifacts_git_commit_spec_never_yields_a_phantom_asset():  # type: ignore[no-untyped-def]
    """A `git_commit`-kind `produces` spec is skipped by kind, not name — even when no
    git-commit artifact actually covers it (a build node whose worker declared nothing
    yet), so its name is never aliased to the judgement assessment as a phantom ASSET."""
    envelope = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=_CHOICES,
        produces=[ProducesEntry(name="commit", kind=ArtifactKind.GIT_COMMIT)],
    )

    submitted = ProducesReconciler(envelope).collect_assets([], "assessment", {})

    assert submitted == []


@pytest.mark.unit
def test_flush_done_releases_environments(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict="pass"), probe=FakeProbe()
    )

    Advance(ctx).run()
    Pull(ctx).run()

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

    Advance(ctx).run()  # worker alive -> nothing judged, nothing polled

    assert store.pending_outbound() == []
    assert store.active_lease_for_chunk("ch_1") is not None


# Store-and-forward across an outage


@pytest.mark.unit
def test_completion_survives_hub_outage_and_applies_once(tmp_path):  # type: ignore[no-untyped-def]
    """Hub down mid-work -> completion buffered -> hub back -> flush applies exactly once."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.HUB_NODE_TAKEN)]
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )

    Advance(ctx).run()  # worker exited -> completion buffered

    hub.down = True
    Pull(ctx).run()  # flush fails — the completion stays buffered
    assert hub.completions == []
    assert [b.kind for b in store.pending_outbound()] == ["completion.submitted"]
    assert store.active_lease_for_chunk("ch_1") is not None  # not advanced

    hub.down = False
    Pull(ctx).run()  # hub back -> flush applies
    Pull(ctx).run()  # a redundant extra drain must not resubmit (buffer already acked)

    assert len(hub.completions) == 1  # applied exactly once
    assert store.pending_outbound() == []
    assert store.active_lease_for_chunk("ch_1") is None


# ADVANCE — hub-node poll


@pytest.mark.unit
def test_poll_hub_node_releases_on_done(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    # A chunk held at a hub node: a binding but no active lease.
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.DONE,
        current_node_id="deliver",
        latest_epoch=1,
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict="pass"), probe=FakeProbe()
    )

    Advance(ctx).run()

    assert provider.released == ["e1"]
    assert store.held_environment_ids() == []


@pytest.mark.unit
def test_poll_hub_node_waits_while_delivering(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.DELIVERING,
        current_node_id="deliver",
        latest_epoch=1,
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict="pass"), probe=FakeProbe()
    )

    Advance(ctx).run()

    assert provider.released == []  # still delivering — hold
    assert store.held_environment_ids() == ["e1"]
    # #66 re-drive path: a chunk parked at a hub node is polled via hub-advance every
    # tick — the mechanism that closes the deferred/pending-node liveness gap.
    assert hub.hub_advance_calls == ["ch_1"]


@pytest.mark.unit
def test_advance_held_chunk_spawns_into_post_merge_node(tmp_path):  # type: ignore[no-untyped-def]
    """#63: an authored ``merged -> <node>`` edge lands the chunk into a post-merge
    runner node while the coordinator retains the route. ADVANCE discovers the fresh
    transition and spawns into the already-held, warm environment."""
    store = _store(tmp_path)
    # A chunk held at the (former) deliver hub node: its prior build lease is closed, the
    # binding retained; the coordinator advanced it into ``verify`` at a HIGHER hub_epoch.
    store.record_lease(
        NewLease(
            lease_id="lease_build",
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
    store.record_closure(
        lease_id="lease_build", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.RUNNING,
        current_node_id="nd_verify",
        current_node_name="verify",
        latest_epoch=2,  # the coordinator's hub_epoch — ahead of the runner's minted epoch 1
    )
    hub.envelopes["ch_1"] = make_envelope("ch_1", "verify", node_id="nd_verify", choices=_CHOICES)
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())

    Advance(ctx).run()

    assert len(harness.spawns) == 1
    spawned_envelope, _ = harness.spawns[0]
    assert spawned_envelope.node.node_name == "verify"
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "verify"
    assert provider.released == []  # merged but running — still held, no release
    assert store.held_environment_ids() == ["e1"]


@pytest.mark.unit
def test_advance_held_chunk_does_not_respawn_a_buffered_escalation(tmp_path):  # type: ignore[no-untyped-def]
    """The epoch gate: a locally-escalated chunk whose fact is still buffered is NOT
    re-spawned. Until the buffered escalation flushes, the hub still derives ``running``
    at the SAME epoch — only a strictly-higher hub epoch is a genuine advance."""
    store = _store(tmp_path)
    # The runner minted a build lease at epoch 2, it failed retries-exhausted, the lease is
    # closed and the escalation buffered (not asserted here). The binding is retained.
    store.record_lease(
        NewLease(
            lease_id="lease_esc",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=2,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_closure(lease_id="lease_esc", chunk_id="ch_1", node_id="nd_build", reason="failed", closed_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    # The hub has NOT advanced: it still reads running at the SAME epoch the runner minted (2),
    # because the escalation.recorded fact has not flushed yet.
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.RUNNING,
        current_node_id="nd_build",
        current_node_name="build",
        latest_epoch=2,
    )
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())

    Advance(ctx).run()

    assert harness.spawns == []  # no re-spawn — the epoch gate held
    assert store.active_lease_for_chunk("ch_1") is None  # nothing minted
    assert store.held_environment_ids() == ["e1"]  # binding retained for the flush → needs_human


@pytest.mark.unit
def test_advance_held_chunk_with_no_binding_and_no_active_lease_is_a_noop(tmp_path):  # type: ignore[no-untyped-def]
    """A chunk with no binding at all never reaches ``_advance_held_chunk`` — it isn't
    in ``live_tenure_chunk_ids()`` — so a bare ADVANCE tick over an empty store spawns
    nothing (the degenerate case the new branch must not misfire on)."""
    store = _store(tmp_path)
    hub = FakeHub()
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )

    Advance(ctx).run()

    assert store.held_environment_ids() == []


# Failure, requeue, escalation


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

    Advance(ctx).run()  # no parseable <Choice> -> failure -> requeue in place (local, no hub call)

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

    Reap(ctx).run()

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.lease_id != "lease_1"  # a fresh lease replaced the orphan
    assert lease.pid == 202


@pytest.mark.unit
def test_reap_stalled_but_alive_worker(tmp_path):  # type: ignore[no-untyped-def]
    """A live worker whose heartbeat has gone stale is reaped as stalled."""
    store = _store(tmp_path)
    _seed_running_lease(store)  # created_at = _NOW, pid 100 alive
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    later = _NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(minutes=5)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=999, process_start_time="start-999"), verdict="pass"
    )
    probe = FakeProbe(alive={_ALIVE})  # pid 100 is still alive
    ctx = make_context(
        store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe, clock=FixedClock(later)
    )

    Reap(ctx).run()

    assert 100 in probe.killed  # the stalled worker was killed (best-effort hygiene)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.lease_id != "lease_1"  # requeued as a fresh attempt
    assert lease.epoch == 2


@pytest.mark.unit
def test_reap_leaves_fresh_beating_worker(tmp_path):  # type: ignore[no-untyped-def]
    """A live worker inside the staleness window is left running."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    soon = _NOW + timedelta(minutes=1)  # well within the threshold
    hub = FakeHub()
    probe = FakeProbe(alive={_ALIVE})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=probe,
        clock=FixedClock(soon),
    )

    Reap(ctx).run()

    assert probe.killed == []
    survivor = store.active_lease_for_chunk("ch_1")
    assert survivor is not None and survivor.lease_id == "lease_1"  # untouched


@pytest.mark.unit
def test_reap_leaves_exited_worker_for_advance(tmp_path):  # type: ignore[no-untyped-def]
    """An exited (dead-pid) worker is ADVANCE's exit-is-done, not REAP's."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    later = _NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(minutes=5)  # heartbeat is stale
    hub = FakeHub()
    probe = FakeProbe(alive=set())  # pid 100 has exited
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=probe,
        clock=FixedClock(later),
    )

    Reap(ctx).run()

    assert probe.killed == []  # not reaped — exit-is-done belongs to ADVANCE
    survivor = store.active_lease_for_chunk("ch_1")
    assert survivor is not None and survivor.lease_id == "lease_1"


@pytest.mark.unit
def test_retries_exhausted_escalates_and_holds_envs(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()  # retries_max = 2
    # A runtime dir with whitespace (issue #251) — proves the composed wrapped command
    # stays shell-safe (shlex.quote) rather than merely happening to work on a plain path.
    runner_dir = "/tmp/runner dir/r1"
    config = LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1, runner_dir=runner_dir)
    # Three verdict-less attempts: attempt 1 & 2 requeue, attempt 3 escalates.
    provider = FakeProvider({"e1": "/ws/e1"})
    for i in range(1, 4):
        handle = WorkerHandle(session_id=f"sess-{i}", pid=300 + i, process_start_time=f"start-{i}")
        harness = FakeHarness(handle=handle, verdict=None)
        ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(), config=config)
        if i == 1:
            _seed_running_lease(store, pid=300, start="start-0")
        Advance(ctx).run()

    assert store.active_lease_for_chunk("ch_1") is None  # no more retries
    escalations = [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED]
    assert len(escalations) == 1
    assert store.held_environment_ids() == ["e1"]  # envs held for takeover
    assert provider.released == []
    # The buffered escalation.recorded carries the pasteable takeover command;
    # the flusher reports it up to POST /events, where the fleet derives needs_human.
    payload = json.loads(escalations[0].payload)
    assert payload["chunk_id"] == "ch_1"
    assert payload["takeover_command"].startswith("cd /ws/e1 &&") and "--resume" in payload["takeover_command"]
    # The wrapped, supported entry point (issue #251) — composed under the same guard,
    # alongside the raw fallback above.
    wrapped = payload["wrapped_takeover_command"]
    assert wrapped == f"blizzard runner takeover ch_1 --dir {shlex.quote(runner_dir)}"
    # shlex.quote round-trips: splitting the composed command back out yields the
    # original whitespace-bearing runner_dir verbatim, not a truncated/mangled token.
    assert shlex.split(wrapped)[-1] == runner_dir


@pytest.mark.unit
def test_escalation_without_a_session_composes_neither_takeover_command(tmp_path):  # type: ignore[no-untyped-def]
    """A lease escalated before it ever recorded a session (`session_id` still `None`)
    composes no takeover command at all — with nothing to resume there is no raw command
    to build, so nothing for the wrapped verb (issue #251) to wrap either."""
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
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.session_id is None  # never spawned

    hub = FakeHub()
    config = LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1, runner_dir="/tmp/runner-dir")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        config=config,
    )

    Attempt(ctx, lease).escalate()

    escalations = [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED]
    assert len(escalations) == 1
    payload = json.loads(escalations[0].payload)
    assert payload["takeover_command"] == ""
    assert payload["wrapped_takeover_command"] == ""


@pytest.mark.unit
def test_escalation_with_a_session_but_no_binding_composes_neither_takeover_command(tmp_path):  # type: ignore[no-untyped-def]
    """A lease with a recorded session that was never bound to an environment composes
    neither command and raises nothing: `and bindings` guards the `IndexError`. The
    *released*-binding shape is its own case below."""
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
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.session_id == "sess-a"  # spawned, but no binding recorded

    hub = FakeHub()
    config = LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1, runner_dir="/tmp/runner-dir")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        config=config,
    )

    Attempt(ctx, lease).escalate()

    escalations = [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED]
    assert len(escalations) == 1
    payload = json.loads(escalations[0].payload)
    assert payload["takeover_command"] == ""
    assert payload["wrapped_takeover_command"] == ""


@pytest.mark.unit
def test_escalation_after_its_bindings_were_released_still_escalates(tmp_path):  # type: ignore[no-untyped-def]
    """The doubted state built the way the funnel reaches it (blizzard#280): a binding recorded
    and then *released*, as `abandon` releases it. It escalates anyway — the chunk still needs a
    human — and warns which of `humans.md`'s reasons produced a command-less escalation."""
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
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_release(chunk_id="ch_1", environment_id="e1", released_at=_NOW)
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.session_id == "sess-a"
    assert store.bindings_for_chunk("ch_1") == []  # released, not merely never bound

    config = LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1, runner_dir="/tmp/runner-dir")
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        config=config,
    )

    from structlog.testing import capture_logs

    with capture_logs() as logs:
        Attempt(ctx, lease).escalate()

    escalations = [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED]
    assert len(escalations) == 1
    payload = json.loads(escalations[0].payload)
    assert payload["chunk_id"] == "ch_1"
    assert payload["takeover_command"] == ""
    assert payload["wrapped_takeover_command"] == ""
    # The fields, not the prose, are what tell a released binding apart from a lease that
    # never had a session — the two reasons `humans.md` §Escalation keeps distinct.
    warned = [entry for entry in logs if entry["event"] == "escalating with no takeover command"]
    assert len(warned) == 1
    assert warned[0]["log_level"] == "warning"
    assert warned[0]["has_session"] is True
    assert warned[0]["bound_envs"] == 0


# Per-chunk spend cap (issue #61a)


def _cap_config(cap, runner_dir="/tmp/runner-dir"):  # type: ignore[no-untyped-def]
    return LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1, chunk_cap_usd=cap, runner_dir=runner_dir)


@pytest.mark.unit
def test_cost_cap_parks_needs_human_at_next_step_boundary(tmp_path):  # type: ignore[no-untyped-def]
    """Crossing the cap parks the chunk `needs_human` instead of spawning its next attempt."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    next_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=next_env)]
    hub.chunks["ch_1"] = _chunk_with_cost(cost_usd=7.0)  # over the $5 cap
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=_cap_config(5.0),
    )

    Advance(ctx).run()  # the attempt finishes and its completion is buffered — not yet applied
    Pull(ctx).run()  # the flush applies it (NEXT); the cap check runs at that boundary and parks

    # No next attempt spawned — the cap parked before `Spawner.spawn`, not by killing anyone.
    assert harness.spawns == []
    assert store.active_lease_for_chunk("ch_1") is None
    escalations = [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED]
    assert len(escalations) == 1
    payload = json.loads(escalations[0].payload)
    assert payload["chunk_id"] == "ch_1"
    # The takeover resumes the just-finished attempt's own session — a human's entry point
    # back into the chunk, the same shape a retries-exhausted escalation carries.
    assert payload["takeover_command"].startswith("cd /ws/e1 &&") and "--resume sess-a" in payload["takeover_command"]
    # The cap-park caller shares the same composition-root `ctx` as every other
    # `Attempt.escalate` call — with `runner_dir` configured, it composes a real wrapped command.
    assert payload["wrapped_takeover_command"] == "blizzard runner takeover ch_1 --dir /tmp/runner-dir"
    # Envs stay held for the takeover; nothing was released on a cap park.
    assert store.held_environment_ids() == ["e1"]


@pytest.mark.unit
def test_cost_cap_park_leaves_wrapped_empty_without_runner_dir(tmp_path):  # type: ignore[no-untyped-def]
    """A cap park with no `runner_dir` configured composes the raw fallback normally but
    leaves the wrapped command empty, same as any other `Attempt.escalate` caller — there is no
    runtime dir to fill `--dir` with."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    next_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=next_env)]
    hub.chunks["ch_1"] = _chunk_with_cost(cost_usd=7.0)  # over the $5 cap
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=_cap_config(5.0, runner_dir=""),
    )

    Advance(ctx).run()
    Pull(ctx).run()

    escalations = [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED]
    assert len(escalations) == 1
    payload = json.loads(escalations[0].payload)
    assert payload["takeover_command"].startswith("cd /ws/e1 &&") and "--resume sess-a" in payload["takeover_command"]
    assert payload["wrapped_takeover_command"] == ""


@pytest.mark.unit
def test_cost_cap_park_does_not_consume_a_retry(tmp_path):  # type: ignore[no-untyped-def]
    """A cap park is not a failed attempt: the closed lease reads `transitioned`, not
    `escalated`/`failed`, and the next node's attempt count stays at zero — a later
    resume mints its first real attempt at that node, not a second one."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    next_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=next_env)]
    hub.chunks["ch_1"] = _chunk_with_cost(cost_usd=7.0)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=_cap_config(5.0),
    )

    Advance(ctx).run()
    Pull(ctx).run()

    assert store.attempt_count("ch_1", "nd_review") == 0  # no lease ever minted for the next node


@pytest.mark.unit
def test_cost_cap_never_kills_a_live_worker(tmp_path):  # type: ignore[no-untyped-def]
    """The cap is checked between attempts only: a chunk still mid-attempt (worker alive)
    is left running untouched even though the hub already reports it over cap."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    hub.chunks["ch_1"] = _chunk_with_cost(cost_usd=99.0)  # far over cap, but the worker is alive
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    probe = FakeProbe(alive={_ALIVE})  # the worker is still running
    ctx = make_context(
        store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe, config=_cap_config(5.0)
    )

    Advance(ctx).run()

    # ADVANCE never even looked at this lease — still running, no judgement, no park.
    assert harness.judged == []
    assert store.pending_outbound() == []
    assert store.active_lease_for_chunk("ch_1") is not None
    assert probe.killed == []


@pytest.mark.unit
def test_cost_cap_under_cap_continues_normally(tmp_path):  # type: ignore[no-untyped-def]
    """Spend below the cap spawns the next node exactly as an uncapped runner would."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    next_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=next_env)]
    hub.chunks["ch_1"] = _chunk_with_cost(cost_usd=1.0)  # well under the $5 cap
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200"), verdict="pass"
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=_cap_config(5.0),
    )

    Advance(ctx).run()
    Pull(ctx).run()

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "review" and lease.epoch == 2
    assert ESCALATION_RECORDED not in [b.kind for b in store.pending_outbound()]


@pytest.mark.unit
def test_cost_cap_absent_never_parks_regardless_of_spend(tmp_path):  # type: ignore[no-untyped-def]
    """`chunk_cap_usd` unset (today's default) never parks a chunk, however much it spent."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    next_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=next_env)]
    hub.chunks["ch_1"] = _chunk_with_cost(cost_usd=9999.0)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200"), verdict="pass"
    )
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Advance(ctx).run()
    Pull(ctx).run()

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "review"
    assert ESCALATION_RECORDED not in [b.kind for b in store.pending_outbound()]


@pytest.mark.unit
def test_cost_cap_partial_total_trips_the_lower_bound_and_logs_partial(tmp_path):  # type: ignore[no-untyped-def]
    """A cost-absent row makes the total PARTIAL (a lower bound); the cap still trips on
    that lower bound, and the escalation log line states PARTIAL so an operator reading
    the takeover is never told a partial total is the whole spend."""
    from structlog.testing import capture_logs

    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    next_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=next_env)]
    hub.chunks["ch_1"] = _chunk_with_cost(cost_usd=5.0, cost_partial=True)  # exactly at the cap, partial
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=_cap_config(5.0),
    )

    Advance(ctx).run()
    with capture_logs() as captured:
        Pull(ctx).run()

    assert store.active_lease_for_chunk("ch_1") is None  # parked despite being only a lower bound
    escalations = [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED]
    assert len(escalations) == 1
    park_events = [e for e in captured if "spend cap exceeded" in e.get("event", "")]
    assert len(park_events) == 1
    assert park_events[0]["cost_partial"] is True
    assert "PARTIAL" in park_events[0]["event"]
    escalate_events = [e for e in captured if e.get("event", "").startswith("escalated to needs-human")]
    assert len(escalate_events) == 1
    assert "PARTIAL" in escalate_events[0]["event"] and "spend cap" in escalate_events[0]["event"]


@pytest.mark.unit
def test_cost_cap_raised_then_requeued_resumes_normally(tmp_path):  # type: ignore[no-untyped-def]
    """A capped-then-requeued chunk resumes normally once the cap is raised — no special
    un-park logic beyond the standard requeue path FILL's interrupted-claim reconciler
    already drives."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    next_env = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES)
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=next_env)]
    hub.chunks["ch_1"] = _chunk_with_cost(cost_usd=7.0)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=_cap_config(5.0),
    )
    Advance(ctx).run()
    Pull(ctx).run()
    assert store.active_lease_for_chunk("ch_1") is None  # parked

    # The operator raises the cap and requeues at the hub: `running` with a live route
    # and no active lease — exactly FILL's interrupted-claim shape.
    hub.envelopes["ch_1"] = next_env
    hub.chunks["ch_1"] = _chunk_with_cost(cost_usd=7.0, status=ChunkStatus.RUNNING, route_runner_id="r1")
    ctx2 = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=_cap_config(100.0),  # raised well above the $7 spend
    )

    Fill(ctx2).run()  # `_reconcile_interrupted_claims` adopts — spawns the current (review) node

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "review"
    assert len(harness.spawns) == 1


# Full happy path across ticks


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
    wt = FakeWorktreeGit()
    probe = FakeProbe(alive={_ALIVE})  # worker alive during tick 1
    clock = FixedClock(_NOW)
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=probe, worktree_git=wt, clock=clock)

    # Tick 1: FILL claims + spawns (buffers lease.minted); the worker is alive.
    tick(ctx)
    assert store.active_lease_for_chunk("ch_1") is not None
    assert hub.completions == []

    # The worker finishes and exits.
    probe.alive.clear()

    # Tick 2: PULL flushes lease.minted; ADVANCE judges the exited worker and buffers
    # its completion.
    tick(ctx)
    assert [f.kind for f in hub.pushed] == [LEASE_MINTED]
    assert hub.completions == []
    assert store.active_lease_for_chunk("ch_1") is not None  # awaiting flush

    # Tick 3: PULL flushes the completion -> deliver hub node; envs held.
    tick(ctx)
    assert len(hub.completions) == 1
    assert store.active_lease_for_chunk("ch_1") is None
    assert store.held_environment_ids() == ["e1"]

    # The hub's merge queue lands the delivery; nothing left to peek.
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.DONE,
        current_node_id="deliver",
        latest_epoch=1,
    )
    hub.queue = []

    # Tick 4: the hub-node poll sees `done` and releases the environment.
    tick(ctx)
    assert provider.released == ["e1"]
    assert store.held_environment_ids() == []
    assert store.live_tenure_chunk_ids() == []


@pytest.mark.unit
def test_spawn_prefixes_static_workspace_prompt_and_sets_workspace_root(tmp_path):  # type: ignore[no-untyped-def]
    """The preamble carries the workspace root (spawn cwd) and the static config prompt (issue #17)."""
    store = _store(tmp_path)
    hub = FakeHub()
    env = _build_envelope()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(
            runner_id="r1",
            workspace_id="ws1",
            workspace_root="/ws",
            workspace_prompt="STATIC-PROMPT",
        ),
    )

    Fill(ctx).run()

    _, preamble = harness.spawns[0]
    assert preamble.workspace_root == "/ws"
    # Layer 1 (the baked blizzard preamble, since runner_prompt is unset) leads,
    # followed by the static workspace prompt (issue #103).
    assert preamble.prompt_prefix.startswith(f"{DEFAULT_BLIZZARD_PREAMBLE}\n\nSTATIC-PROMPT\n\n")
    assert "| environment name | `e1` |" in preamble.prompt_prefix


@pytest.mark.unit
def test_spawn_reflects_runtime_prompt_override_with_no_restart(tmp_path):  # type: ignore[no-untyped-def]
    """A store override (the PUT /api/workspace-prompt write) wins over static at the next spawn."""
    store = _store(tmp_path)
    # Simulate the local-API replace landing before this spawn.
    store.set_workspace_prompt("ws1", prompt="OVERRIDDEN", at=datetime(2026, 7, 13, tzinfo=UTC))
    hub = FakeHub()
    env = _build_envelope()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", workspace_prompt="STATIC-PROMPT"),
    )

    Fill(ctx).run()

    _, preamble = harness.spawns[0]
    assert preamble.prompt_prefix.startswith(f"{DEFAULT_BLIZZARD_PREAMBLE}\n\nOVERRIDDEN\n\n")
    assert "STATIC-PROMPT" not in preamble.prompt_prefix


def _engine_for(tmp_path):  # type: ignore[no-untyped-def]
    """A migrated engine for the counting store below (mirrors `make_store`)."""
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'runner.db'}")
    runner_metadata.create_all(engine)
    return engine


class _CountingPreambleStore(SqlAlchemyRunnerStore):
    """The real store with one read counted (issue #149) — a subclass, not a hand-written
    double, so every other method the loop touches keeps its genuine behaviour."""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine)
        self.fingerprint_reads: list[str] = []

    def session_preamble_fingerprint(self, session_id: str) -> PreambleFingerprint | None:
        self.fingerprint_reads.append(session_id)
        return super().session_preamble_fingerprint(session_id)


@pytest.mark.unit
def test_prior_preamble_is_read_only_when_the_spawn_resumes(tmp_path):  # type: ignore[no-untyped-def]
    """The lookup is resume-**gated**, not merely resume-shaped (issue #149): hoisting
    the read above the `resume_from` check passes every other test here (a fresh session
    has no prior row *yet*) but silently elides prose once a session id is reused."""
    store = _CountingPreambleStore(_engine_for(tmp_path))
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    build_env = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=_CHOICES, session=SessionMode.RESUME, session_source="build"
    )

    # --- A fresh spawn: first arrival at `build`, nothing to resume.
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    harness1 = FakeHarness(
        handle=WorkerHandle(session_id="sess-build-1", pid=100, process_start_time="start-100"), verdict="pass"
    )
    ctx1 = make_context(store, hub=hub, provider=provider, harness=harness1, probe=FakeProbe(), clock=FixedClock(_NOW))
    Fill(ctx1).run()

    assert harness1.resume_froms == [None]
    assert store.fingerprint_reads == []  # never looked one up

    # --- A resumed spawn: build re-entered on its own prior session.
    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=build_env)]
    harness2 = FakeHarness(
        handle=WorkerHandle(session_id="sess-build-1", pid=200, process_start_time="start-200"), verdict="pass"
    )
    ctx2 = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness2,
        probe=FakeProbe(),
        clock=FixedClock(_NOW + timedelta(minutes=1)),
    )
    Advance(ctx2).run()
    Pull(ctx2).run()

    assert harness2.resume_froms == ["sess-build-1"]
    assert store.fingerprint_reads == ["sess-build-1"]  # exactly one, for the resumed session


@pytest.mark.unit
def test_an_empty_resume_from_is_not_treated_as_a_resume(tmp_path):  # type: ignore[no-untyped-def]
    """The core's "is this a resume?" predicate matches the ADAPTER's (issue #149): the
    adapter treats an empty `resume_from` as a brand-new session, while a core keyed on
    `is not None` would look up a fingerprint for `""` and could wrongly elide."""
    store = _CountingPreambleStore(_engine_for(tmp_path))
    hub = FakeHub()
    envelope = _build_envelope()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    Spawner(ctx).spawn("ch_1", envelope, [AcquiredEnvironment("e1", "/ws/e1")], via="test", resume_from="")

    assert store.fingerprint_reads == [], "an empty resume_from was treated as a resume"
    # And the prefix is a full fresh render, not a collapse banner.
    assert RESUME_STANDING_UNCHANGED not in harness.spawns[0][1].prompt_prefix


@pytest.mark.unit
def test_advance_harvests_git_commits_from_every_bound_environment(tmp_path):  # type: ignore[no-untyped-def]
    """A chunk holding two environments has a worktree of the same repo in each. Reading
    only the first binding would drop the second env's work with no error at all, so the
    harvest spans every binding, verified against its own environment's origin."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_binding(chunk_id="ch_1", environment_id="e2", workdir="/ws/e2", bound_at=_NOW)
    for env, commit in (("e1", "aaa111"), ("e2", "bbb222")):
        store.record_git_commit_declaration(
            lease_id="lease_1",
            chunk_id="ch_1",
            node_id="nd_build",
            epoch=1,
            environment_id=env,
            repo="toy-api",
            branch=f"feat/from-{env}",
            commit=commit,
            declared_at=_NOW,
        )
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.HUB_NODE_TAKEN)]
    provider = FakeProvider(
        {"e1": "/ws/e1", "e2": "/ws/e2"},
        repos={
            "e1": [("toy-api", "file:///origins/toy-api.git")],
            "e2": [("toy-api", "file:///origins/toy-api.git")],
        },
    )
    wt = FakeWorktreeGit()
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        worktree_git=wt,
    )

    Advance(ctx).run()
    Pull(ctx).run()

    assert len(wt.verified_calls) == 2  # both envs' declarations were checked
    _chunk_id, submission = hub.completions[0]
    branches = sorted(a.branch_name or "" for a in submission.artifacts if a.kind is ArtifactKind.GIT_COMMIT)
    assert branches == ["feat/from-e1", "feat/from-e2"]


@pytest.mark.unit
def test_pull_registers_every_declared_redirect_uri(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    uris = (
        "http://127.0.0.1:8431/api/auth/callback",
        "https://tailnet.example:8431/api/auth/callback",
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        config=LoopConfig(
            runner_id="r1",
            workspace_id="ws1",
            public_url="http://127.0.0.1:8431",
            redirect_uris=uris,
        ),
    )

    Pull(ctx).run()

    assert hub.registered_redirect_uris == [uris]
    assert hub.registered_urls == ["http://127.0.0.1:8431"]
