"""The reconciliation step functions — the loop logic (unit tier).

Each step is driven directly against a real tmp store with fakes at the seams
(``bzh:steppable-loop``): FILL claims and spawns (buffering ``lease.minted``), ADVANCE
judges an exited worker and **buffers** its completion, PULL's flusher delivers the
buffer and drives the apply-response (store-and-forward), a hub-node hold polls
to release, REAP expires an orphan and a stalled-but-alive worker, and the retry budget
requeues then escalates. The full happy path is exercised as a sequence of ticks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.work import DEFAULT_MODEL, ChunkStatus
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import advance, fill, pull, reap
from blizzard.runner.loop.tick import tick
from blizzard.runner.loop.worktree import GitArtifact
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, ChunkUsageTotalView, RouteView
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from blizzard.wire.facts import ESCALATION_RECORDED, LEASE_MINTED
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
        model=DEFAULT_MODEL,
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

    fill(ctx)

    _, preamble = harness.spawns[0]
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    assert preamble.lease_id == lease.lease_id
    assert preamble.local_api_url == "http://127.0.0.1:9999"


@pytest.mark.unit
def test_route_token_never_reaches_the_worker_preamble_or_prompt(tmp_path):  # type: ignore[no-untyped-def]
    """Containment (issue #84a): the route token lives only in the runner store and
    stamped outbound payloads — never in ``WorkerPreamble``, so it can never reach the
    worker's environment or its rendered prompt (``WorkerPreamble`` carries no such
    field by construction; this pins the behavior, not just the shape)."""
    store = _store(tmp_path)
    hub = FakeHub()
    env = _build_envelope()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env, route_token="super-secret-route-token")
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    fill(ctx)

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

    fill(ctx)

    # The lease.minted rides the outbound buffer (store-and-forward); PULL's
    # flusher reports it up to POST /events, so it is not pushed inline at spawn.
    buffered = [b for b in store.pending_outbound() if b.kind == LEASE_MINTED]
    assert len(buffered) == 1
    assert json.loads(buffered[0].payload) == {"chunk_id": "ch_1", "epoch": 1, "route_token": "rtok_test"}
    pull(ctx)
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

    fill(ctx)

    assert store.route_token("ch_1") == "rtok-abc123"


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

    fill(ctx)  # claims and stashes the token, spawns the worker
    advance(ctx)  # worker "exited" (FakeProbe reports it dead) — judged and buffered

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

    fill(ctx)  # claims (epoch 1), stashes the token
    advance(ctx)  # verdict-less exit -> fail attempt -> requeue in place (fresh lease, epoch 2)

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

    fill(ctx)

    assert provider.released == ["e1"]  # released the acquired-but-unclaimed env
    assert store.held_environment_ids() == []
    assert store.list_active_leases() == []


@pytest.mark.unit
def test_fill_paused_denial_releases_and_stops_filling(tmp_path):  # type: ignore[no-untyped-def]
    """A 403 (issue #44) is a distinct outcome from a 409 conflict: the hub's registry
    already has this runner paused, so the claim was refused outright rather than lost
    to another runner — FILL releases the binding and stops trying further slots this
    tick rather than keep racing claims the hub will refuse the same way."""
    from blizzard.runner.loop.hub import RouteClaimOutcome
    from blizzard.wire.route import RouteClaimPausedDenial

    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = RouteClaimOutcome(denied_paused=RouteClaimPausedDenial(chunk_id="ch_1", runner_id="r1"))
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())

    fill(ctx)

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

    fill(ctx)

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

    fill(ctx)

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

    fill(ctx)

    assert hub.claims == []  # no free slot


# --------------------------------------------------------------------------- #
# ADVANCE — exited worker (buffer) + PULL flush (deliver)
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_advance_buffers_completion_then_flush_enters_hub_node(tmp_path):  # type: ignore[no-untyped-def]
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

    # The branch is pushed and the completion is BUFFERED — not yet submitted.
    assert wt.pushed == [("/ws/e1/toy-api", "e1")]
    assert hub.completions == []
    buffered = [b for b in store.pending_outbound() if b.kind == "completion.submitted"]
    assert len(buffered) == 1 and buffered[0].lease_id == "lease_1"
    assert store.active_lease_for_chunk("ch_1") is not None  # still open, awaiting flush

    pull(ctx)  # the flusher delivers the completion and drives the apply-response

    assert len(hub.completions) == 1
    chunk_id, submission = hub.completions[0]
    assert chunk_id == "ch_1"
    assert submission.choice == "pass"
    assert submission.epoch == 1
    assert submission.artifacts[0].commit_hash == "abc123"
    assert store.active_lease_for_chunk("ch_1") is None  # build lease closed on flush
    assert store.held_environment_ids() == ["e1"]  # envs held for the hub node
    assert provider.released == []


@pytest.mark.unit
def test_advance_elicits_verdict_exactly_once_while_flush_pending(tmp_path):  # type: ignore[no-untyped-def]
    """A second ADVANCE before the flush must not re-elicit the buffered completion."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    advance(ctx)
    advance(ctx)  # completion already buffered -> the lease is skipped

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

    advance(ctx)
    pull(ctx)

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "review" and lease.epoch == 2  # fresh epoch, same env
    assert store.held_environment_ids() == ["e1"]
    # The review node-step's fresh epoch is buffered for the flusher to report up so the
    # hub's fence advances — it rides the store-and-forward buffer, not an inline push.
    review_mints = [
        b for b in store.pending_outbound() if b.kind == LEASE_MINTED and json.loads(b.payload)["epoch"] == 2
    ]
    assert len(review_mints) == 1


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
        worktree_git=FakeWorktreeGit([]),
    )

    advance(ctx)  # buffers the completion (with the harvested findings asset)
    pull(ctx)  # the flusher delivers it to the hub (store-and-forward)

    _, submission = hub.completions[0]
    findings = [a for a in submission.artifacts if a.name == "review-findings"]
    assert len(findings) == 1
    assert findings[0].kind is ArtifactKind.ASSET
    assert findings[0].content == "BLOCKING: guard the empty input"


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

    advance(ctx)
    pull(ctx)

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

    assert store.pending_outbound() == []
    assert store.active_lease_for_chunk("ch_1") is not None


# --------------------------------------------------------------------------- #
# Store-and-forward across an outage
# --------------------------------------------------------------------------- #


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

    advance(ctx)  # worker exited -> completion buffered

    hub.down = True
    pull(ctx)  # flush fails — the completion stays buffered
    assert hub.completions == []
    assert [b.kind for b in store.pending_outbound()] == ["completion.submitted"]
    assert store.active_lease_for_chunk("ch_1") is not None  # not advanced

    hub.down = False
    pull(ctx)  # hub back -> flush applies
    pull(ctx)  # a redundant extra drain must not resubmit (buffer already acked)

    assert len(hub.completions) == 1  # applied exactly once
    assert store.pending_outbound() == []
    assert store.active_lease_for_chunk("ch_1") is None


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
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.DONE,
        current_node_id="deliver",
        latest_epoch=1,
        model=DEFAULT_MODEL,
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
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.DELIVERING,
        current_node_id="deliver",
        latest_epoch=1,
        model=DEFAULT_MODEL,
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=FakeHarness(handle=_HANDLE, verdict="pass"), probe=FakeProbe()
    )

    advance(ctx)

    assert provider.released == []  # still delivering — hold
    assert store.held_environment_ids() == ["e1"]
    # #66 re-drive path: a chunk parked at a hub node is polled via hub-advance every
    # tick — the mechanism that closes the deferred/pending-node liveness gap.
    assert hub.hub_advance_calls == ["ch_1"]


@pytest.mark.unit
def test_advance_held_chunk_spawns_into_post_merge_node(tmp_path):  # type: ignore[no-untyped-def]
    """#63: an authored ``merged -> <node>`` edge lands the chunk into a post-merge
    runner node while the coordinator retains the route (no release). ADVANCE
    discovers the fresh transition — no active lease, the hub reports ``running``,
    not ``delivering`` — and spawns the current node into the already-held, warm
    environment (the same :func:`_spawn_attempt` path ``NEXT`` uses)."""
    store = _store(tmp_path)
    # A chunk held at the (former) deliver hub node: its prior build lease (epoch 1) is
    # closed, the binding retained. The coordinator then landed and advanced the chunk into
    # ``verify`` under its own ``hub_epoch = 2`` — a HIGHER epoch than this runner has minted.
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
        model=DEFAULT_MODEL,
    )
    hub.envelopes["ch_1"] = make_envelope("ch_1", "verify", node_id="nd_verify", choices=_CHOICES)
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())

    advance(ctx)

    assert len(harness.spawns) == 1
    spawned_envelope, _ = harness.spawns[0]
    assert spawned_envelope.node.node_name == "verify"
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "verify"
    assert provider.released == []  # merged but running — still held, no release
    assert store.held_environment_ids() == ["e1"]


@pytest.mark.unit
def test_advance_held_chunk_does_not_respawn_a_buffered_escalation(tmp_path):  # type: ignore[no-untyped-def]
    """The epoch gate: a locally-escalated chunk whose fact is still buffered is NOT re-spawned.

    When a node exhausts its retries the runner enqueues ``escalation.recorded`` to its outbound
    buffer and closes the lease, but until that flushes the hub still derives ``running`` — at the
    **same** epoch this runner last minted. ``_advance_held_chunk`` must NOT mistake that for a hub
    advance and re-spawn the escalated node: firing on ``status == running`` alone loops forever
    (spawn → verdict-less fail → escalate → hub still running → spawn …). Only a strictly-higher hub
    epoch is a genuine advance, so here — hub epoch == the runner's minted epoch — nothing spawns."""
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
        model=DEFAULT_MODEL,
    )
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())

    advance(ctx)

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

    advance(ctx)

    assert store.held_environment_ids() == []


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

    advance(ctx)  # no parseable <Choice> -> failure -> requeue in place (local, no hub call)

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

    reap(ctx)

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

    reap(ctx)

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

    reap(ctx)

    assert probe.killed == []  # not reaped — exit-is-done belongs to ADVANCE
    survivor = store.active_lease_for_chunk("ch_1")
    assert survivor is not None and survivor.lease_id == "lease_1"


@pytest.mark.unit
def test_retries_exhausted_escalates_and_holds_envs(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()  # retries_max = 2
    # Three verdict-less attempts: attempt 1 & 2 requeue, attempt 3 escalates.
    provider = FakeProvider({"e1": "/ws/e1"})
    for i in range(1, 4):
        handle = WorkerHandle(session_id=f"sess-{i}", pid=300 + i, process_start_time=f"start-{i}")
        harness = FakeHarness(handle=handle, verdict=None)
        ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())
        if i == 1:
            _seed_running_lease(store, pid=300, start="start-0")
        advance(ctx)

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


# --------------------------------------------------------------------------- #
# Per-chunk spend cap (issue #61a)
# --------------------------------------------------------------------------- #


def _cap_config(cap):  # type: ignore[no-untyped-def]
    return LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1, chunk_cap_usd=cap)


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

    advance(ctx)  # the attempt finishes and its completion is buffered — not yet applied
    pull(ctx)  # the flush applies it (NEXT); the cap check runs at that boundary and parks

    # No next attempt spawned — the cap parked before `_spawn_attempt`, not by killing anyone.
    assert harness.spawns == []
    assert store.active_lease_for_chunk("ch_1") is None
    escalations = [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED]
    assert len(escalations) == 1
    payload = json.loads(escalations[0].payload)
    assert payload["chunk_id"] == "ch_1"
    # The takeover resumes the just-finished attempt's own session — a human's entry point
    # back into the chunk, the same shape a retries-exhausted escalation carries.
    assert payload["takeover_command"].startswith("cd /ws/e1 &&") and "--resume sess-a" in payload["takeover_command"]
    # Envs stay held for the takeover; nothing was released on a cap park.
    assert store.held_environment_ids() == ["e1"]


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

    advance(ctx)
    pull(ctx)

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

    advance(ctx)

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

    advance(ctx)
    pull(ctx)

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

    advance(ctx)
    pull(ctx)

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

    advance(ctx)
    with capture_logs() as captured:
        pull(ctx)

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
    advance(ctx)
    pull(ctx)
    assert store.active_lease_for_chunk("ch_1") is None  # parked

    # The operator raises the cap and requeues at the hub — the hub closes the escalation
    # by supersession and the chunk's route stays live (`_has_live_route`), so it re-derives
    # `running` with no active lease here: exactly FILL's interrupted-claim shape. The hub's
    # current node is now `review` (the completion already applied before the park), so its
    # idempotent envelope re-read reflects that.
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

    fill(ctx2)  # `_reconcile_interrupted_claims` adopts — spawns the current (review) node

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "review"
    assert len(harness.spawns) == 1


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
        model=DEFAULT_MODEL,
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

    fill(ctx)

    _, preamble = harness.spawns[0]
    assert preamble.workspace_root == "/ws"
    assert preamble.prompt_prefix.startswith("STATIC-PROMPT\n\n")
    assert "| winter environment name | `e1` |" in preamble.prompt_prefix


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

    fill(ctx)

    _, preamble = harness.spawns[0]
    assert preamble.prompt_prefix.startswith("OVERRIDDEN\n\n")
    assert "STATIC-PROMPT" not in preamble.prompt_prefix
