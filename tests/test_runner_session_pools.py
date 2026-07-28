"""Named session pools, resolution, and the lease stamps (issue #144, phase 5).

Component tier over a **real store**, doubles only at the hub/harness/provider/probe
seams — so each phase's assertion reads back the *previous* phase's actually-recorded
lease rather than a scripted double, exactly as ``test_runner_loop``'s #115 session-mode
tests do. That is what makes the pool-head resolution and the stamp inheritance provable
here at all: both are reads over facts the prior spawn wrote.

The unit-tier halves — argv placement, resolution order — live in
``test_runner_harness_adapter``; this file is about which session a spawn continues, and
what it records about it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.graph import SessionMode
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import advance, fill, pull
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
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


# --------------------------------------------------------------------------- #
# Pool resolution: `fresh:<name>` mints the head a later `resume:<name>` continues.
# --------------------------------------------------------------------------- #


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
    fill(_ctx(store, hub, provider, h1))

    assert h1.resume_froms == [None]  # `fresh:code` always mints

    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=verify_env)]
    h2 = FakeHarness(handle=WorkerHandle(session_id="unused", pid=200, process_start_time="t200"), verdict="pass")
    ctx2 = _ctx(store, hub, provider, h2, minutes=1)
    advance(ctx2)
    pull(ctx2)

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

    fill(_ctx(store, hub, provider, harness))

    assert harness.resume_froms == [None]
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None and lease.session_name == "code"  # still stamps the pool


@pytest.mark.component
def test_re_entering_a_fresh_named_node_mints_a_new_head_and_the_lineage_stays_linear(tmp_path):  # type: ignore[no-untyped-def]
    """A cyclic graph: build(`fresh:code`) → verify(`resume:code`) → build → verify.

    Two assertions in one traversal. (1) `fresh:<name>` is a **forced rotation point** —
    re-entering it mints a second head rather than continuing the first, so each iteration
    starts clean. (2) D2's pool-member serialization: the pool's session-id sequence is
    **linear** — every member continues the current head and the lineage never forks,
    which holds structurally because a chunk has at most one live lease.
    """
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    build_env = _pooled("build", "nd_build", mode=SessionMode.FRESH)
    verify_env = _pooled("verify", "nd_verify", mode=SessionMode.RESUME)

    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    h1 = FakeHarness(handle=WorkerHandle(session_id="sess-code-1", pid=100, process_start_time="t1"), verdict="pass")
    fill(_ctx(store, hub, provider, h1))

    # build#1 -> verify#1
    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=verify_env)]
    h2 = FakeHarness(handle=WorkerHandle(session_id="unused", pid=200, process_start_time="t2"), verdict="fail")
    ctx2 = _ctx(store, hub, provider, h2, minutes=1)
    advance(ctx2)
    pull(ctx2)

    # verify#1 fails -> back into build (`fresh:code`), which must MINT, not continue.
    hub.envelopes["ch_1"] = verify_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=build_env)]
    h3 = FakeHarness(handle=WorkerHandle(session_id="sess-code-2", pid=300, process_start_time="t3"), verdict="pass")
    ctx3 = _ctx(store, hub, provider, h3, minutes=2)
    advance(ctx3)
    pull(ctx3)

    assert h3.resume_froms == [None]  # the forced rotation point

    # build#2 -> verify#2, which must continue the NEW head.
    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=verify_env)]
    h4 = FakeHarness(handle=WorkerHandle(session_id="unused", pid=400, process_start_time="t4"), verdict="pass")
    ctx4 = _ctx(store, hub, provider, h4, minutes=3)
    advance(ctx4)
    pull(ctx4)

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


# --------------------------------------------------------------------------- #
# Re-spawns join the pool (the retry regression the plan names).
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_a_retry_at_a_pooled_node_becomes_the_head_a_later_member_continues(tmp_path):  # type: ignore[no-untyped-def]
    """A `fresh:code` node fails and retries; the retry's session — not the failed first
    attempt's — is what the next `resume:code` member continues.

    Without the retry stamping `session_name`, `pool_head` would not see it and a later
    member would resume the **failed** attempt — a regression against #115's
    `resume:<node>`, which already returns the newest lease at that node.
    """
    store = _store(tmp_path)
    hub = FakeHub()
    provider = FakeProvider({"e1": "/ws/e1"})
    build_env = _pooled("build", "nd_build", mode=SessionMode.FRESH)
    verify_env = _pooled("verify", "nd_verify", mode=SessionMode.RESUME)

    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", build_env)
    h1 = FakeHarness(handle=WorkerHandle(session_id="sess-attempt-1", pid=100, process_start_time="t1"), verdict="pass")
    fill(_ctx(store, hub, provider, h1))

    # The first attempt produces no parseable verdict — a retry-consuming failure, which
    # re-spawns at the same node with a fresh session.
    hub.envelopes["ch_1"] = build_env
    h2 = FakeHarness(handle=WorkerHandle(session_id="sess-attempt-2", pid=200, process_start_time="t2"), verdict=None)
    ctx2 = _ctx(store, hub, provider, h2, minutes=1)
    advance(ctx2)

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
    advance(ctx3)
    pull(ctx3)

    assert h3.resume_froms == ["sess-attempt-2"]


# --------------------------------------------------------------------------- #
# The stamp describes the session, not the preference.
# --------------------------------------------------------------------------- #


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

    fill(_ctx(store, hub, provider, harness))

    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    assert (lease.resolved_model, lease.resolved_effort) == ("sonnet", "medium")
    # And the mint carried them to the harness.
    assert harness.spawn_model_effort == [("sonnet", "medium")]


@pytest.mark.component
def test_a_bare_resume_node_entered_after_a_pooled_one_stamps_the_pools_model(tmp_path):  # type: ignore[no-untyped-def]
    """The case that makes stamp-inheritance load-bearing rather than tidy.

    `retrospective` carries a bare `resume:` — no pool, no declaration, and (here) no
    chunk default, so a *fresh* resolution for it would fall to the runner default. But it
    resumes the `code` pool's session and passes no `--model`, so the process keeps
    running what that session was minted with. Stamping the fresh preference would book
    this turn's spend against the wrong model, and hand a takeover command that flips a
    live session's model on an operator.
    """
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
    fill(_ctx(store, hub, provider, h1))

    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=retro_env)]
    h2 = FakeHarness(handle=WorkerHandle(session_id="unused", pid=200, process_start_time="t2"), verdict="pass")
    # A fresh resolution at `retrospective` would produce the runner default…
    h2.resolved_model = "opus"
    ctx2 = _ctx(store, hub, provider, h2, minutes=1)
    advance(ctx2)
    pull(ctx2)

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
    fill(_ctx(store, hub, provider, h1))
    # Simulate a pre-#144 lease: blank the stamps the mint just wrote.
    _blank_stamps(store, "ch_1")

    hub.envelopes["ch_1"] = build_env
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=resume_env)]
    h2 = FakeHarness(handle=WorkerHandle(session_id="unused", pid=200, process_start_time="t2"), verdict="pass")
    ctx2 = _ctx(store, hub, provider, h2, minutes=1)
    advance(ctx2)
    pull(ctx2)

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
