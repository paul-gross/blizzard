"""Every other ``adapter_for``/``harnesses.adapter`` call ``Advance.run()`` (or a step it
shares a per-lease loop with) reaches escalates the chunk in place rather than raising or
resuming blind. ``Judgement._elicit``/``collect`` share the same guard ``DormantSession``'s
three wakes reuse; ``TranscriptPump``'s segment read logs instead (tested apart);
``Attempt.escalate`` — reached only once the escalation's own closure is already durable —
can only cost the takeover command a resolvable owner would have composed."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import SessionMode
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.loop.attempt import REAPED, Attempt
from blizzard.runner.loop.steps import Advance
from blizzard.wire.chunk import ChunkStatusView
from blizzard.wire.facts import ESCALATION_RECORDED, EVENT_RECORDED, RUNNER_LOCALLY_PAUSED
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_envelope,
    make_store,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_FOREIGN_HARNESS_ID = "foreign"
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _build_envelope(chunk="ch_1"):  # type: ignore[no-untyped-def]
    return make_envelope(chunk, "build", node_id="nd_build", choices=_CHOICES)


def _registry(default_harness, *, unavailable: bool) -> HarnessRegistry:
    """The default owner alone (an unregistered ``foreign`` id reads as unknown), or —
    when ``unavailable`` — ``foreign`` registered with no adapter bound (a known owner this
    runner cannot use right now, not an unheard-of one)."""
    bindings = {
        CLAUDE_CODE_HARNESS_ID: HarnessBinding(
            adapter=default_harness, transcript_source=default_harness.transcript_source()
        ),
    }
    if unavailable:
        bindings[_FOREIGN_HARNESS_ID] = HarnessBinding(
            adapter=None, transcript_source=default_harness.transcript_source()
        )
    return HarnessRegistry(bindings)


def _done_chunk(chunk_id: str) -> ChunkStatusView:
    return ChunkStatusView(chunk_id=chunk_id, status=ChunkStatus.DONE, latest_epoch=1)


def _assert_escalated_once_with_no_takeover(store, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    """The lease closed ``escalated``, exactly one ``escalation.recorded`` fact rode the
    outbound buffer for it, and it composed no takeover command — an owner this runner
    cannot resolve can never compose one."""
    escalations = [e for e in store.open_escalations() if e.chunk_id == chunk_id]
    assert len(escalations) == 1
    events = [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED and b.chunk_id == chunk_id]
    assert len(events) == 1
    payload = json.loads(events[0].payload)
    assert payload["chunk_id"] == chunk_id
    assert payload["takeover_command"] == ""
    assert payload["wrapped_takeover_command"] == ""


@pytest.mark.parametrize("unavailable", [False, True], ids=["unknown-owner", "unavailable-owner"])
def test_judgement_launch_blocked_by_unresolvable_owner_escalates_in_place(tmp_path, unavailable):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_blocked",
            chunk_id="ch_blocked",
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
        "lease_blocked",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(_FOREIGN_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_blocked", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_binding(chunk_id="ch_done", environment_id="e_done", workdir="/ws/e_done", bound_at=_NOW)

    hub = FakeHub()
    hub.envelopes["ch_blocked"] = _build_envelope("ch_blocked")
    hub.chunks["ch_done"] = _done_chunk("ch_done")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="pass"
    )
    probe = FakeProbe(alive=set())  # exited
    provider = FakeProvider({"e1": "/ws/e1", "e_done": "/ws/e_done"})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=probe, clock=FixedClock(_NOW))
    ctx = replace(ctx, harnesses=_registry(harness, unavailable=unavailable))

    Advance(ctx).run()  # must not raise

    # Never even reached `judge` — the chunk escalates in place instead, clearing the
    # in-flight record `_launch` wrote first as part of that same closure.
    assert harness.judged == []
    assert store.in_flight_elicitation("lease_blocked", 1) is None
    assert store.active_lease("lease_blocked") is None
    _assert_escalated_once_with_no_takeover(store, "ch_blocked")

    assert provider.released == ["e_done"]
    assert store.held_environment_ids() == ["e1"]

    # Replay after a crash never double-escalates — the lease is already closed.
    Advance(ctx).run()
    _assert_escalated_once_with_no_takeover(store, "ch_blocked")


@pytest.mark.parametrize("unavailable", [False, True], ids=["unknown-owner", "unavailable-owner"])
def test_judgement_collect_blocked_by_unresolvable_owner_escalates_in_place(tmp_path, unavailable):  # type: ignore[no-untyped-def]
    """The owner resolved fine at launch, then stopped resolving before collect — e.g. an
    operator mid-repair. ``collect`` must not misread that as a lost write and relaunch — it
    escalates instead: no other runner can resume this exact session either."""
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
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope("ch_1")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="pass"
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(alive=set()), clock=FixedClock(_NOW)
    )

    Advance(ctx).run()  # launch — the owner resolves fine here
    assert len(harness.judged) == 1
    launched = store.in_flight_elicitation("lease_1", 1)
    assert launched is not None and launched.pid is not None

    # The registry now can't serve THIS session's own owner — a swapped-in registry models a
    # runtime config change: gone outright (unknown), or adapter withdrawn (unavailable).
    blocked_registry = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=None, transcript_source=harness.transcript_source())}
        if unavailable
        else {}
    )
    blocked_ctx = replace(ctx, harnesses=blocked_registry)

    Advance(blocked_ctx).run()  # must not raise

    # Not lost, not relaunched, not (re-)judged — the chunk escalates in place instead,
    # clearing the in-flight record as part of that same closure.
    assert store.in_flight_elicitation("lease_1", 1) is None
    assert len(harness.judged) == 1
    assert store.active_lease("lease_1") is None
    _assert_escalated_once_with_no_takeover(store, "ch_1")

    # Replay after a crash never double-escalates — the lease is already closed.
    Advance(blocked_ctx).run()
    _assert_escalated_once_with_no_takeover(store, "ch_1")


def test_judgement_collect_blocked_by_unresolvable_owner_defers_while_locally_paused(tmp_path):  # type: ignore[no-untyped-def]
    """`escalate_owner_unresolvable` takes the same locally-paused precedence `fail` takes:
    ADVANCE's sweep still reaches `collect`, but the escalation itself defers rather than
    closing the lease, exactly as `fail`'s own paused branch defers."""
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
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope("ch_1")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="pass"
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(alive=set()), clock=FixedClock(_NOW)
    )

    Advance(ctx).run()  # launch — the owner resolves fine here
    assert len(harness.judged) == 1
    launched = store.in_flight_elicitation("lease_1", 1)
    assert launched is not None and launched.pid is not None

    # The recorded owner stops resolving, same shape as the escalating sibling test, but this
    # runner is now also locally paused — the operator brake, not a per-lease park.
    blocked_ctx = replace(ctx, harnesses=HarnessRegistry({}))
    store.record_local_pause(
        "r1",
        paused=True,
        at=ctx.clock.now(),
        by="operator",
        report_kind=RUNNER_LOCALLY_PAUSED,
        report_payload=json.dumps({"runner_id": "r1", "by": "operator"}),
    )

    Advance(blocked_ctx).run()  # must not raise, and must not escalate

    # Deferred, not escalated: the in-flight record and the lease both stay exactly as they
    # were — nothing killed, nothing closed — for a later pass once the pause lifts.
    assert store.in_flight_elicitation("lease_1", 1) is not None
    assert store.active_lease("lease_1") is not None
    assert [e for e in store.open_escalations() if e.chunk_id == "ch_1"] == []
    assert [b for b in store.pending_outbound() if b.kind == EVENT_RECORDED and b.chunk_id == "ch_1"] == []


def test_judgement_collect_blocked_by_unresolvable_owner_abandons_a_detached_chunk(tmp_path):  # type: ignore[no-untyped-def]
    """`escalate_owner_unresolvable` takes the same detached precedence `fail` takes: a chunk
    the hub no longer routes here abandons instead of escalating."""
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
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope("ch_1")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="pass"
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(
        store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(alive=set()), clock=FixedClock(_NOW)
    )

    Advance(ctx).run()  # launch — the owner resolves fine here
    assert len(harness.judged) == 1
    launched = store.in_flight_elicitation("lease_1", 1)
    assert launched is not None and launched.pid is not None

    # The recorded owner stops resolving, AND the hub has reassigned the chunk elsewhere.
    hub.chunks["ch_1"] = ChunkStatusView(chunk_id="ch_1", status=ChunkStatus.RUNNING, route_runner_id="other-runner")
    blocked_ctx = replace(ctx, harnesses=HarnessRegistry({}))

    Advance(blocked_ctx).run()  # must not raise, and must not escalate

    # Abandoned, not escalated — no takeover command was ever this runner's to compose.
    assert store.in_flight_elicitation("lease_1", 1) is None
    assert store.active_lease("lease_1") is None
    assert [e for e in store.open_escalations() if e.chunk_id == "ch_1"] == []
    abandoned = [b for b in store.pending_outbound() if b.kind == EVENT_RECORDED and b.chunk_id == "ch_1"]
    assert len(abandoned) == 1
    assert json.loads(abandoned[0].payload)["kind"] == "attempt-abandoned"


@pytest.mark.parametrize("unavailable", [False, True], ids=["unknown-owner", "unavailable-owner"])
def test_escalate_blocked_by_unresolvable_owner_still_escalates_with_no_takeover_command(tmp_path, unavailable):  # type: ignore[no-untyped-def]
    """The escalation's closure is already durable by the time the takeover command would be
    composed, so an unresolvable owner cannot undo it — the chunk still escalates, with the
    same empty takeover command the no-session/no-bindings branch already uses."""
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_blocked",
            chunk_id="ch_blocked",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=0,  # exhausted on the first failure
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_blocked",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(_FOREIGN_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_blocked", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    hub = FakeHub()
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="pass"
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(), clock=FixedClock(_NOW))
    ctx = replace(ctx, harnesses=_registry(harness, unavailable=unavailable))
    lease = store.active_lease("lease_blocked")
    assert lease is not None

    Attempt(ctx, lease).fail(reason=REAPED, via="test")  # must not raise

    assert store.active_lease("lease_blocked") is None  # closed
    escalations = [e for e in store.open_escalations() if e.chunk_id == "ch_blocked"]
    assert len(escalations) == 1
    events = [b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED]
    assert len(events) == 1
    assert json.loads(events[0].payload)["chunk_id"] == "ch_blocked"
    assert json.loads(events[0].payload)["takeover_command"] == ""


@pytest.mark.parametrize("unavailable", [False, True], ids=["unknown-owner", "unavailable-owner"])
def test_fail_owner_block_short_circuits_a_retry_the_budget_would_otherwise_allow(tmp_path, unavailable):  # type: ignore[no-untyped-def]
    """A first failure with retry budget still open (``retried=0 < retries_max=2``) would
    ordinarily requeue, but an unresolvable recorded owner blocks that — the chunk escalates
    in place instead, on the same tick, consuming no retry."""
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_blocked",
            chunk_id="ch_blocked",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,  # budget still open — the owner block must pre-empt it
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_blocked",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(_FOREIGN_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_blocked", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    hub = FakeHub()
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"), verdict="pass"
    )
    provider = FakeProvider({"e1": "/ws/e1"})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(), clock=FixedClock(_NOW))
    ctx = replace(ctx, harnesses=_registry(harness, unavailable=unavailable))
    lease = store.active_lease("lease_blocked")
    assert lease is not None
    attempts_before = store.attempt_count("ch_blocked", "nd_build")

    Attempt(ctx, lease).fail(reason=REAPED, via="test")  # must not raise

    assert store.active_lease("lease_blocked") is None  # closed, not reopened by a requeue
    assert harness.spawns == []  # never requeued under the very same unresolvable owner
    assert store.attempt_count("ch_blocked", "nd_build") == attempts_before  # no retry consumed

    escalations = [e for e in store.open_escalations() if e.chunk_id == "ch_blocked"]
    assert len(escalations) == 1
    events = [b for b in store.pending_outbound() if b.kind == EVENT_RECORDED and b.chunk_id == "ch_blocked"]
    assert len(events) == 1
    payload = json.loads(events[0].payload)
    assert payload["kind"] == "owner-unresolvable"
    assert payload["detail"] == {
        "via": "test",
        "harness_id": _FOREIGN_HARNESS_ID,
        "owner_status": "unavailable" if unavailable else "unknown",
    }
    escalation_events = [
        b for b in store.pending_outbound() if b.kind == ESCALATION_RECORDED and b.chunk_id == "ch_blocked"
    ]
    assert len(escalation_events) == 1
    assert json.loads(escalation_events[0].payload)["takeover_command"] == ""


def _pooled_envelope(chunk_id: str, node_name: str, node_id: str):  # type: ignore[no-untyped-def]
    return make_envelope(
        chunk_id,
        node_name,
        node_id=node_id,
        choices=_CHOICES,
        session=SessionMode.RESUME,
        session_source="code",
        session_name="code",
        session_model=["blizzard:basic"],
    )


@pytest.mark.parametrize("unavailable", [False, True], ids=["unknown-owner", "unavailable-owner"])
def test_pool_head_owner_unresolvable_at_node_entry_escalates_but_a_sibling_chunk_still_advances(tmp_path, unavailable):  # type: ignore[no-untyped-def]
    """``Spawner.enter_node``: a named pool's head owner unresolvable at node entry escalates
    that chunk in place, never spawning or substituting a default harness — and never blocks
    a sibling the SAME ``Advance.run()`` sweep also drives into a fresh node."""
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_head",
            chunk_id="ch_pool",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            session_name="code",
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_head",
        pid=1,
        process_start_time="t",
        session=SessionReference(_FOREIGN_HARNESS_ID, "sess-head"),
        spawned_at=_NOW,
    )
    store.record_closure(
        lease_id="lease_head", chunk_id="ch_pool", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )
    store.record_binding(chunk_id="ch_pool", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_binding(chunk_id="ch_sibling", environment_id="e2", workdir="/ws/e2", bound_at=_NOW)

    hub = FakeHub()
    # Routed here still — only its recorded owner is unresolvable, so `Attempt.detached`'s
    # own check (now taken ahead of the escalation too) must not read this as reassigned.
    hub.chunks["ch_pool"] = ChunkStatusView(
        chunk_id="ch_pool", status=ChunkStatus.RUNNING, route_runner_id="r1", latest_epoch=2
    )
    hub.envelopes["ch_pool"] = _pooled_envelope("ch_pool", "verify", "nd_verify")
    hub.chunks["ch_sibling"] = ChunkStatusView(chunk_id="ch_sibling", status=ChunkStatus.RUNNING, latest_epoch=1)
    hub.envelopes["ch_sibling"] = make_envelope(
        "ch_sibling", "verify", node_id="nd_verify", choices=_CHOICES, session=SessionMode.FRESH
    )

    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-sibling", pid=200, process_start_time="start-200"), verdict="pass"
    )
    provider = FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(), clock=FixedClock(_NOW))
    ctx = replace(ctx, harnesses=_registry(harness, unavailable=unavailable))

    Advance(ctx).run()  # must not raise, and must not let ch_pool block ch_sibling

    # The pool chunk escalated in place — no spawn, no substituted default harness.
    assert store.active_lease_for_chunk("ch_pool") is None
    assert [e for e in store.open_escalations() if e.chunk_id == "ch_pool"]
    owner_events = [b for b in store.pending_outbound() if b.kind == EVENT_RECORDED and b.chunk_id == "ch_pool"]
    assert len(owner_events) == 1
    assert json.loads(owner_events[0].payload)["kind"] == "owner-unresolvable"

    # The sibling, driven by the SAME sweep, spawned normally under the resolvable default.
    assert harness.spawns != []
    assert harness.resume_froms == [None]
    lease = store.active_lease_for_chunk("ch_sibling")
    assert lease is not None and lease.session == SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-sibling")


@pytest.mark.parametrize("unavailable", [False, True], ids=["unknown-owner", "unavailable-owner"])
def test_a_plain_resumes_unresolvable_owner_escalates_node_entry_but_a_sibling_still_advances(tmp_path, unavailable):  # type: ignore[no-untyped-def]
    """A bare, un-pooled ``resume:`` whose latest session's own owner cannot be dispatched to
    escalates the chunk in place at node entry too, exactly as a named pool's breached head
    does, while a sibling the SAME sweep also drives still spawns."""
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_head",
            chunk_id="ch_plain",
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
        "lease_head",
        pid=1,
        process_start_time="t",
        session=SessionReference(_FOREIGN_HARNESS_ID, "sess-head"),
        spawned_at=_NOW,
    )
    store.record_closure(
        lease_id="lease_head", chunk_id="ch_plain", node_id="nd_build", reason="transitioned", closed_at=_NOW
    )
    store.record_binding(chunk_id="ch_plain", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_binding(chunk_id="ch_sibling", environment_id="e2", workdir="/ws/e2", bound_at=_NOW)

    hub = FakeHub()
    # Routed here still — only its recorded owner is unresolvable.
    hub.chunks["ch_plain"] = ChunkStatusView(
        chunk_id="ch_plain", status=ChunkStatus.RUNNING, route_runner_id="r1", latest_epoch=2
    )
    hub.envelopes["ch_plain"] = make_envelope(
        "ch_plain",
        "verify",
        node_id="nd_verify",
        choices=_CHOICES,
        session=SessionMode.RESUME,
        session_source="build",  # a targeted, un-pooled resume — no `session_name`
    )
    hub.chunks["ch_sibling"] = ChunkStatusView(chunk_id="ch_sibling", status=ChunkStatus.RUNNING, latest_epoch=1)
    hub.envelopes["ch_sibling"] = make_envelope(
        "ch_sibling", "verify", node_id="nd_verify", choices=_CHOICES, session=SessionMode.FRESH
    )

    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-sibling", pid=200, process_start_time="start-200"), verdict="pass"
    )
    provider = FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(), clock=FixedClock(_NOW))
    ctx = replace(ctx, harnesses=_registry(harness, unavailable=unavailable))

    Advance(ctx).run()  # must not raise, and must not let ch_plain block ch_sibling

    # The plain-resume chunk escalated in place — no spawn, no substituted default harness.
    assert store.active_lease_for_chunk("ch_plain") is None
    escalations = [e for e in store.open_escalations() if e.chunk_id == "ch_plain"]
    assert len(escalations) == 1
    owner_events = [b for b in store.pending_outbound() if b.kind == EVENT_RECORDED and b.chunk_id == "ch_plain"]
    assert len(owner_events) == 1
    payload = json.loads(owner_events[0].payload)
    assert payload["kind"] == "owner-unresolvable"
    assert payload["detail"]["harness_id"] == "foreign"

    # The sibling, driven by the SAME sweep, spawned normally under the resolvable default.
    assert harness.spawns != []
    assert harness.resume_froms == [None]
    lease = store.active_lease_for_chunk("ch_sibling")
    assert lease is not None and lease.session == SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-sibling")
