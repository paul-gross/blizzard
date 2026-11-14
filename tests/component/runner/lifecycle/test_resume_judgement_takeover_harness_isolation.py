"""Two harnesses sharing one raw session-id text never collide in RESUME, JUDGEMENT, or
TAKEOVER — each dispatches through its own recorded owner, keyed by the FULL
``SessionReference`` (harness + raw id), never the raw id alone. Same shape as
``tests/component/runner/usage/test_recorder_harness_isolation.py``: two leases seeded under the same raw
session id but different ``harness_id``, each bound to its own fake adapter, so dropping
``harness_id`` from any of the three resolvers this file targets turns one assertion red."""

from __future__ import annotations

import json
import weakref
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.runner.app import _harnesses_for_lease  # pyright: ignore[reportPrivateUsage]
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.harness_set import (
    HarnessAssemblyKey,
    HarnessSet,
    HarnessSets,
    JudgeInvocation,
    WorkerInvocation,
)
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.session_settings import SessionEnvironment
from blizzard.runner.harness.transcript import TranscriptPosition
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.dormant import DormantSession
from blizzard.runner.lifecycle.judgement.judgement_prompt import JudgementPrompt
from blizzard.runner.lifecycle.resume_intents import ResumeIntents
from blizzard.runner.lifecycle.takeover import TakeoverOpenScope, TakeoverOwnerUnresolvable, TakeoverService
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.steps import Advance, Resume
from blizzard.runner.loop_wiring import ResumeMarking, UsageRegistryLookup
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.transcripts.internal.harness_transcript_repositories import HarnessTranscriptRepositories
from blizzard.runner.transcripts.service import TranscriptService
from blizzard.runner.transcripts.transcript_pump import TranscriptPump
from tests.support.runner_fakes import (
    FakeArchivedTranscriptRepository,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    SqlAlchemyRunnerStore,
    claimed_outcome,
    make_context,
    make_envelope,
    make_host_stores,
    make_store,
    make_stores,
    single_workspace,
)
from tests.support.runner_mint import new_mint

_NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=UTC)
_SHARED_SESSION_ID = "shared-dispatch-session"
_OTHER_HARNESS_ID = "other_harness"


def _seed_lease(
    store: SqlAlchemyRunnerStore,
    *,
    lease_id: str,
    chunk_id: str,
    harness_id: str,
    pid: int,
    node_id: str = "nd_build",
    node_name: str = "build",
) -> None:
    store.record_mint(
        new_mint(
            NewLease(
                lease_id=lease_id,
                chunk_id=chunk_id,
                graph_id="gr_1",
                node_id=node_id,
                node_name=node_name,
                epoch=1,
                retries_max=2,
                created_at=_NOW,
            )
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


def _swap_registry(ctx: LoopContext, registry: HarnessRegistry) -> LoopContext:
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
# RESUME (src/blizzard/runner/lifecycle/dormant.py) — the restart-resume re-attach.


def test_restart_resume_dispatches_each_lease_to_its_own_harness_never_the_siblings(tmp_path: Path) -> None:
    """Two leases, sharing one raw session id, marked for graceful restart-resume — each
    must be re-attached through its OWN recorded owner's ``resume_with_message``, never the
    sibling's, despite the fake registry storing the pids identically."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    _seed_lease(store, lease_id="lease_b", chunk_id="ch_b", harness_id=_OTHER_HARNESS_ID, pid=200)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_a"] = ChunkState(chunk_id="ch_a", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1")
    hub.chunks["ch_b"] = ChunkState(chunk_id="ch_b", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1")

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


def test_restart_resume_owner_failure_escalates_only_the_affected_lease(tmp_path: Path) -> None:
    """An unresolvable owner on ONE lease (a harness id the registry never binds) escalates
    only that lease — the sibling, sharing the same raw session id under a KNOWN harness,
    still resumes cleanly through its own adapter."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    _seed_lease(store, lease_id="lease_b", chunk_id="ch_b", harness_id="unbound-harness", pid=200)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)

    hub = FakeHub()
    hub.chunks["ch_a"] = ChunkState(chunk_id="ch_a", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1")
    hub.chunks["ch_b"] = ChunkState(chunk_id="ch_b", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1")

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
# JUDGEMENT (src/blizzard/runner/lifecycle/judgement/judgement.py `_resolve_harness`) — launch (`_elicit`) then collect.


def _judgement_ctx(store: SqlAlchemyRunnerStore, *, harness_a: FakeHarness, harness_b: FakeHarness, probe: FakeProbe):
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


def test_judgement_launch_and_collect_dispatch_each_lease_to_its_own_harness(tmp_path: Path) -> None:
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

    # Each fake's OWN judge log carries its own lease's session and prompt, rendered
    # independently from its own envelope, never read back off the fake's own call.
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
    # lease_a's own "pass" landed lease_a's completion, lease_b's "fail" landed lease_b's
    # — never flipped, which a raw-id-only lookup (no cross-wired collect) would risk.
    assert choice_a == "pass"
    assert choice_b == "fail"


def test_judge_collects_from_retained_set_after_replacement(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    handle = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    old_source = FakeTranscriptSource(tail_positions_by_session={_SHARED_SESSION_ID: TranscriptPosition("old-tail")})
    new_source = FakeTranscriptSource(tail_positions_by_session={_SHARED_SESSION_ID: TranscriptPosition("new-tail")})
    old_harness = FakeHarness(handle=handle, verdict="pass", transcript_source=old_source)
    new_harness = FakeHarness(handle=handle, verdict="fail", transcript_source=new_source)
    ctx, _, _ = _judgement_ctx(store, harness_a=old_harness, harness_b=new_harness, probe=FakeProbe())
    old = Mock(spec=HarnessSet, registry=ctx.harnesses, versions=None)
    new_registry = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=new_harness, transcript_source=new_source)}
    )
    new = Mock(spec=HarnessSet, registry=new_registry, versions=None)
    sets = HarnessSets(Mock(side_effect=[old, new]))
    sets.select(HarnessAssemblyKey((1,)))
    ctx = replace(
        ctx,
        harness_sets=sets,
        usage=replace(ctx.usage, invocation_registry=UsageRegistryLookup(sets, ctx.usage.harnesses)),
    )

    Advance(ctx).run()
    key = JudgeInvocation("lease_a", 1)
    assert sets.lookup(key) is old
    sets.select(HarnessAssemblyKey((2,)))
    Advance(ctx).run()

    completion = next(b for b in store.pending_outbound() if b.kind == "completion.submitted")
    assert json.loads(completion.payload)["submission"]["choice"] == "pass"
    assert sets.find(key) is None


def test_lost_judge_relaunch_retains_earlier_owner_until_collection(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    handle = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    first = FakeHarness(handle=handle, verdict="pass", judge_output_usable=False)
    second = FakeHarness(handle=handle, verdict="pass")
    ctx, _, _ = _judgement_ctx(store, harness_a=first, harness_b=second, probe=FakeProbe())
    old = Mock(spec=HarnessSet, registry=ctx.harnesses, versions=None)
    old_ref = weakref.ref(old)
    new = Mock(
        spec=HarnessSet,
        registry=HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=second)}),
        versions=None,
    )
    remaining = [new, old]
    sets = HarnessSets(lambda _: remaining.pop())
    sets.select(HarnessAssemblyKey((1,)))
    ctx = replace(
        ctx,
        harness_sets=sets,
        usage=replace(ctx.usage, invocation_registry=UsageRegistryLookup(sets, ctx.usage.harnesses)),
    )

    Advance(ctx).run()
    sets.select(HarnessAssemblyKey((2,)))
    del old
    Advance(ctx).run()  # lost old elicitation, launch the new one
    assert old_ref() is not None
    assert sets.lookup(JudgeInvocation("lease_a", 1)) is old_ref()
    first.judge_output_usable = True
    Advance(ctx).run()  # collect the relaunched elicitation
    assert old_ref() is not None  # the worker's still-open transcript retains it
    sets.release_lease("lease_a")
    assert old_ref() is None


def test_judge_uses_worker_source_after_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    handle = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    old_source = FakeTranscriptSource(
        lines_by_session={_SHARED_SESSION_ID: ["old"]},
        tail_positions_by_session={_SHARED_SESSION_ID: TranscriptPosition("old-start")},
    )
    new_source = FakeTranscriptSource(
        lines_by_session={_SHARED_SESSION_ID: ["new"]},
        tail_positions_by_session={_SHARED_SESSION_ID: TranscriptPosition("new-start")},
    )
    first = FakeHarness(handle=handle, verdict="pass", transcript_source=old_source)
    second = FakeHarness(handle=handle, verdict="pass", transcript_source=new_source)
    origin_settings = replace(
        first.launch_settings(),
        binary="old-bin",
        config_dir="old-home",
        bundle_path="old-bundle",
        transcript_root="old-root",
        environment=SessionEnvironment((("PATH", "old-path"),), "/old-shell"),
    )
    current_settings = replace(
        second.launch_settings(),
        binary="new-bin",
        config_dir="new-home",
        bundle_path="new-bundle",
        transcript_root="new-root",
        environment=SessionEnvironment((("PATH", "new-path"),), "/new-shell"),
        model_aliases=(("tier", "current-model"),),
    )
    monkeypatch.setattr(first, "launch_settings", Mock(return_value=origin_settings))
    monkeypatch.setattr(second, "launch_settings", Mock(return_value=current_settings))
    judge_call = Mock(wraps=first.judge)
    monkeypatch.setattr(first, "judge", judge_call)
    ctx, _, _ = _judgement_ctx(store, harness_a=first, harness_b=second, probe=FakeProbe())
    old = Mock(spec=HarnessSet, registry=ctx.harnesses, versions=None)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=second, transcript_source=new_source)})
    new = Mock(spec=HarnessSet, registry=registry, versions=None)
    sets = HarnessSets(Mock(side_effect=[old, new]))
    sets.select(HarnessAssemblyKey((1,)))
    sets.retain(WorkerInvocation("lease_a", 1))
    sets.select(HarnessAssemblyKey((2,)))
    ctx = replace(
        ctx,
        harness_sets=sets,
        usage=replace(ctx.usage, invocation_registry=UsageRegistryLookup(sets, ctx.usage.harnesses)),
    )

    Advance(ctx).run()
    assert len(first.judged) == 1
    assert second.judged == []
    settings = judge_call.call_args.kwargs["session_settings"]
    assert (settings.binary, settings.config_dir, settings.bundle_path, settings.transcript_root) == (
        "old-bin",
        "old-home",
        "old-bundle",
        "old-root",
    )
    assert settings.environment == current_settings.environment
    assert settings.model_aliases == current_settings.model_aliases
    old_source.script_tail_position(_SHARED_SESSION_ID, TranscriptPosition("old-end"))
    old_source.read_raw_lines_calls.clear()
    new_source.read_raw_lines_calls.clear()
    lease = store.active_lease("lease_a")
    assert lease is not None

    assert ctx.usage.judge_transcript_lines(lease, store.bindings_for_chunk("ch_a"), generation=1) == ["old"]
    assert old_source.read_raw_lines_calls == [
        (_SHARED_SESSION_ID, TranscriptPosition("old-start"), TranscriptPosition("old-end"))
    ]
    assert new_source.read_raw_lines_calls == []


def test_judge_after_restart_anchors_current_session_location_across_relaunch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    handle = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    recovered = FakeHarness(handle=handle, verdict="pass", judge_output_usable=False)
    replacement = FakeHarness(handle=handle, verdict="pass")
    origin = replace(recovered.launch_settings(), binary="recovered-bin", bundle_path="recovered-bundle")
    current = replace(replacement.launch_settings(), binary="new-bin", model_aliases=(("tier", "new"),))
    monkeypatch.setattr(recovered, "launch_settings", Mock(return_value=origin))
    monkeypatch.setattr(replacement, "launch_settings", Mock(return_value=current))
    judge_call = Mock(wraps=recovered.judge)
    monkeypatch.setattr(recovered, "judge", judge_call)
    ctx, _, _ = _judgement_ctx(store, harness_a=recovered, harness_b=replacement, probe=FakeProbe())
    first = Mock(spec=HarnessSet, registry=ctx.harnesses, versions=None)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=replacement)})
    second = Mock(spec=HarnessSet, registry=registry, versions=None)
    sets = HarnessSets(Mock(side_effect=[first, second]))
    sets.select(HarnessAssemblyKey((1,)))
    ctx = replace(
        ctx,
        harness_sets=sets,
        usage=replace(ctx.usage, invocation_registry=UsageRegistryLookup(sets, ctx.usage.harnesses)),
    )

    Advance(ctx).run()
    assert sets.lookup(WorkerInvocation("lease_a", 1)) is first
    assert judge_call.call_args.kwargs["session_settings"] == origin
    sets.select(HarnessAssemblyKey((2,)))
    Advance(ctx).run()

    assert judge_call.call_count == 2
    settings = judge_call.call_args.kwargs["session_settings"]
    assert settings.binary == "recovered-bin"
    assert settings.bundle_path == "recovered-bundle"
    assert settings.model_aliases == current.model_aliases
    assert sets.lookup(JudgeInvocation("lease_a", 1)) is first


@pytest.mark.parametrize("binding", [None, HarnessBinding()])
def test_judge_defers_unavailable_current_settings_then_uses_replacement_values(
    tmp_path: Path, binding: HarnessBinding | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    handle = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    old_harness = FakeHarness(handle=handle, verdict="pass")
    current_harness = FakeHarness(handle=handle, verdict="fail")
    origin = replace(old_harness.launch_settings(), binary="old-binary", model_aliases=(("tier", "old"),))
    fresh = replace(
        current_harness.launch_settings(),
        binary="new-binary",
        model_aliases=(("tier", "fresh"),),
        environment=SessionEnvironment((("PATH", "fresh-path"),), "/fresh-shell"),
    )
    monkeypatch.setattr(old_harness, "launch_settings", Mock(return_value=origin))
    monkeypatch.setattr(current_harness, "launch_settings", Mock(return_value=fresh))
    judge_call = Mock(wraps=old_harness.judge)
    monkeypatch.setattr(old_harness, "judge", judge_call)
    ctx, _, _ = _judgement_ctx(store, harness_a=old_harness, harness_b=old_harness, probe=FakeProbe())
    old = Mock(spec=HarnessSet, registry=ctx.harnesses, versions=None)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: binding} if binding is not None else {})
    new = Mock(spec=HarnessSet, registry=registry, versions=None)
    fresh_registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=current_harness)})
    ready = Mock(spec=HarnessSet, registry=fresh_registry, versions=None)
    sets = HarnessSets(Mock(side_effect=[old, new, ready]))
    sets.select(HarnessAssemblyKey((1,)))
    sets.retain(WorkerInvocation("lease_a", 1))
    sets.select(HarnessAssemblyKey((2,)))
    ctx = replace(
        ctx,
        harness_sets=sets,
        usage=replace(ctx.usage, invocation_registry=UsageRegistryLookup(sets, ctx.usage.harnesses)),
    )

    Advance(ctx).run()
    assert old_harness.judged == []
    assert sets.find(JudgeInvocation("lease_a", 1)) is None
    assert store.in_flight_elicitation("lease_a", 1) is None
    assert store.active_lease("lease_a") is not None
    sets.select(HarnessAssemblyKey((3,)))
    Advance(ctx).run()
    assert len(old_harness.judged) == 1
    assert sets.lookup(JudgeInvocation("lease_a", 1)) is old
    settings = judge_call.call_args.kwargs["session_settings"]
    assert settings.binary == "old-binary"
    assert settings.model_aliases == fresh.model_aliases
    assert settings.environment == fresh.environment
    Advance(ctx).run()
    completion = next(item for item in store.pending_outbound() if item.kind == "completion.submitted")
    assert json.loads(completion.payload)["submission"]["choice"] == "pass"


def test_resume_drains_previous_generation_before_releasing_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    handle = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    old_source = FakeTranscriptSource(tail_positions_by_session={_SHARED_SESSION_ID: TranscriptPosition("old-tail")})
    new_source = FakeTranscriptSource(tail_positions_by_session={_SHARED_SESSION_ID: TranscriptPosition("new-tail")})
    old_harness = FakeHarness(handle=handle, verdict="pass", transcript_source=old_source)
    new_harness = FakeHarness(handle=handle, verdict="fail", transcript_source=new_source)
    ctx, _, _ = _judgement_ctx(store, harness_a=old_harness, harness_b=new_harness, probe=FakeProbe())
    old = Mock(spec=HarnessSet, registry=ctx.harnesses)
    new_registry = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=new_harness, transcript_source=new_source)}
    )
    new = Mock(spec=HarnessSet, registry=new_registry, versions=None)
    sets = HarnessSets(Mock(side_effect=[old, new]))
    sets.select(HarnessAssemblyKey((1,)))
    previous = WorkerInvocation("lease_a", 1)
    sets.retain(previous)
    sets.select(HarnessAssemblyKey((2,)))
    ctx = replace(
        ctx,
        harness_sets=sets,
        usage=replace(ctx.usage, invocation_registry=UsageRegistryLookup(sets, ctx.usage.harnesses)),
    )
    observed: list[str] = []

    def pump(_pump: TranscriptPump, lease_id: str, **_kwargs: object) -> bool:
        assert lease_id == "lease_a"
        assert sets.lookup(previous) is old
        observed.append("drained")
        return True

    monkeypatch.setattr(TranscriptPump, "pump_lease", pump)
    lease = store.active_lease("lease_a")
    assert lease is not None
    DormantSession(ctx, lease).resume_on_unmet_produces("continue", store.bindings_for_chunk("ch_a"))

    assert observed == ["drained"]
    assert sets.find(previous) is None
    assert sets.lookup(WorkerInvocation("lease_a", 2)) is new
    assert len(new_harness.resumed) == 1
    boundary = store.boundary("lease_a", 2, "resume")
    assert boundary is not None and boundary.start_position == "old-tail"


@pytest.mark.parametrize("failure", [False, RuntimeError("unavailable")])
def test_failed_resume_drain_does_not_block_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: bool | Exception
) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    handle = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    old_harness, new_harness = FakeHarness(handle=handle, verdict=None), FakeHarness(handle=handle, verdict=None)
    ctx, _, _ = _judgement_ctx(store, harness_a=old_harness, harness_b=new_harness, probe=FakeProbe())
    old = Mock(spec=HarnessSet, registry=ctx.harnesses)
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=new_harness)})
    new = Mock(spec=HarnessSet, registry=registry, versions=None)
    sets = HarnessSets(Mock(side_effect=[old, new]))
    sets.select(HarnessAssemblyKey((1,)))
    previous = WorkerInvocation("lease_a", 1)
    sets.retain(previous)
    sets.select(HarnessAssemblyKey((2,)))
    ctx = replace(
        ctx,
        harness_sets=sets,
        usage=replace(ctx.usage, invocation_registry=UsageRegistryLookup(sets, ctx.usage.harnesses)),
    )

    def pump(_pump: TranscriptPump, lease_id: str, **kwargs: object) -> bool:
        assert lease_id == "lease_a"
        assert sets.lookup(previous) is old
        assert kwargs.get("mark_incomplete", True)
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(TranscriptPump, "pump_lease", pump)
    lease = store.active_lease("lease_a")
    assert lease is not None
    dormant = DormantSession(ctx, lease)
    bindings = store.bindings_for_chunk("ch_a")

    dormant.resume_on_unmet_produces("continue", bindings)
    assert sets.find(previous) is None
    assert sets.lookup(WorkerInvocation("lease_a", 2)) is new
    assert len(new_harness.resumed) == 1


def test_restart_continues_after_failed_drain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)
    hub = FakeHub()
    hub.chunks["ch_a"] = ChunkState(chunk_id="ch_a", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1")
    handle = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    harness = FakeHarness(handle=handle, verdict=None)
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e-lease_a": "/ws/lease_a"}),
        harness=harness,
        probe=probe,
        clock=FixedClock(_NOW + timedelta(seconds=1)),
    )
    monkeypatch.setattr(TranscriptPump, "pump_lease", Mock(return_value=False))

    Resume(ctx).run()
    assert probe.killed == [100]
    assert len(harness.resumed) == 1
    assert store.resume_intent_lease_ids() == set()


def test_startup_reconciliation_uses_newly_composed_set(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store, lease_id="lease_a", chunk_id="ch_a", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100)
    handle = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    stale, current = FakeHarness(handle=handle, verdict=None), FakeHarness(handle=handle, verdict=None)
    old_registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=stale)})
    new_registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=current)})
    sets = HarnessSets(Mock(return_value=Mock(spec=HarnessSet, registry=new_registry)))
    sets.select(HarnessAssemblyKey((1,)))
    marking = ResumeMarking(
        make_stores(store),
        make_host_stores(store),
        FixedClock(_NOW),
        FakeProbe(),
        old_registry,
        invocation_harnesses=sets,
    )

    marking.reconcile_sessions()
    assert current.reconciled == [(_SHARED_SESSION_ID,)]
    assert stale.reconciled == []


def test_judgement_owner_failure_on_one_lease_never_blocks_the_others_collect(tmp_path: Path) -> None:
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

    # lease_a's own adapter elicited a verdict, prompt rendered off its own envelope.
    expected_prompt_a = JudgementPrompt(envelope_a, []).render()
    assert harness_a.judged == [("/ws/lease_a", _SHARED_SESSION_ID, expected_prompt_a)]
    # lease_b never got a launch call on ANY adapter — no in-flight elicitation for it.
    assert store.in_flight_elicitation("lease_b", 1) is None
    escalations = [e for e in store.open_escalations() if e.chunk_id == "ch_b"]
    assert len(escalations) == 1


# --------------------------------------------------------------------------- #
# TAKEOVER (src/blizzard/runner/lifecycle/takeover.py `_resolved_harness`).


def _takeover_service(
    store: SqlAlchemyRunnerStore, *, harness_a: FakeHarness, harness_b: FakeHarness
) -> TakeoverService:
    return TakeoverService(
        FixedClock(_NOW),
        FakeProbe(),
        takeover=store,
        elicitations=store,
        local_api_url="http://127.0.0.1:8431",
        workspaces=single_workspace(FakeProvider({})),
        harnesses=_two_harness_registry(harness_a, harness_b),
    )


def _open_scope(store: SqlAlchemyRunnerStore, chunk_id: str) -> TakeoverOpenScope:
    return TakeoverOpenScope(
        chunk_id=chunk_id,
        open_takeover=store.open_takeover_for_chunk(chunk_id),
        bindings=store.bindings_for_chunk(chunk_id),
        active_lease=store.active_lease_for_chunk(chunk_id),
        latest_lease_with_session=store.latest_lease_with_session_for_chunk(chunk_id),
        latest_epoch=store.latest_epoch(chunk_id),
        active_parked=_active_in(store, chunk_id, store.parked_lease_ids()),
        submission_pending=_active_in(store, chunk_id, store.pending_submission_lease_ids()),
    )


def test_takeover_opens_each_parked_chunk_through_its_own_harness(tmp_path: Path) -> None:
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
    service = _takeover_service(store, harness_a=harness_a, harness_b=harness_b)

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


def test_takeover_owner_failure_on_one_chunk_never_blocks_the_others_open(tmp_path: Path) -> None:
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
        FixedClock(_NOW),
        FakeProbe(),
        takeover=store,
        elicitations=store,
        local_api_url="http://127.0.0.1:8431",
        workspaces=single_workspace(FakeProvider({})),
        # Only `claude_code` is bound — `ch_b`'s own `unbound-harness` id resolves nowhere.
        harnesses=HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness_a)}),
    )

    opened_a = service.open(_open_scope(store, "ch_a"), force=False)

    assert opened_a.command == "cd /ws/lease_a && claude --resume shared-dispatch-session"
    assert harness_a.resume_command_config == [(None, None)]

    with pytest.raises(TakeoverOwnerUnresolvable):
        service.open(_open_scope(store, "ch_b"), force=False)

    # No takeover was ever recorded for the failed chunk — the fact-before-command ordering
    # (module docstring) never wrote a partial record for an owner it couldn't resolve past.
    assert store.open_takeover_for_chunk("ch_b") is None


def _active_in(store: SqlAlchemyRunnerStore, chunk_id: str, lease_ids: set[str]) -> bool:
    active = store.active_lease_for_chunk(chunk_id)
    return active is not None and active.lease_id in lease_ids


def test_hosted_services_follow_retained_then_current_set(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    for suffix in ("a", "b"):
        _seed_lease(
            store, lease_id=f"lease_{suffix}", chunk_id=f"ch_{suffix}", harness_id=CLAUDE_CODE_HARNESS_ID, pid=100
        )
        store.record_park(
            lease_id=f"lease_{suffix}", chunk_id=f"ch_{suffix}", question_id=f"qn_{suffix}", parked_at=_NOW
        )
    handle = WorkerHandle(session_id=_SHARED_SESSION_ID, pid=100, process_start_time="start-100", pgid=100)
    old_source, new_source = FakeTranscriptSource(), FakeTranscriptSource()
    old_harness, new_harness = FakeHarness(handle=handle, verdict=None), FakeHarness(handle=handle, verdict=None)
    old_registry = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=old_harness, transcript_source=old_source)}
    )
    new_registry = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=new_harness, transcript_source=new_source)}
    )
    sets = HarnessSets(
        Mock(side_effect=[Mock(spec=HarnessSet, registry=old_registry), Mock(spec=HarnessSet, registry=new_registry)])
    )
    sets.select(HarnessAssemblyKey((1,)))
    sets.retain(WorkerInvocation("lease_a", 1))
    graph = Mock(harness_sets=sets)

    def resolve(lease_id: str) -> HarnessRegistry:
        return _harnesses_for_lease(graph, lease_id)

    workspaces = single_workspace(FakeProvider({}))
    transcripts = TranscriptService(
        store,
        store,
        store,
        HarnessTranscriptRepositories(old_registry),
        FakeArchivedTranscriptRepository(),
        workspaces,
        transcripts_for_lease=lambda lease_id: HarnessTranscriptRepositories(resolve(lease_id)),
    )
    takeover = TakeoverService(
        FixedClock(_NOW),
        FakeProbe(),
        takeover=store,
        elicitations=store,
        local_api_url="http://localhost",
        harnesses=old_registry,
        harnesses_for_lease=resolve,
        workspaces=workspaces,
    )
    sets.select(HarnessAssemblyKey((2,)))
    for suffix in ("a", "b"):
        transcripts.for_lease(f"lease_{suffix}")
        [segment] = store.transcript_segments_for_chunk(f"ch_{suffix}")
        transcripts.segment_content(f"ch_{suffix}", segment.segment_id)
        takeover.open(_open_scope(store, f"ch_{suffix}"), force=False)
    assert len(old_source.turns_since_calls) == 2
    assert len(new_source.turns_since_calls) == 2
    assert len(old_harness.resume_command_config) == 1
    assert len(new_harness.resume_command_config) == 1
    sets.release_lease("lease_a")
    transcripts.for_lease("lease_a")
    assert len(new_source.turns_since_calls) == 3
