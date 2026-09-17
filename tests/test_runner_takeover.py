"""``blizzard runner takeover`` — the domain service + loop-guard (component tier, issue #52).

Drives ``TakeoverService`` against a real tmp store with fakes at the seams: the three
parked shapes each open cleanly with no force; a live worker attempt refuses without
``--force`` and is superseded (fact, kill, fence, no retry/escalation) with it. A
second slice drives REAP/ADVANCE against an open takeover."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import SessionMode
from blizzard.foundation.tokens import TokenHash
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD, NewLease
from blizzard.runner.domain.takeover import (
    ChunkNotTakeable,
    LiveWorkerConflict,
    SubmissionPending,
    TakeoverCloseScope,
    TakeoverEndedElsewhere,
    TakeoverOpenScope,
    TakeoverService,
)
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.loop.session import SessionResolver
from blizzard.runner.loop.spawn import Spawner
from blizzard.runner.loop.steps import Advance, Fill, Reap
from blizzard.wire.chunk import ChunkStatusView
from blizzard.wire.facts import LEASE_MINTED
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
    make_stores,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _service(store, *, clock=None, harness=None, probe=None):  # type: ignore[no-untyped-def]
    return TakeoverService(
        make_stores(store),
        clock or FixedClock(_NOW),
        probe or FakeProbe(),
        local_api_url="http://127.0.0.1:8431",
        harnesses=HarnessRegistry(
            {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness or FakeHarness(handle=_HANDLE, verdict=None))}
        ),
    )


def _seed_lease(
    store,
    *,
    chunk="ch_1",
    lease="lease_1",
    node_id="nd_build",
    node_name="build",
    epoch=1,
    pid=100,
    session="sess-a",
    session_name=None,
    resolved_model=None,
    resolved_effort=None,
):  # type: ignore[no-untyped-def]
    """A build lease, spawned and bound — the shape every scenario below starts from.

    The session stamps (issue #144) default to unset, i.e. the pre-#144 shape: every
    scenario that says nothing about them asserts today's bare resume command."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            runner_id="r1",
            retries_max=2,
            session_name=session_name,
            resolved_model=resolved_model,
            resolved_effort=resolved_effort,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease,
        pid=pid,
        process_start_time=f"start-{pid}",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, session),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _open_scope(store, chunk_id: str = "ch_1") -> TakeoverOpenScope:  # type: ignore[no-untyped-def]
    """The edge resolution `chunk_scope.resolved_takeover_open_scope` performs in production."""
    return TakeoverOpenScope(
        chunk_id=chunk_id,
        open_takeover=store.open_takeover_for_chunk(chunk_id),
        bindings=store.bindings_for_chunk(chunk_id),
        active_lease=store.active_lease_for_chunk(chunk_id),
        latest_lease_with_session=store.latest_lease_with_session_for_chunk(chunk_id),
        latest_epoch=store.latest_epoch(chunk_id),
    )


def _close_scope(store, chunk_id: str = "ch_1") -> TakeoverCloseScope:  # type: ignore[no-untyped-def]
    """The edge resolution `chunk_scope.resolved_takeover_close_scope` performs in production."""
    return TakeoverCloseScope(chunk_id=chunk_id, open_takeover=store.open_takeover_for_chunk(chunk_id))


# The three parked shapes — happy path, no force
# --------------------------------------------------------------------------- #


def test_takeover_opens_over_an_ask_parked_chunk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    opened = _service(store).open(_open_scope(store), force=False)

    assert opened.command == "cd /ws/e1 && claude --resume sess-a"
    assert opened.workdir == "/ws/e1"
    record = store.open_takeover_for_chunk("ch_1")
    assert record is not None
    assert record.takeover_id == opened.takeover_id
    assert record.lease_id == "lease_1"
    assert record.session_id == "sess-a"
    assert record.fence_epoch is None  # nothing live to fence
    assert "ch_1" in store.open_takeover_chunk_ids()
    assert store.pending_outbound() == []  # no fence bump enqueued — a dormant lease needs none


def test_takeover_opens_over_a_needs_human_chunk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)

    opened = _service(store).open(_open_scope(store), force=False)

    assert opened.command == "cd /ws/e1 && claude --resume sess-a"
    record = store.open_takeover_for_chunk("ch_1")
    assert record is not None
    assert record.lease_id == "lease_1"  # the closed escalated lease, recovered via latest_lease_with_session
    assert record.fence_epoch is None


def test_takeover_after_a_node_entry_escalation_resolves_the_prior_session(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A plain resume's owner unresolvable at node entry (`Spawner.enter_node`) mints a
    session-less lease newer than the prior session it could not resume. Once the owner is
    restored, a takeover must still resolve against that prior session, not the empty mint."""
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
    store.record_spawn(
        "lease_1", pid=1, process_start_time="t1", session=SessionReference("foreign", "sess-old"), spawned_at=_NOW
    )
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    default = FakeHarness(handle=_HANDLE, verdict="pass")
    unresolved = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=default, transcript_source=default.transcript_source())}
    )
    hub = FakeHub()
    envelope = make_envelope(
        "ch_1",
        "verify",
        node_id="nd_verify",
        choices=[("pass", "meets criteria"), ("fail", "does not")],
        session=SessionMode.RESUME,
        session_source="build",  # a targeted, un-pooled resume — no `session_name`
    )
    hub.envelopes["ch_1"] = envelope
    now2 = _NOW + timedelta(minutes=5)  # strictly after the prior session's own mint
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=default,
        probe=FakeProbe(),
        clock=FixedClock(now2),
    )
    ctx = replace(
        ctx, harnesses=unresolved, sessions=SessionResolver(leases=store, harnesses=unresolved, transcripts_wired=True)
    )

    Spawner(ctx).enter_node(
        "ch_1", envelope, [AcquiredEnvironment("e1", "/ws/e1")], via="test"
    )  # `foreign` unresolvable

    assert store.active_lease_for_chunk("ch_1") is None
    assert len([e for e in store.open_escalations() if e.chunk_id == "ch_1"]) == 1

    # The owner is restored.
    resolved = HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=default, transcript_source=default.transcript_source()),
            "foreign": HarnessBinding(adapter=default, transcript_source=default.transcript_source()),
        }
    )
    service = TakeoverService(
        make_stores(store), FixedClock(_NOW), FakeProbe(), local_api_url="http://127.0.0.1:8431", harnesses=resolved
    )

    opened = service.open(_open_scope(store), force=False)

    assert opened.command == "cd /ws/e1 && claude --resume sess-old"
    record = store.open_takeover_for_chunk("ch_1")
    assert record is not None
    assert record.lease_id == "lease_1"
    assert record.session_id == "sess-old"


def test_takeover_opens_over_a_gate_parked_chunk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="parked", closed_at=_NOW)

    opened = _service(store).open(_open_scope(store), force=False)

    record = store.open_takeover_for_chunk("ch_1")
    assert record is not None
    assert record.lease_id == "lease_1"
    assert opened.workdir == "/ws/e1"


def test_takeover_refuses_a_chunk_with_no_held_binding(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    with pytest.raises(ChunkNotTakeable):
        _service(store).open(_open_scope(store, "ch_missing"), force=False)


def test_takeover_refuses_a_second_open_takeover(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    service = _service(store)
    service.open(_open_scope(store), force=False)

    with pytest.raises(ChunkNotTakeable):
        service.open(_open_scope(store), force=False)


# A live worker attempt — 409 without force, superseded with it
# --------------------------------------------------------------------------- #


def test_takeover_refuses_a_live_worker_without_force(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store)  # active, not parked — a live attempt

    with pytest.raises(LiveWorkerConflict):
        _service(store).open(_open_scope(store), force=False)

    # Refusing must not touch anything: no takeover fact, no kill, no fence.
    assert store.open_takeover_for_chunk("ch_1") is None
    assert store.pending_outbound() == []


def test_forced_takeover_orders_fact_before_kill_fences_the_epoch_and_consumes_no_retry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store, pid=100)

    class _OrderingProbe(FakeProbe):
        """Records whether the takeover fact was already durable at kill time."""

        def __init__(self) -> None:
            super().__init__(alive={(100, "start-100")})
            self.fact_open_at_kill: bool | None = None

        def kill(self, pid: int) -> None:
            self.fact_open_at_kill = store.open_takeover_for_chunk("ch_1") is not None
            super().kill(pid)

    probe = _OrderingProbe()
    opened = _service(store, probe=probe).open(_open_scope(store), force=True)

    # Fact-before-kill (bzh:crash-correctness): the fact was already durable the
    # instant the kill ran.
    assert probe.fact_open_at_kill is True
    assert probe.killed == [100]

    # The command is still returned, over the live worker's own session.
    assert opened.command == "cd /ws/e1 && claude --resume sess-a"

    record = store.open_takeover_for_chunk("ch_1")
    assert record is not None
    assert record.fence_epoch == 2  # latest_epoch (1) + 1 — the fence bump

    # The fence rides the outbound buffer as an ordinary lease.minted fact, so a late
    # completion from the killed worker's session lands on a stale epoch.
    pending = store.pending_outbound()
    assert len(pending) == 1
    assert pending[0].kind == LEASE_MINTED
    assert '"epoch": 2' in pending[0].payload

    # latest_epoch is now fenced past the killed attempt, even though no local lease
    # was minted for it — a later real spawn would not reuse epoch 2.
    assert store.latest_epoch("ch_1") == 2

    # No retry consumed: attempt_count only counts lease_context rows written at mint,
    # and the takeover writes none.
    assert store.attempt_count("ch_1", "nd_build") == 1

    # No escalation recorded: a live worker attempt under takeover is superseded, not
    # failed, so the lease is not closed at all.
    assert store.open_escalations() == []
    assert store.lease("lease_1") is not None


def test_forced_takeover_refuses_a_lease_with_a_pending_submission(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The worker exited with its completion already buffered but the lease still
    ``live``; a fence minted now would arrive behind PULL's strict FIFO, too late to
    matter, so ``--force`` must refuse instead."""
    store = _store(tmp_path)
    _seed_lease(store, pid=100)
    store.enqueue_outbound(
        kind="completion.submitted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW
    )

    with pytest.raises(SubmissionPending):
        _service(store).open(_open_scope(store), force=True)

    # Refusing must not touch anything: no takeover fact, no kill, no fresh fence
    # enqueued — the buffer holds only the pre-existing completion.
    assert store.open_takeover_for_chunk("ch_1") is None
    pending = store.pending_outbound()
    assert len(pending) == 1
    assert pending[0].kind == "completion.submitted"


def test_takeover_close_marks_it_ended(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    service = _service(store)
    opened = service.open(_open_scope(store), force=False)

    service.close(_close_scope(store), opened.takeover_id)

    assert store.open_takeover_for_chunk("ch_1") is None
    assert "ch_1" not in store.open_takeover_chunk_ids()


def test_takeover_close_on_a_different_open_takeover_raises(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    service = _service(store)
    service.open(_open_scope(store), force=False)

    with pytest.raises(TakeoverEndedElsewhere):
        service.close(_close_scope(store), "tko_bogus")


def test_takeover_close_is_idempotent_once_already_ended(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Ending an already-ended takeover is the desired state (issue #291), not an error —
    the shape the CLI's own end-PATCH ``finally`` needs when ``Pull`` closes it first."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    service = _service(store)
    opened = service.open(_open_scope(store), force=False)
    service.close(_close_scope(store), opened.takeover_id)

    service.close(_close_scope(store), opened.takeover_id)  # does not raise


def test_takeover_close_with_no_takeover_ever_opened_is_a_no_op(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    service = _service(store)

    service.close(_close_scope(store), "tko_bogus")  # does not raise


# The loop guard — REAP/ADVANCE skip a chunk under an open takeover
# --------------------------------------------------------------------------- #


def test_reap_skips_a_stalled_worker_under_an_open_takeover(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store, pid=100)
    # A forced takeover already killed pid 100 and fenced the chunk — this mirrors that
    # end state without going through the API, isolating REAP's own guard.
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=2,
        opened_at=_NOW,
    )
    probe = FakeProbe(alive=set())  # pid already dead
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    clock = FixedClock(_NOW + HEARTBEAT_STALENESS_THRESHOLD * 2)  # long stale, would ordinarily reap
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=probe, clock=clock)

    Reap(ctx).run()

    # Untouched: no closure recorded, no fresh mint, no kill attempted a second time.
    assert store.active_lease("lease_1") is not None
    assert probe.killed == []
    assert store.attempt_count("ch_1", "nd_build") == 1


def test_advance_skips_judgement_and_the_held_chunk_poll_under_an_open_takeover(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store, pid=100)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    probe = FakeProbe(alive=set())  # exited — would ordinarily be judged
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=probe, clock=FixedClock(_NOW))

    Advance(ctx).run()

    assert harness.judged == []  # never resumed to elicit a verdict
    assert store.pending_outbound() == []  # no completion buffered
    assert store.active_lease("lease_1") is not None  # left exactly as it was


def test_advance_skips_the_held_chunk_gate_hub_node_poll_under_an_open_takeover(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    # A gate-parked chunk: a held binding, no active lease (its lease already closed
    # "parked"), and an open takeover over it.
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    hub = FakeHub()
    # Scripted DONE: if the guard failed to skip, `_advance_held_chunk` would poll this
    # and release the binding — an observable side effect the assertion below catches.
    hub.chunks["ch_1"] = ChunkStatusView(
        chunk_id="ch_1",
        status=ChunkStatus.DONE,
        latest_epoch=1,
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store,
        hub=hub,
        provider=provider,
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    Advance(ctx).run()

    assert provider.released == []
    assert store.held_environment_ids() == ["e1"]


# `InterruptedClaims.reconcile`'s deliberate absence of an open-takeover skip.
# --------------------------------------------------------------------------- #


def test_fill_reclaims_a_park_the_hub_superseded_even_under_an_open_takeover(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A hub-side park-supersede reaches this reconcile arm and reclaims into a fresh
    attempt at the held binding, deliberately, even while an open takeover still stands
    over the chunk — the park's own guarantee does not outlive the park itself."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkStatusView(chunk_id="ch_1", status=ChunkStatus.READY, latest_epoch=1)
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    hub.claim_outcome = claimed_outcome("ch_1", env)
    hub.queue = []  # nothing new to fill — only the interrupted-claim reclaim should act
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Fill(ctx).run()

    assert len(hub.claims) == 1  # reclaimed rather than skipped
    assert len(harness.spawns) == 1
    assert store.active_lease_for_chunk("ch_1") is not None


def test_fill_adopts_a_restart_against_a_lease_the_escalation_already_closed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A restart against a lease an escalation already closed reaches this reconcile arm
    and adopts undeferred, deliberately, even while an already-fenced takeover still
    stands open over the chunk."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    store.set_route_token("ch_1", token="tok_x", at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=2,
        opened_at=_NOW,
    )
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkStatusView(
        chunk_id="ch_1", status=ChunkStatus.RUNNING, latest_epoch=2, route_runner_id="r1"
    )
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], epoch=2)
    hub.queue = []
    harness = FakeHarness(handle=_HANDLE, verdict=None)
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe())

    Fill(ctx).run()

    assert len(harness.spawns) == 1  # adopted rather than skipped
    assert store.active_lease_for_chunk("ch_1") is not None


# The takeover reads the session's stamps (D4, issue #144).
# --------------------------------------------------------------------------- #


def test_takeover_composes_its_command_from_the_sessions_own_stamps(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A read, not a re-resolution: the operator continues under exactly the
    configuration the session ran with — the deliberate exception to mint-only, which
    exists for prompt-cache efficiency on runner-driven resumes only."""
    store = _store(tmp_path)
    _seed_lease(store, session_name="code", resolved_model="opus", resolved_effort="high")
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    opened = _service(store).open(_open_scope(store), force=False)

    assert opened.command == "cd /ws/e1 && claude --resume sess-a --model opus --effort high"
    # And it names WHICH lineage is being taken over, not just an opaque session id.
    assert opened.session_name == "code"


def test_takeover_of_a_session_predating_the_stamps_renders_the_bare_command(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store)  # no stamps — *unknown*
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    opened = _service(store).open(_open_scope(store), force=False)

    assert opened.command == "cd /ws/e1 && claude --resume sess-a"
    assert opened.session_name is None


def test_takeover_carries_the_lease_identity_env_and_reminting_its_token(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The taken-over session's worker identity (issue #258): ``--resume`` inherits no
    spawn env, so the takeover carries it — with a freshly re-minted capability token,
    invalidating the prior one, and never baked into the printable command."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    store.record_lease_token("lease_1", "prior-token-hash", _NOW)  # the spawn's original mint

    opened = _service(store).open(_open_scope(store), force=False)

    assert opened.env["BLIZZARD_CHUNK_ID"] == "ch_1"
    assert opened.env["BLIZZARD_LEASE_ID"] == "lease_1"
    assert opened.env["BLIZZARD_SESSION_ID"] == "sess-a"
    assert opened.env["BLIZZARD_ENV_IDS"] == "e1"
    assert opened.env["BLIZZARD_ENV_WORKDIRS"] == "/ws/e1"
    assert opened.env["BLIZZARD_RUNNER_URL"] == "http://127.0.0.1:8431"
    token = opened.env["BLIZZARD_LEASE_TOKEN"]
    assert token
    stored = store.lease_token_hash("lease_1")
    assert stored == TokenHash(token).hex  # re-minted and recorded...
    assert stored != "prior-token-hash"  # ...INVALIDATING the spawn's original token
    assert token not in opened.command  # env only — never a printable surface


def test_takeover_env_is_bounded_to_identity_plus_path_and_home(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """What leaves the daemon is a bounded set (issue #258 review): BLIZZARD_* identity
    plus PATH/HOME — never the daemon's TERM or an ``env_passthrough`` secret."""
    store = _store(tmp_path)
    _seed_lease(store)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)

    opened = _service(store).open(_open_scope(store), force=False)

    # Forwarded: the execution vars an interactive resume needs from the daemon side.
    assert opened.env["PATH"] == "/daemon/venv/bin:/usr/bin"
    assert opened.env["HOME"] == "/daemon/home"
    # Withheld: the rest of the daemon's child env (FakeHarness plants both).
    assert "TERM" not in opened.env
    assert "FAKE_PASSTHROUGH_SECRET" not in opened.env
    assert set(opened.env) == {"PATH", "HOME"} | {k for k in opened.env if k.startswith("BLIZZARD_")}
