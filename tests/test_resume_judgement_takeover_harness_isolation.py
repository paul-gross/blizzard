"""Two harnesses sharing one raw session-id text never collide in RESUME, JUDGEMENT, or
TAKEOVER — each dispatches its resumed/judged/taken-over session through its own recorded
owner, keyed by the FULL ``SessionReference`` (harness + raw id), never the raw id alone.

Same shape as ``tests/test_usage_recorder_harness_isolation.py`` and
``tests/test_transcript_harness_isolation.py``: two leases seeded under the SAME raw session
id but different ``harness_id``, each bound to its OWN fake adapter in one
``HarnessRegistry`` — every assertion below reads a specific fake's own call log, never the
sibling's, so dropping ``harness_id`` from any of the three lookups this file targets
(``DormantSession._resolve_harness`` in ``dormant.py``, ``Judgement._resolve_harness`` in
``judgement.py``, ``TakeoverService._resolved_harness`` in ``takeover.py``) turns one of
these red."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.domain.takeover import TakeoverOpenScope, TakeoverService
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry, UnknownHarnessError
from blizzard.runner.loop.judgement_prompt import JudgementPrompt
from blizzard.runner.loop.steps import Advance, Resume, ResumeIntents
from blizzard.wire.chunk import ChunkStatusView
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

_NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
_SHARED_SESSION_ID = "shared-dispatch-session"
_OTHER_HARNESS_ID = "other_harness"


def _seed_lease(  # type: ignore[no-untyped-def]
    store,
    *,
    lease_id: str,
    chunk_id: str,
    harness_id: str,
    pid: int,
    node_id: str = "nd_build",
    node_name: str = "build",
) -> None:
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id=node_id,
            node_name=node_name,
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease_id,
        pid=pid,
        process_start_time=f"start-{pid}",
        session=SessionReference(harness_id, _SHARED_SESSION_ID),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk_id, environment_id=f"e-{lease_id}", workdir=f"/ws/{lease_id}", bound_at=_NOW)


def _two_harness_registry(harness_a: FakeHarness, harness_b: FakeHarness) -> HarnessRegistry:
    return HarnessRegistry(
        {
            CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness_a, transcript_source=harness_a.transcript_source()),
            _OTHER_HARNESS_ID: HarnessBinding(adapter=harness_b, transcript_source=harness_b.transcript_source()),
        }
    )


def _swap_registry(ctx, registry: HarnessRegistry):  # type: ignore[no-untyped-def]
    """``make_context`` bakes its single-harness registry into three other places besides
    ``ctx.harnesses`` itself — ``ctx.usage`` (:class:`UsageRecorder`), ``ctx.sessions``
    (:class:`SessionResolver`), and ``ctx.harness_selector`` (:class:`HarnessSelector`) each
    hold their OWN copy of the registry reference, not a read-through of ``ctx.harnesses`` —
    so a swap that only replaces the top-level field leaves those three still dispatching
    through the original one-harness registry. All four are replaced together here."""
    return replace(
        ctx,
        harnesses=registry,
        usage=replace(ctx.usage, harnesses=registry),
        sessions=replace(ctx.sessions, harnesses=registry),
        harness_selector=replace(ctx.harness_selector, harnesses=registry),
    )


# --------------------------------------------------------------------------- #
# RESUME (dormant.py) — the restart-resume re-attach.


def test_restart_resume_dispatches_each_lease_to_its_own_harness_never_the_siblings(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Two leases, sharing one raw session id, marked for graceful restart-resume — each
    must be re-attached through its OWN recorded owner's ``resume_with_message``, never the
    sibling's, despite the fake registry storing the pids identically."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    _seed_lease(store, lease_id="lease_b", chunk_id="ch_b", harness_id=_OTHER_HARNESS_ID, pid=200)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_a"] = ChunkStatusView(
        chunk_id="ch_a", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1"
    )
    hub.chunks["ch_b"] = ChunkStatusView(
        chunk_id="ch_b", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1"
    )

    handle_a = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    handle_b = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=200, process_start_time="start-200", pgid=200)
    harness_a = FakeHarness(handle=handle_a, verdict=None)
    harness_a.resume_pid = 101
    harness_b = FakeHarness(handle=handle_b, verdict=None)
    harness_b.resume_pid = 201
    probe = FakeProbe(alive={(100, "start-100"), (101, "resume-start"), (200, "start-200"), (201, "resume-start")})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e-lease_a": "/ws/lease_a", "e-lease_b": "/ws/lease_b"}),
        harness=harness_a,
        probe=probe,
    )
    ctx = _swap_registry(ctx, _two_harness_registry(harness_a, harness_b))

    Resume(ctx).run()

    # Each fake's OWN resume log carries exactly its own lease's resume — never the sibling's.
    assert harness_a.resumed == [
        ("/ws/lease_a", _SHARED_SESSION_ID, "# The supervisor restarted; continue your task where you left off.")
    ]
    assert harness_b.resumed == [
        ("/ws/lease_b", _SHARED_SESSION_ID, "# The supervisor restarted; continue your task where you left off.")
    ]
    lease_a = store.active_lease("lease_a")
    lease_b = store.active_lease("lease_b")
    assert lease_a is not None and lease_a.pid == 101
    assert lease_b is not None and lease_b.pid == 201


def test_restart_resume_owner_failure_escalates_only_the_affected_lease(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An unresolvable owner on ONE lease (a harness id the registry never binds) escalates
    only that lease — the sibling, sharing the same raw session id under a KNOWN harness,
    still resumes cleanly through its own adapter."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    _seed_lease(store, lease_id="lease_b", chunk_id="ch_b", harness_id="unbound-harness", pid=200)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_a"] = ChunkStatusView(
        chunk_id="ch_a", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1"
    )
    hub.chunks["ch_b"] = ChunkStatusView(
        chunk_id="ch_b", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1"
    )

    handle_a = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    harness_a = FakeHarness(handle=handle_a, verdict=None)
    harness_a.resume_pid = 101
    probe = FakeProbe(alive={(100, "start-100"), (101, "resume-start"), (200, "start-200")})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e-lease_a": "/ws/lease_a", "e-lease_b": "/ws/lease_b"}),
        harness=harness_a,
        probe=probe,
    )
    # `lease_b`'s own harness id (`unbound-harness`) is never a member of this registry —
    # only `claude_code` is bound, so `lease_b`'s resolution must fail, never fall back to it.
    ctx = _swap_registry(ctx, HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness_a)}))

    Resume(ctx).run()

    # lease_a resumed cleanly through its own (the only bound) adapter.
    assert harness_a.resumed == [
        ("/ws/lease_a", _SHARED_SESSION_ID, "# The supervisor restarted; continue your task where you left off.")
    ]
    lease_a = store.active_lease("lease_a")
    assert lease_a is not None and lease_a.pid == 101
    # lease_b escalated instead of silently resuming under the wrong (or a made-up) owner.
    assert store.active_lease("lease_b") is None
    escalations = [e for e in store.open_escalations() if e.chunk_id == "ch_b"]
    assert len(escalations) == 1


# --------------------------------------------------------------------------- #
# JUDGEMENT (judgement.py:484 `_resolve_harness`) — launch (`_elicit`) then collect.


def _judgement_ctx(store, *, harness_a: FakeHarness, harness_b: FakeHarness, probe: FakeProbe):  # type: ignore[no-untyped-def]
    choices = [("pass", "meets criteria"), ("fail", "does not")]
    envelope_a = make_envelope("ch_a", "build", node_id="nd_build", choices=choices)
    envelope_b = make_envelope("ch_b", "build", node_id="nd_build", choices=choices)
    hub = FakeHub()
    hub.envelopes["ch_a"] = envelope_a
    hub.envelopes["ch_b"] = envelope_b
    hub.claim_outcome = claimed_outcome("ch_a", envelope_a)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e-lease_a": "/ws/lease_a", "e-lease_b": "/ws/lease_b"}),
        harness=harness_a,
        probe=probe,
    )
    return _swap_registry(ctx, _two_harness_registry(harness_a, harness_b)), envelope_a, envelope_b


def test_judgement_launch_and_collect_dispatch_each_lease_to_its_own_harness(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Two exited workers, sharing one raw session id under different harnesses: the launch
    pass elicits a verdict from each lease's OWN adapter, and the collect pass reads each
    verdict back from that SAME adapter — never the sibling's, even though both fakes are
    scripted with distinct, checkably different verdicts."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    _seed_lease(store, lease_id="lease_b", chunk_id="ch_b", harness_id=_OTHER_HARNESS_ID, pid=200)

    handle_a = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    handle_b = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=200, process_start_time="start-200", pgid=200)
    # Distinct verdicts per owner — a cross-wired collect would read the WRONG one back.
    harness_a = FakeHarness(handle=handle_a, verdict="pass", judge_pid=8801)
    harness_b = FakeHarness(handle=handle_b, verdict="fail", judge_pid=8802)
    probe = FakeProbe()  # neither worker pid (100/200) is alive — both read as exited
    ctx, envelope_a, envelope_b = _judgement_ctx(store, harness_a=harness_a, harness_b=harness_b, probe=probe)

    Advance(ctx).run()  # launch pass — elicits both verdicts

    # Each fake's OWN judge log carries exactly its own lease's session — never the
    # sibling's — and the prompt text, rendered independently from each lease's OWN
    # envelope (no checks declared, so an empty check-results list), not read back off
    # the fake's own recorded call.
    expected_prompt_a = JudgementPrompt(envelope_a, []).render()
    expected_prompt_b = JudgementPrompt(envelope_b, []).render()
    assert harness_a.judged == [("/ws/lease_a", _SHARED_SESSION_ID, expected_prompt_a)]
    assert harness_b.judged == [("/ws/lease_b", _SHARED_SESSION_ID, expected_prompt_b)]
    assert store.in_flight_elicitation("lease_a", 1) is not None
    assert store.in_flight_elicitation("lease_b", 1) is not None

    Advance(ctx).run()  # collect pass — the fake elicitation pids read dead by default

    assert store.in_flight_elicitation("lease_a", 1) is None
    assert store.in_flight_elicitation("lease_b", 1) is None
    outbound = {b.chunk_id: b for b in store.pending_outbound() if b.kind == "completion.submitted"}
    assert "ch_a" in outbound and "ch_b" in outbound
    choice_a = json.loads(outbound["ch_a"].payload)["submission"]["choice"]
    choice_b = json.loads(outbound["ch_b"].payload)["submission"]["choice"]
    # lease_a's own "pass" verdict landed lease_a's completion; lease_b's own "fail" landed
    # lease_b's — never the other way around (a cross-wired collect reading the sibling's
    # verdict back would flip these), which a raw-id-only lookup would risk.
    assert choice_a == "pass"
    assert choice_b == "fail"


def test_judgement_owner_failure_on_one_lease_never_blocks_the_others_collect(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An unresolvable owner (a harness id this registry never binds) on ONE lease escalates
    only that lease; the sibling, sharing the same raw session id under a KNOWN harness,
    still judges cleanly through its own adapter, on the same tick."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    _seed_lease(store, lease_id="lease_b", chunk_id="ch_b", harness_id="unbound-harness", pid=200)

    choices = [("pass", "meets criteria"), ("fail", "does not")]
    envelope_a = make_envelope("ch_a", "build", node_id="nd_build", choices=choices)
    envelope_b = make_envelope("ch_b", "build", node_id="nd_build", choices=choices)
    hub = FakeHub()
    hub.envelopes["ch_a"] = envelope_a
    hub.envelopes["ch_b"] = envelope_b
    hub.claim_outcome = claimed_outcome("ch_a", envelope_a)

    handle_a = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    harness_a = FakeHarness(handle=handle_a, verdict="pass")
    probe = FakeProbe()
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e-lease_a": "/ws/lease_a", "e-lease_b": "/ws/lease_b"}),
        harness=harness_a,
        probe=probe,
    )
    # Only `claude_code` is bound — `lease_b`'s own `unbound-harness` id resolves nowhere.
    ctx = _swap_registry(ctx, HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness_a)}))

    Advance(ctx).run()

    # lease_a's own adapter was reached and elicited a verdict, with the prompt text
    # rendered independently from lease_a's OWN envelope (no checks declared), not read
    # back off the fake's own recorded call.
    expected_prompt_a = JudgementPrompt(envelope_a, []).render()
    assert harness_a.judged == [("/ws/lease_a", _SHARED_SESSION_ID, expected_prompt_a)]
    # lease_b never got a launch call on ANY adapter — no in-flight elicitation for it.
    assert store.in_flight_elicitation("lease_b", 1) is None
    escalations = [e for e in store.open_escalations() if e.chunk_id == "ch_b"]
    assert len(escalations) == 1


# --------------------------------------------------------------------------- #
# TAKEOVER (takeover.py:370 `_resolved_harness`).


def _takeover_service(stores, *, harness_a: FakeHarness, harness_b: FakeHarness) -> TakeoverService:  # type: ignore[no-untyped-def]
    return TakeoverService(
        stores,
        FixedClock(_NOW),
        FakeProbe(),
        local_api_url="http://127.0.0.1:8431",
        harnesses=_two_harness_registry(harness_a, harness_b),
    )


def _open_scope(store, chunk_id: str) -> TakeoverOpenScope:  # type: ignore[no-untyped-def]
    return TakeoverOpenScope(
        chunk_id=chunk_id,
        open_takeover=store.open_takeover_for_chunk(chunk_id),
        bindings=store.bindings_for_chunk(chunk_id),
        active_lease=store.active_lease_for_chunk(chunk_id),
        latest_lease_with_session=store.latest_lease_with_session_for_chunk(chunk_id),
        latest_epoch=store.latest_epoch(chunk_id),
    )


def test_takeover_opens_each_parked_chunk_through_its_own_harness(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Two ask-parked chunks, sharing one raw session id under different harnesses: opening a
    takeover on each must compose its resume command through its OWN recorded owner's
    ``resume_command`` — never the sibling's, proven by each fake's own call-log staying
    empty for the OTHER lease's takeover."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    _seed_lease(store, lease_id="lease_b", chunk_id="ch_b", harness_id=_OTHER_HARNESS_ID, pid=200)
    store.record_park(lease_id="lease_a", chunk_id="ch_a", question_id="qn_a", parked_at=_NOW)
    store.record_park(lease_id="lease_b", chunk_id="ch_b", question_id="qn_b", parked_at=_NOW)

    handle_a = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    handle_b = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=200, process_start_time="start-200", pgid=200)
    harness_a = FakeHarness(handle=handle_a, verdict=None)
    harness_b = FakeHarness(handle=handle_b, verdict=None)
    service = _takeover_service(make_stores(store), harness_a=harness_a, harness_b=harness_b)

    opened_a = service.open(_open_scope(store, "ch_a"), force=False)
    opened_b = service.open(_open_scope(store, "ch_b"), force=False)

    assert opened_a.command == "cd /ws/lease_a && claude --resume shared-dispatch-session"
    assert opened_b.command == "cd /ws/lease_b && claude --resume shared-dispatch-session"
    # Each fake's OWN resume-command log carries exactly one call — its own — never the
    # sibling's takeover; a harness-id-dropped lookup would double one and starve the other.
    assert harness_a.resume_command_config == [(None, None)]
    assert harness_b.resume_command_config == [(None, None)]
    record_a = store.open_takeover_for_chunk("ch_a")
    record_b = store.open_takeover_for_chunk("ch_b")
    assert record_a is not None and record_a.harness_id == CLAUDE_CODE_HARNESS_ID
    assert record_b is not None and record_b.harness_id == _OTHER_HARNESS_ID


def test_takeover_owner_failure_on_one_chunk_never_blocks_the_others_open(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """An unresolvable owner on ONE parked chunk (its harness id unbound in this registry)
    raises out of `open` for that chunk alone; the sibling, sharing the same raw session id
    under a KNOWN harness, still opens cleanly through its own adapter."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    _seed_lease(store, lease_id="lease_b", chunk_id="ch_b", harness_id="unbound-harness", pid=200)
    store.record_park(lease_id="lease_a", chunk_id="ch_a", question_id="qn_a", parked_at=_NOW)
    store.record_park(lease_id="lease_b", chunk_id="ch_b", question_id="qn_b", parked_at=_NOW)

    handle_a = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    harness_a = FakeHarness(handle=handle_a, verdict=None)
    service = TakeoverService(
        make_stores(store),
        FixedClock(_NOW),
        FakeProbe(),
        local_api_url="http://127.0.0.1:8431",
        # Only `claude_code` is bound — `ch_b`'s own `unbound-harness` id resolves nowhere.
        harnesses=HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness_a)}),
    )

    opened_a = service.open(_open_scope(store, "ch_a"), force=False)

    assert opened_a.command == "cd /ws/lease_a && claude --resume shared-dispatch-session"
    assert harness_a.resume_command_config == [(None, None)]

    with pytest.raises(UnknownHarnessError):
        service.open(_open_scope(store, "ch_b"), force=False)

    # No takeover was ever recorded for the failed chunk — the fact-before-command ordering
    # (module docstring) never wrote a partial record for an owner it couldn't resolve past.
    assert store.open_takeover_for_chunk("ch_b") is None
