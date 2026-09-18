"""``DormantSession``'s wake paths never raise on an unresolvable session owner — they
escalate the chunk in place instead. ``_restart`` already guarded its own wake;
``on_answer``, ``on_unpause``, and ``resume_on_unmet_produces`` now share that same guard
instead of raising out of ``Advance.run()``'s per-lease loop. ``park_on_ask`` shares no
wake — parking needs no harness — but skips its own harness-dependent usage record the
same way, tested here on its own."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import LeaseRecord, NewLease
from blizzard.runner.environments.repository import EnvBindingRecord
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.loop.steps import Advance
from blizzard.runner.loop.usage import UsageRecorder
from blizzard.wire.chunk import ChunkStatusView
from blizzard.wire.facts import ESCALATION_RECORDED, QUESTION_ASKED
from blizzard.wire.question import QuestionView
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, make_context, make_envelope, make_store

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_FOREIGN_HARNESS_ID = "foreign"
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


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
    """A chunk the hub reports DONE — the held-chunk poll's own release trigger, seeded as
    the sibling every test below asserts ``Advance.run()`` still reached."""
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
def test_on_answer_blocked_by_unresolvable_owner_escalates_in_place(tmp_path, unavailable):  # type: ignore[no-untyped-def]
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
    store.record_ask(
        lease_id="lease_blocked",
        chunk_id="ch_blocked",
        question_id="qn_1",
        question="Which API?",
        options=[],
        session=SessionReference(_FOREIGN_HARNESS_ID, "sess-a"),
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_blocked", chunk_id="ch_blocked", question_id="qn_1", parked_at=_NOW)
    store.record_binding(chunk_id="ch_done", environment_id="e_done", workdir="/ws/e_done", bound_at=_NOW)

    hub = FakeHub()
    hub.questions["qn_1"] = _answered_question()
    hub.chunks["ch_done"] = _done_chunk("ch_done")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="pass"
    )
    provider = FakeProvider({"e1": "/ws/e1", "e_done": "/ws/e_done"})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(), clock=FixedClock(_NOW))
    ctx = replace(ctx, harnesses=_registry(harness, unavailable=unavailable))

    Advance(ctx).run()  # must not raise

    # The blocked lease escalates in place — closed, never resumed. Its ask-park fact is left
    # standing, harmless since the closed lease no longer appears in the active-lease sweep.
    assert store.active_lease("lease_blocked") is None
    assert harness.resumed == []
    _assert_escalated_once_with_no_takeover(store, "ch_blocked")

    # The sibling held chunk was still reached and released this same Advance() sweep.
    assert provider.released == ["e_done"]
    assert store.held_environment_ids() == ["e1"]

    # Replay after a crash — a fresh Advance() over the same store — never double-escalates:
    # the lease is already closed, so no active-lease sweep can reach this session again.
    Advance(ctx).run()
    _assert_escalated_once_with_no_takeover(store, "ch_blocked")


@pytest.mark.parametrize("unavailable", [False, True], ids=["unknown-owner", "unavailable-owner"])
def test_on_unpause_blocked_by_unresolvable_owner_escalates_in_place(tmp_path, unavailable):  # type: ignore[no-untyped-def]
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
    store.record_pause_park(lease_id="lease_blocked", chunk_id="ch_blocked", parked_at=_NOW)
    store.record_binding(chunk_id="ch_done", environment_id="e_done", workdir="/ws/e_done", bound_at=_NOW)

    hub = FakeHub()
    # The pause has lifted and the chunk is still routed to this runner — the on_unpause
    # wake's own precondition.
    hub.chunks["ch_blocked"] = ChunkStatusView(
        chunk_id="ch_blocked", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1"
    )
    hub.chunks["ch_done"] = _done_chunk("ch_done")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="pass"
    )
    provider = FakeProvider({"e1": "/ws/e1", "e_done": "/ws/e_done"})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe(), clock=FixedClock(_NOW))
    ctx = replace(ctx, harnesses=_registry(harness, unavailable=unavailable))

    Advance(ctx).run()  # must not raise

    # The blocked lease escalates in place instead of resuming under an owner it cannot serve.
    # Its pause-park fact is left standing, harmless since the closed lease no longer appears.
    assert store.active_lease("lease_blocked") is None
    assert harness.resumed == []
    _assert_escalated_once_with_no_takeover(store, "ch_blocked")

    assert provider.released == ["e_done"]
    assert store.held_environment_ids() == ["e1"]

    # Replay after a crash never double-escalates — the lease is already closed.
    Advance(ctx).run()
    _assert_escalated_once_with_no_takeover(store, "ch_blocked")


@pytest.mark.parametrize("unavailable", [False, True], ids=["unknown-owner", "unavailable-owner"])
def test_resume_on_unmet_produces_blocked_by_unresolvable_owner_escalates_in_place(  # type: ignore[no-untyped-def]
    tmp_path, unavailable
):
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
    # A clean exit with a required `produces:` name never attached — the unmet-produces
    # resume's own trigger.
    hub.envelopes["ch_blocked"] = make_envelope(
        "ch_blocked", "build", node_id="nd_build", choices=_CHOICES, produces=["artifact-x"]
    )
    hub.chunks["ch_done"] = _done_chunk("ch_done")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100),
        verdict=None,
        assessment="",
    )
    probe = FakeProbe(alive=set())  # the worker already exited
    provider = FakeProvider({"e1": "/ws/e1", "e_done": "/ws/e_done"})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=probe, clock=FixedClock(_NOW))
    ctx = replace(ctx, harnesses=_registry(harness, unavailable=unavailable))

    Advance(ctx).run()  # must not raise

    # No resume, no spend recorded, no verdict elicited — the chunk escalates in place instead.
    assert store.active_lease("lease_blocked") is None
    assert harness.resumed == [] and harness.judged == []
    _assert_escalated_once_with_no_takeover(store, "ch_blocked")

    assert provider.released == ["e_done"]
    assert store.held_environment_ids() == ["e1"]

    # Replay after a crash never double-escalates — the lease is already closed.
    Advance(ctx).run()
    _assert_escalated_once_with_no_takeover(store, "ch_blocked")


@dataclass
class _RecordWorkerSpy:
    """Proxies :class:`UsageRecorder`, logging every :meth:`record_worker` call rather than
    performing it — the one harness-dependent write ``park_on_ask`` must skip, apart from
    forwarding the ask and parking the chunk, when the owner cannot be resolved."""

    inner: UsageRecorder
    calls: list[str] = field(default_factory=list)

    def record_worker(self, lease: LeaseRecord, bindings: list[EnvBindingRecord]) -> None:
        self.calls.append(lease.lease_id)
        self.inner.record_worker(lease, bindings)

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)


@pytest.mark.parametrize("unavailable", [False, True], ids=["unknown-owner", "unavailable-owner"])
def test_park_on_ask_skips_only_the_usage_record_when_owner_unresolvable(tmp_path, unavailable):  # type: ignore[no-untyped-def]
    """Parking needs no harness at all; only the generation's usage spend does, so an
    unresolvable owner skips *that* record alone — the ask still reaches the hub and the
    chunk still parks, exactly as it would otherwise."""
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
    store.record_ask(
        lease_id="lease_blocked",
        chunk_id="ch_blocked",
        question_id="qn_1",
        question="Which API?",
        options=[],
        session=SessionReference(_FOREIGN_HARNESS_ID, "sess-a"),
        asked_at=_NOW,
    )
    store.record_binding(chunk_id="ch_done", environment_id="e_done", workdir="/ws/e_done", bound_at=_NOW)

    hub = FakeHub()
    hub.chunks["ch_done"] = _done_chunk("ch_done")
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100), verdict="pass"
    )
    probe = FakeProbe(alive=set())  # the worker already exited — ask-and-exit
    provider = FakeProvider({"e1": "/ws/e1", "e_done": "/ws/e_done"})
    ctx = make_context(store, hub=hub, provider=provider, harness=harness, probe=probe, clock=FixedClock(_NOW))
    ctx = replace(ctx, harnesses=_registry(harness, unavailable=unavailable))
    spy = _RecordWorkerSpy(ctx.usage)
    ctx = replace(ctx, usage=spy)

    Advance(ctx).run()  # must not raise

    # The harness-dependent usage record alone is skipped — never even attempted.
    assert spy.calls == []

    # Parking itself needed no harness: the ask still reached the hub and the chunk still
    # parked, exactly as `_resolve_harness`'s success would have left it.
    assert store.ask_parked_lease_ids() == {"lease_blocked"}
    assert any(fact.kind == QUESTION_ASKED for fact in store.pending_outbound())
    blocked = store.active_lease("lease_blocked")
    assert blocked is not None and blocked.pid == 100

    # The sibling held chunk was still reached and released this same Advance() sweep.
    assert provider.released == ["e_done"]
    assert store.held_environment_ids() == ["e1"]


def _answered_question():  # type: ignore[no-untyped-def]
    return QuestionView(
        question_id="qn_1",
        chunk_id="ch_blocked",
        runner_id="r1",
        epoch=1,
        question="Which API?",
        asked_at="t",
        answered=True,
        answer="rest",
        answered_by="alice",
        answered_at="t2",
    )
