"""Runner usage telemetry — recording, buffering, and cleanup (epic #57, issue #58).

ADVANCE records one append-only usage fact per attempt invocation — the spawn/resume
that produced the exited worker, and the judgement resume that elicited its verdict —
and buffers each fact's outbound report on the same store-and-forward rails as
``lease.minted``, atomically with the local write. The per-lease stdout redirect
(Phase 1) survives a killed/reaped worker for ADVANCE's readback; when no envelope
survived, the fallback sums the raw session transcript with ``cost_usd`` left absent —
never fabricated. Facts, not aggregates (``bzh:facts-not-status``): a chunk's total is a
derived sum over these, the hub's job (Phase 3), not this module's.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.loop import steps
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import advance
from blizzard.runner.store.repository import NewLease
from blizzard.wire.facts import USAGE_RECORDED
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscripts,
    make_context,
    make_envelope,
    make_store,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _build_envelope(chunk="ch_1"):  # type: ignore[no-untyped-def]
    return make_envelope(chunk, "build", node_id="nd_build", choices=_CHOICES)


def _seed_running_lease(store, *, chunk="ch_1", lease="lease_1", session="sess-a", epoch=1):  # type: ignore[no-untyped-def]
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
    store.record_spawn(lease, pid=100, process_start_time="start-100", session_id=session, spawned_at=_NOW)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _write_stdout(stdout_dir, lease_id, generation, content="<envelope>") -> None:  # type: ignore[no-untyped-def]
    (stdout_dir / f"{lease_id}.{generation}.stdout").write_text(content)


def _usage_payloads(store):  # type: ignore[no-untyped-def]
    return [json.loads(b.payload) for b in store.pending_outbound() if b.kind == USAGE_RECORDED]


@pytest.mark.unit
def test_advance_records_spawn_and_judge_usage_facts(tmp_path):  # type: ignore[no-untyped-def]
    """The happy path: an exited worker's own invocation (kind=spawn, generation 1) and
    the judgement resume (kind=judge) each land their own fact, buffered outbound."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    _write_stdout(stdout_dir, "lease_1", 1)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    spawn_sample = UsageSample(
        kind="spawn",
        model="claude-x",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=5,
        cache_create_tokens=6,
        cost_usd=0.42,
    )
    judge_sample = UsageSample(
        kind="judge",
        model="claude-x",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.01,
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass", usage_by_kind={"spawn": spawn_sample, "judge": judge_sample})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", worker_stdout_dir=str(stdout_dir)),
    )

    advance(ctx)

    payloads = _usage_payloads(store)
    assert len(payloads) == 2
    assert {p["kind"] for p in payloads} == {"spawn", "judge"}
    assert all(p["chunk_id"] == "ch_1" and p["node_id"] == "nd_build" and p["epoch"] == 1 for p in payloads)
    totals = store.usage_since(_NOW)
    assert totals.input_tokens == 110  # 100 (spawn) + 10 (judge)
    assert totals.cost_partial is False


@pytest.mark.unit
def test_advance_records_resume_kind_on_a_later_generation(tmp_path):  # type: ignore[no-untyped-def]
    """A resume within the same lease (a later spawn generation) records kind=resume —
    a genuinely new fact, distinct from a fresh lease's kind=spawn."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    # Simulate a restart/answer/pause resume: a second spawn generation, same lease.
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    _write_stdout(stdout_dir, "lease_1", 2)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    resume_sample = UsageSample(
        kind="resume",
        model="claude-x",
        input_tokens=20,
        output_tokens=10,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.05,
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass", usage_by_kind={"resume": resume_sample})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", worker_stdout_dir=str(stdout_dir)),
    )

    advance(ctx)

    payloads = _usage_payloads(store)
    resume_payload = next(p for p in payloads if p["kind"] == "resume")
    assert resume_payload["input_tokens"] == 20


@pytest.mark.unit
def test_resume_generation_with_no_envelope_of_its_own_never_reads_the_prior_generations(tmp_path):  # type: ignore[no-untyped-def]
    """Pins the cross-generation contamination fix: a generation-2 resume that exits
    without writing its **own** result envelope must fall through to the
    transcript-sum fallback (cost-absent) — never replay generation 1's still-present
    envelope file under the new (``lease``, ``resume``) usage key. Before the
    per-generation stdout split, readback re-scanned the one shared per-lease file and
    would parse generation 1's stale envelope here, over-counting its tokens and cost
    a second time under a key the ``runner:usage-attributed-once`` invariant cannot
    catch (it is a *distinct* key from generation 1's own fact)."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    # Simulate a restart/answer/pause resume that landed generation 2, same lease.
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    # If readback were to (mis)read generation 1's leftover file for generation 2, the
    # content-agnostic fake would hand back this contamination sample for kind=resume —
    # a stand-in for "the prior generation's envelope, parsed a second time".
    contamination_sample = UsageSample(
        kind="resume",
        model="claude-x",
        input_tokens=999,
        output_tokens=999,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=9.99,
    )
    fallback_sample = UsageSample(
        kind="resume",
        model="claude-x",
        input_tokens=3,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
    )
    harness = FakeHarness(
        handle=_HANDLE, verdict="pass", usage_by_kind={"resume": contamination_sample}, transcript_usage=fallback_sample
    )
    transcripts = FakeTranscripts({"sess-a": ['{"type": "assistant", "message": {}}']})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", worker_stdout_dir=str(stdout_dir)),
        transcripts=transcripts,
    )
    # Generation 1's own envelope file is still on disk (not yet cleaned up — tenure
    # hasn't ended) — but generation 2 never wrote its own file: an envelope-less
    # done-exit. Written via the module's own path-builder (not a hardcoded name) so
    # a mutation collapsing the per-generation path back to one shared per-lease file
    # writes generation 1's content to the exact path generation 2's readback would
    # then (wrongly) reuse.
    with open(steps._stdout_path(ctx, "lease_1", 1), "w") as f:
        f.write("<generation-1's own envelope>")

    advance(ctx)

    payloads = _usage_payloads(store)
    resume_payload = next(p for p in payloads if p["kind"] == "resume")
    assert resume_payload["input_tokens"] == 3  # the honest transcript fallback...
    assert resume_payload["cost_usd"] is None  # ...never generation 1's envelope, replayed
    assert store.usage_since(_NOW).cost_partial is True


@pytest.mark.unit
def test_advance_falls_back_to_transcript_usage_when_no_envelope(tmp_path):  # type: ignore[no-untyped-def]
    """A worker killed/reaped before writing a result envelope: ADVANCE sums the raw
    session transcript instead, cost left absent — never fabricated."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    # No stdout file at all — envelope-less exit.
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    fallback_sample = UsageSample(
        kind="spawn",
        model="claude-x",
        input_tokens=7,
        output_tokens=3,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass", transcript_usage=fallback_sample)
    transcripts = FakeTranscripts({"sess-a": ['{"type": "assistant", "message": {}}']})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", worker_stdout_dir=str(stdout_dir)),
        transcripts=transcripts,
    )

    advance(ctx)

    payloads = _usage_payloads(store)
    spawn_payload = next(p for p in payloads if p["kind"] == "spawn")
    assert spawn_payload["cost_usd"] is None
    assert spawn_payload["input_tokens"] == 7
    totals = store.usage_since(_NOW)
    assert totals.cost_partial is True


@pytest.mark.unit
def test_advance_records_no_usage_fact_when_no_envelope_and_no_transcript(tmp_path):  # type: ignore[no-untyped-def]
    """No envelope and no transcript lines: no fact at all — never a fabricated zero."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1"),  # no worker_stdout_dir, no transcripts
    )

    advance(ctx)

    assert _usage_payloads(store) == []


@pytest.mark.unit
def test_usage_replay_after_crash_is_idempotent(tmp_path):  # type: ignore[no-untyped-def]
    """A crash between the usage write and the completion buffer, replayed by the next
    tick reaching the same exited worker again, records nothing a second time."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    _write_stdout(stdout_dir, "lease_1", 1)
    sample = UsageSample(
        kind="spawn",
        model="claude-x",
        input_tokens=9,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.1,
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass", usage_by_kind={"spawn": sample})
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", worker_stdout_dir=str(stdout_dir)),
    )
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    bindings = store.bindings_for_chunk("ch_1")

    steps._record_attempt_usage(ctx, lease, bindings, judge_output="<judged output>")
    steps._record_attempt_usage(ctx, lease, bindings, judge_output="<judged output>")  # the replay

    payloads = _usage_payloads(store)
    assert len(payloads) == 1  # the spawn fact only — not doubled by the replay
    assert store.usage_since(_NOW).input_tokens == 9


@pytest.mark.unit
def test_ask_and_exit_records_the_worker_usage_before_parking(tmp_path):  # type: ignore[no-untyped-def]
    """A worker that asked-and-exited burned its spawn invocation's tokens: ADVANCE
    parks the chunk on the question *and* records that spawn usage (issue #58) — the
    token-burning attempt is not silently undercounted just because no verdict followed.
    No judgement ran, so only the worker's own fact is recorded."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Which API?",
        options=["rest", "graphql"],
        session_id="sess-a",
        asked_at=_NOW,
    )
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    _write_stdout(stdout_dir, "lease_1", 1)
    spawn_sample = UsageSample(
        kind="spawn",
        model="claude-x",
        input_tokens=77,
        output_tokens=11,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.33,
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass", usage_by_kind={"spawn": spawn_sample})
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", worker_stdout_dir=str(stdout_dir)),
    )

    advance(ctx)

    # Parked on the question, no verdict elicited — a park is not a judgement.
    assert store.parked_lease_ids() == {"lease_1"}
    assert harness.judged == []
    # But the spawn invocation that asked-and-exited is accounted for, once.
    payloads = _usage_payloads(store)
    assert [p["kind"] for p in payloads] == ["spawn"]
    assert payloads[0]["input_tokens"] == 77
    assert store.usage_since(_NOW).input_tokens == 77


@pytest.mark.unit
def test_ask_park_worker_usage_is_idempotent_across_a_re_park(tmp_path):  # type: ignore[no-untyped-def]
    """Re-reaching the ask-park site (a crash between the usage write and the park,
    replayed next tick) records the worker's spawn usage only once — keyed on
    ``(lease, generation, kind)``, so the re-park drops the duplicate."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    _write_stdout(stdout_dir, "lease_1", 1)
    sample = UsageSample(
        kind="spawn",
        model="claude-x",
        input_tokens=12,
        output_tokens=3,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.02,
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass", usage_by_kind={"spawn": sample})
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", worker_stdout_dir=str(stdout_dir)),
    )
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Q",
        options=[],
        session_id="sess-a",
        asked_at=_NOW,
    )
    ask = store.unforwarded_ask("lease_1")
    assert ask is not None
    steps._park_on_ask(ctx, lease, ask)
    steps._park_on_ask(ctx, lease, ask)  # the replay

    assert len(_usage_payloads(store)) == 1
    assert store.usage_since(_NOW).input_tokens == 12


@pytest.mark.unit
def test_verdict_less_failure_still_records_spawn_and_judge_usage(tmp_path):  # type: ignore[no-untyped-def]
    """A dead worker whose session cannot answer a parseable <Choice> fails and requeues
    — but it burned the same spawn + judge invocations a passing one does, so ADVANCE
    records both facts before failing it (issue #58). The retry mints a fresh lease and
    discards this one's stdout, so recording here is the only chance to count its spend."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    _write_stdout(stdout_dir, "lease_1", 1)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    spawn_sample = UsageSample(
        kind="spawn",
        model="claude-x",
        input_tokens=60,
        output_tokens=20,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.5,
    )
    judge_sample = UsageSample(
        kind="judge",
        model="claude-x",
        input_tokens=8,
        output_tokens=2,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.01,
    )
    harness = FakeHarness(handle=_HANDLE, verdict=None, usage_by_kind={"spawn": spawn_sample, "judge": judge_sample})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", worker_stdout_dir=str(stdout_dir)),
    )

    advance(ctx)

    # It failed the attempt: no completion buffered, the attempt requeued.
    completions = [f for f in store.pending_outbound() if f.kind == "completion.submitted"]
    assert completions == []
    # ...but both burned invocations are accounted for before the fail.
    payloads = _usage_payloads(store)
    assert {p["kind"] for p in payloads} == {"spawn", "judge"}
    totals = store.usage_since(_NOW)
    assert totals.input_tokens == 68  # 60 (spawn) + 8 (judge)


@pytest.mark.unit
def test_verdict_less_failure_falls_back_to_transcript_when_no_envelope(tmp_path):  # type: ignore[no-untyped-def]
    """A killed/reaped worker that also fails verdict-less: no envelope survived, so its
    spawn usage sums from the raw transcript with cost absent (never fabricated) — and it
    is still recorded rather than lost to the requeue."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    fallback_sample = UsageSample(
        kind="spawn",
        model="claude-x",
        input_tokens=5,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
    )
    harness = FakeHarness(handle=_HANDLE, verdict=None, transcript_usage=fallback_sample)
    transcripts = FakeTranscripts({"sess-a": ['{"type": "assistant", "message": {}}']})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", worker_stdout_dir=str(tmp_path / "missing")),
        transcripts=transcripts,
    )

    advance(ctx)

    spawn_payload = next(p for p in _usage_payloads(store) if p["kind"] == "spawn")
    assert spawn_payload["cost_usd"] is None
    assert spawn_payload["input_tokens"] == 5
    assert store.usage_since(_NOW).cost_partial is True


@pytest.mark.unit
def test_release_all_cleans_up_every_lease_stdout_file(tmp_path):  # type: ignore[no-untyped-def]
    """Tenure-end release removes every per-generation stdout file for every lease
    this chunk ever minted — bounded, one file per attempt ever made under each
    lease, no longer needed once its usage facts are durable."""
    store = _store(tmp_path)
    _seed_running_lease(store, lease="lease_1", epoch=1)
    # lease_1 resumed once (generation 2) before it closed — both its files must go.
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="reaped", closed_at=_NOW)
    _seed_running_lease(store, lease="lease_2", epoch=2)
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    _write_stdout(stdout_dir, "lease_1", 1)
    _write_stdout(stdout_dir, "lease_1", 2)
    _write_stdout(stdout_dir, "lease_2", 1)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", worker_stdout_dir=str(stdout_dir)),
    )

    steps._release_all(ctx, "ch_1")

    assert not (stdout_dir / "lease_1.1.stdout").exists()
    assert not (stdout_dir / "lease_1.2.stdout").exists()
    assert not (stdout_dir / "lease_2.1.stdout").exists()


@pytest.mark.unit
def test_release_all_is_a_noop_when_no_stdout_dir_configured(tmp_path):  # type: ignore[no-untyped-def]
    """No ``worker_stdout_dir`` (Phase 1's default) — release touches no filesystem path."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )

    steps._release_all(ctx, "ch_1")  # must not raise

    assert store.held_environment_ids() == []
