"""Named session pools, resolution, and the lease stamps (issue #144, phase 5).

Component tier over a **real store**, doubles only at the hub/harness/provider/probe
seams — each phase's assertion reads back the *previous* phase's actually-recorded
lease. That is what makes the pool-head resolution and stamp inheritance provable
here at all."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.graph import SessionMode
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.transcript import IHarnessTranscriptSource, TranscriptErrorFactory
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.loop.steps import Advance, Fill, Pull
from blizzard.runner.store.repository import NewLease
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, RotatePolicyView
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    QueuePeekEntry,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)

_NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
_CHOICES = [("pass", "ok"), ("fail", "no")]


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _pooled(node_name: str, node_id: str, *, mode: SessionMode, model: list[str] | None = None, effort=None):  # type: ignore[no-untyped-def]
    """An envelope for a node referencing the `code` pool, in either mode."""
    return make_envelope(
        "ch_1",
        node_name,
        node_id=node_id,
        choices=_CHOICES,
        session=mode,
        session_source="code",
        session_name="code",
        session_model=model if model is not None else ["blizzard:basic"],
        session_effort=effort,
    )


def _ctx(store, hub, provider, harness, *, minutes: int = 0):  # type: ignore[no-untyped-def]
    return make_context(
        store,
        hub=hub,
        provider=provider,
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_NOW + timedelta(minutes=minutes)),
    )


# Pool resolution: `fresh:<name>` mints the head a later `resume:<name>` continues.


@pytest.mark.component
def test_a_fresh_named_member_mints_the_head_a_later_resume_member_continues(tmp_path):  # type: ignore[no-untyped-def]
    """The core of the pool: `build` is `fresh:code`, `verify` is `resume:code`. The
    mint's session must be the one `verify` continues, even though `verify` has never run
    at its own node before — which is exactly what `resume:<node>` could not express."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    build_env = _pooled("build", "nd_build", mode=SessionMode.FRESH)
    verify_env = _pooled("verify", "nd_verify", mode=SessionMode.RESUME)

    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    h1 = FakeHarness(handle=WorkerHandle(session_id="sess-code-1", pid=100, process_start_time="t100"), verdict="pass")
    Fill(_ctx(store, hub, provider, h1)).run()

    assert h1.resume_froms == [None]  # `fresh:code` always mints

    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=verify_env)]
    h2 = FakeHarness(handle=WorkerHandle(session_id="unused", pid=200, process_start_time="t200"), verdict="pass")
    ctx2 = _ctx(store, hub, provider, h2, minutes=1)
    Advance(ctx2).run()
    Pull(ctx2).run()

    # `verify` continues the head `build` minted — a session it never ran at its own node.
    assert h2.resume_froms == ["sess-code-1"]
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    assert (lease.node_name, lease.session_id, lease.session_name) == ("verify", "sess-code-1", "code")


@pytest.mark.component
def test_an_empty_pool_falls_back_to_minting_rather_than_erroring(tmp_path):  # type: ignore[no-untyped-def]
    """First arrival at a `resume:<name>` node with no pool yet — best-effort, like
    #115's resume target."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    env = _pooled("verify", "nd_verify", mode=SessionMode.RESUME)

    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    harness = FakeHarness(handle=WorkerHandle(session_id="sess-1", pid=100, process_start_time="t"), verdict="pass")

    Fill(_ctx(store, hub, provider, harness)).run()

    assert harness.resume_froms == [None]
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.session_name == "code"  # still stamps the pool


@pytest.mark.component
def test_re_entering_a_fresh_named_node_mints_a_new_head_and_the_lineage_stays_linear(tmp_path):  # type: ignore[no-untyped-def]
    """A cyclic graph: build(`fresh:code`) → verify(`resume:code`) → build → verify.
    Two assertions: `fresh:<name>` is a **forced rotation point** (re-entry mints a
    second head), and D2's pool-member sequence stays **linear**, never forking."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    build_env = _pooled("build", "nd_build", mode=SessionMode.FRESH)
    verify_env = _pooled("verify", "nd_verify", mode=SessionMode.RESUME)

    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    h1 = FakeHarness(handle=WorkerHandle(session_id="sess-code-1", pid=100, process_start_time="t1"), verdict="pass")
    Fill(_ctx(store, hub, provider, h1)).run()

    # build#1 -> verify#1
    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=verify_env)]
    h2 = FakeHarness(handle=WorkerHandle(session_id="unused", pid=200, process_start_time="t2"), verdict="fail")
    ctx2 = _ctx(store, hub, provider, h2, minutes=1)
    Advance(ctx2).run()
    Pull(ctx2).run()

    # verify#1 fails -> back into build (`fresh:code`), which must MINT, not continue.
    hub.envelopes["ch_1"] = verify_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=build_env)]
    h3 = FakeHarness(handle=WorkerHandle(session_id="sess-code-2", pid=300, process_start_time="t3"), verdict="pass")
    ctx3 = _ctx(store, hub, provider, h3, minutes=2)
    Advance(ctx3).run()
    Pull(ctx3).run()

    assert h3.resume_froms == [None]  # the forced rotation point

    # build#2 -> verify#2, which must continue the NEW head.
    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=verify_env)]
    h4 = FakeHarness(handle=WorkerHandle(session_id="unused", pid=400, process_start_time="t4"), verdict="pass")
    ctx4 = _ctx(store, hub, provider, h4, minutes=3)
    Advance(ctx4).run()
    Pull(ctx4).run()

    assert h4.resume_froms == ["sess-code-2"]

    # D2: the whole traversal's pool sequence is linear — one head at a time, never a fork.
    pool_sessions = [
        lease.session_id for lease in sorted(_pool_leases(store, "ch_1", "code"), key=lambda lease: lease.created_at)
    ]
    assert pool_sessions == ["sess-code-1", "sess-code-1", "sess-code-2", "sess-code-2"]


def _pool_leases(store, chunk_id: str, session_name: str):  # type: ignore[no-untyped-def]
    """Every lease this chunk minted into ``session_name``'s pool, oldest first."""
    from sqlalchemy import select

    from blizzard.runner.store import schema as s

    with store._engine.connect() as conn:  # a store-internal read, test-only
        rows = conn.execute(
            select(s.leases, s.lease_context.c.session_name)
            .join(s.lease_context, s.lease_context.c.lease_id == s.leases.c.lease_id)
            .where(s.leases.c.chunk_id == chunk_id)
            .where(s.lease_context.c.session_name == session_name)
        ).all()
    return list(rows)


# Re-spawns join the pool (the retry regression the plan names).


@pytest.mark.component
def test_a_retry_at_a_pooled_node_becomes_the_head_a_later_member_continues(tmp_path):  # type: ignore[no-untyped-def]
    """A `fresh:code` node fails and retries; the retry's session, not the failed first
    attempt's, is what the next `resume:code` member continues — without the retry
    stamping `session_name`, a later member would resume the **failed** attempt."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    build_env = _pooled("build", "nd_build", mode=SessionMode.FRESH)
    verify_env = _pooled("verify", "nd_verify", mode=SessionMode.RESUME)

    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    h1 = FakeHarness(handle=WorkerHandle(session_id="sess-attempt-1", pid=100, process_start_time="t1"), verdict="pass")
    Fill(_ctx(store, hub, provider, h1)).run()

    # The first attempt produces no parseable verdict — a retry-consuming failure, which
    # re-spawns at the same node with a fresh session.
    hub.envelopes["ch_1"] = build_env
    h2 = FakeHarness(handle=WorkerHandle(session_id="sess-attempt-2", pid=200, process_start_time="t2"), verdict=None)
    ctx2 = _ctx(store, hub, provider, h2, minutes=1)
    Advance(ctx2).run()

    retry_lease = store.active_lease_for_chunk("ch_1")
    assert retry_lease is not None
    assert retry_lease.session_id == "sess-attempt-2"
    # The retry joined the pool — this is the assertion the regression turns on.
    assert retry_lease.session_name == "code"

    # A later `resume:code` member continues the RETRY's session, not the failed first.
    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=verify_env)]
    h3 = FakeHarness(handle=WorkerHandle(session_id="unused", pid=300, process_start_time="t3"), verdict="pass")
    ctx3 = _ctx(store, hub, provider, h3, minutes=2)
    Advance(ctx3).run()
    Pull(ctx3).run()

    assert h3.resume_froms == ["sess-attempt-2"]


# The stamp describes the session, not the preference.


@pytest.mark.component
def test_a_mint_stamps_what_it_resolved(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    env = _pooled("build", "nd_build", mode=SessionMode.FRESH, model=["blizzard:basic"], effort="medium")

    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    harness = FakeHarness(handle=WorkerHandle(session_id="sess-1", pid=100, process_start_time="t"), verdict="pass")
    harness.resolved_model = "sonnet"

    Fill(_ctx(store, hub, provider, harness)).run()

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    assert (lease.resolved_model, lease.resolved_effort) == ("sonnet", "medium")
    # And the mint carried them to the harness.
    assert harness.spawn_model_effort == [("sonnet", "medium")]


@pytest.mark.component
def test_a_bare_resume_node_entered_after_a_pooled_one_stamps_the_pools_model(tmp_path):  # type: ignore[no-untyped-def]
    """The case that makes stamp-inheritance load-bearing: `retrospective` carries a
    bare `resume:` (no pool, no declaration) that resumes the `code` pool's session with
    no `--model` — stamping the fresh preference would book spend against the wrong model."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    build_env = _pooled("build", "nd_build", mode=SessionMode.FRESH, model=["blizzard:basic"], effort="medium")
    # Bare `resume` — belongs to no pool and declares nothing.
    retro_env = make_envelope("ch_1", "retrospective", node_id="nd_retro", choices=_CHOICES, session=SessionMode.RESUME)

    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    h1 = FakeHarness(handle=WorkerHandle(session_id="sess-code-1", pid=100, process_start_time="t1"), verdict="pass")
    h1.resolved_model = "sonnet"
    Fill(_ctx(store, hub, provider, h1)).run()

    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=retro_env)]
    h2 = FakeHarness(handle=WorkerHandle(session_id="unused", pid=200, process_start_time="t2"), verdict="pass")
    # A fresh resolution at `retrospective` would produce the runner default…
    h2.resolved_model = "opus"
    ctx2 = _ctx(store, hub, provider, h2, minutes=1)
    Advance(ctx2).run()
    Pull(ctx2).run()

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.node_name == "retrospective"
    assert lease.session_id == "sess-code-1"  # it resumed the pool's session
    # …but the stamp is the SESSION's, inherited — not the fresh preference.
    assert (lease.resolved_model, lease.resolved_effort) == ("sonnet", "medium")
    # And no model reached the harness on the resume, so the process really is on sonnet.
    assert h2.spawn_model_effort == [("sonnet", "medium")]


@pytest.mark.component
def test_a_lease_predating_the_stamps_inherits_unknown_rather_than_a_guess(tmp_path):  # type: ignore[no-untyped-def]
    """A session first spawned before the stamps existed reads NULL — *unknown*. Both
    consumers decline to guess, so the resume inherits `None` rather than substituting a
    default it would then present as the fact of what the session ran."""
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    build_env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES, session=SessionMode.FRESH)
    resume_env = make_envelope("ch_1", "verify", node_id="nd_verify", choices=_CHOICES, session=SessionMode.RESUME)

    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    h1 = FakeHarness(handle=WorkerHandle(session_id="sess-1", pid=100, process_start_time="t1"), verdict="pass")
    Fill(_ctx(store, hub, provider, h1)).run()
    # Simulate a pre-#144 lease: blank the stamps the mint just wrote.
    _blank_stamps(store, "ch_1")

    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=resume_env)]
    h2 = FakeHarness(handle=WorkerHandle(session_id="unused", pid=200, process_start_time="t2"), verdict="pass")
    ctx2 = _ctx(store, hub, provider, h2, minutes=1)
    Advance(ctx2).run()
    Pull(ctx2).run()

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    assert (lease.resolved_model, lease.resolved_effort) == (None, None)
    assert h2.spawn_model_effort == [(None, None)]


def _blank_stamps(store, chunk_id: str) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import update

    from blizzard.runner.store import schema as s

    with store._engine.begin() as conn:  # a store-internal write, test-only
        conn.execute(
            update(s.lease_context)
            .where(s.lease_context.c.chunk_id == chunk_id)
            .values(session_name=None, resolved_model=None, resolved_effort=None)
        )


# Rotation (issue #144, phase 6): a head is resumed only while every readable declared
# threshold is under bound AND its stamped model matches; an unreadable signal is not a breach.


def _rotate(**bounds):  # type: ignore[no-untyped-def]
    return RotatePolicyView(**bounds)


def _bounded(mode: SessionMode, rotate=None, model=None):  # type: ignore[no-untyped-def]
    return make_envelope(
        "ch_1",
        "verify",
        node_id="nd_verify",
        choices=_CHOICES,
        session=mode,
        session_source="code",
        session_name="code",
        session_model=model if model is not None else ["blizzard:basic"],
        session_rotate=rotate,
    )


def _seed_head(store, *, session_id: str = "sess-head", model: str = "sonnet") -> str:
    """A pool head for `code` — a session-bearing lease stamping the pool name."""
    store.record_lease(
        NewLease(
            lease_id="lease_head",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            session_name="code",
            resolved_model=model,
            resolved_effort="medium",
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_head", pid=1, process_start_time="t", session_id=session_id, spawned_at=_NOW)
    return session_id


def _seed_usage(store, *, kind: str = "spawn", generation: int = 1, tokens: int = 0) -> None:
    store.record_usage(
        lease_id="lease_head",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=generation,
        sample=UsageSample(
            kind=kind,  # type: ignore[arg-type]
            model="sonnet",
            input_tokens=tokens,
            output_tokens=0,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=None,
        ),
        recorded_at=_NOW + timedelta(seconds=generation),
    )


def _resolve(
    store,  # type: ignore[no-untyped-def]
    envelope,
    *,
    transcript_source: IHarnessTranscriptSource | None = None,
    resolved_model: str = "sonnet",
):
    """Run the node-entry resolver against a seeded store; returns the resume target."""
    harness = FakeHarness(
        handle=WorkerHandle(session_id="unused", pid=9, process_start_time="t"),
        verdict="pass",
        transcript_source=transcript_source,
    )
    harness.resolved_model = resolved_model
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )
    return ctx.sessions.resume_target("ch_1", envelope.node, "/ws/e1")


@pytest.mark.component
def test_a_head_under_every_declared_bound_is_resumed(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    head = _seed_head(store)
    _seed_usage(store, tokens=10)
    env = _bounded(SessionMode.RESUME, _rotate(max_context_tokens=1000, max_invocations=10))

    assert _resolve(store, env) == head


@pytest.mark.component
def test_a_declaration_with_no_rotate_block_bounds_nothing(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    head = _seed_head(store)
    for generation in range(1, 20):
        _seed_usage(store, generation=generation, tokens=10_000_000)

    assert _resolve(store, _bounded(SessionMode.RESUME)) == head


@pytest.mark.component
@pytest.mark.parametrize(
    ("tokens", "bound", "resumed"),
    [(99, 100, True), (100, 100, True), (101, 100, False)],
    ids=["under", "at", "over"],
)
def test_max_context_tokens_fires_strictly_over_the_bound(tmp_path, tokens, bound, resumed):  # type: ignore[no-untyped-def]
    """At the bound is still under it — a threshold is a ceiling, not a trigger point."""
    store = _store(tmp_path)
    head = _seed_head(store)
    _seed_usage(store, tokens=tokens)
    env = _bounded(SessionMode.RESUME, _rotate(max_context_tokens=bound))

    assert _resolve(store, env) == (head if resumed else None)


@pytest.mark.component
@pytest.mark.parametrize(
    ("invocations", "bound", "resumed"),
    [(2, 3, True), (3, 3, True), (4, 3, False)],
    ids=["under", "at", "over"],
)
def test_max_invocations_counts_harness_invocations_not_node_steps(tmp_path, invocations, bound, resumed):  # type: ignore[no-untyped-def]
    """Every `usage_facts` row counts — `spawn`, `resume`, `judge`, `nudge` alike — so one
    node-step burns two or three. An author setting this from a node-step count bounds the
    lineage roughly three times tighter than they intend."""
    store = _store(tmp_path)
    head = _seed_head(store)
    for generation in range(1, invocations + 1):
        _seed_usage(store, kind="judge" if generation % 2 else "spawn", generation=generation)
    env = _bounded(SessionMode.RESUME, _rotate(max_invocations=bound))

    assert _resolve(store, env) == (head if resumed else None)


@pytest.mark.component
@pytest.mark.parametrize(
    ("size", "bound", "resumed"),
    [(99, 100, True), (100, 100, True), (101, 100, False)],
    ids=["under", "at", "over"],
)
def test_max_transcript_bytes_fires_strictly_over_the_bound(tmp_path, size, bound, resumed):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    head = _seed_head(store)
    env = _bounded(SessionMode.RESUME, _rotate(max_transcript_bytes=bound))
    transcript_source = FakeTranscriptSource(sizes_by_session={head: size})

    assert _resolve(store, env, transcript_source=transcript_source) == (head if resumed else None)


@pytest.mark.component
def test_an_unreadable_context_signal_is_not_a_breach(tmp_path):  # type: ignore[no-untyped-def]
    """A freshly minted head has no usage fact yet. Reading that as 0 would be right by
    accident; reading it as a breach would make every new head instantly ineligible."""
    store = _store(tmp_path)
    head = _seed_head(store)  # no usage seeded at all
    env = _bounded(SessionMode.RESUME, _rotate(max_context_tokens=1))

    assert _resolve(store, env) == head


@pytest.mark.component
def test_an_unreadable_transcript_size_is_not_a_breach(tmp_path):  # type: ignore[no-untyped-def]
    """A session absent from the source's map (or a harness with no transcript source
    scripted at all, the default) reads its size as `None` — *not measured*, exactly
    like a missing file — never a zero that would make the threshold silently inert."""
    store = _store(tmp_path)
    head = _seed_head(store)
    env = _bounded(SessionMode.RESUME, _rotate(max_transcript_bytes=1))

    assert _resolve(store, env, transcript_source=FakeTranscriptSource(sizes_by_session={})) == head
    assert _resolve(store, env) == head


@pytest.mark.component
def test_model_drift_rotates_even_with_every_threshold_under_bound(tmp_path):  # type: ignore[no-untyped-def]
    """A graph edit changed the pool's model list mid-chunk. The head stays on what it was
    minted with — a cross-model resume is structurally impossible — so the change takes
    effect at the next mint, which is where a fresh context is being built anyway."""
    store = _store(tmp_path)
    _seed_head(store, model="sonnet")
    _seed_usage(store, tokens=1)
    env = _bounded(SessionMode.RESUME, _rotate(max_context_tokens=1_000_000, max_invocations=1000))

    assert _resolve(store, env, resolved_model="opus") is None


@pytest.mark.component
def test_no_drift_when_the_resolved_model_still_matches_the_stamp(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    head = _seed_head(store, model="sonnet")

    assert _resolve(store, _bounded(SessionMode.RESUME), resolved_model="sonnet") == head


@pytest.mark.component
def test_a_head_with_no_model_stamp_cannot_drift(tmp_path):  # type: ignore[no-untyped-def]
    """A pre-#144 lease reads NULL — *unknown*. Comparing an unknown against a resolved
    name would rotate every such session once, for no reason anyone could name."""
    store = _store(tmp_path)
    head = _seed_head(store, model=None)  # type: ignore[arg-type]

    assert _resolve(store, _bounded(SessionMode.RESUME), resolved_model="opus") == head


@pytest.mark.component
def test_max_transcript_bytes_fires_against_the_real_repository_at_the_production_root(tmp_path):  # type: ignore[no-untyped-def]
    """The threshold end to end over the **real** transcript source (blizzard#245), at a
    production path shape — not a scripted size. Pins that the comparison actually reads
    the thing it thinks it is, since a silently-``None`` ``size_bytes`` would stay green."""
    store = _store(tmp_path)
    head = _seed_head(store)
    projects_root = tmp_path / "projects"
    project_dir = projects_root / ClaudeCodeTranscriptSource.mangle_cwd("/ws/e1")
    project_dir.mkdir(parents=True)
    (project_dir / f"{head}.jsonl").write_text("x" * 5000)
    source = ClaudeCodeTranscriptSource(str(projects_root), TranscriptErrorFactory(get_logger("test")))

    assert (
        _resolve(store, _bounded(SessionMode.RESUME, _rotate(max_transcript_bytes=10_000)), transcript_source=source)
        == head
    )
    assert (
        _resolve(store, _bounded(SessionMode.RESUME, _rotate(max_transcript_bytes=1_000)), transcript_source=source)
        is None
    )
