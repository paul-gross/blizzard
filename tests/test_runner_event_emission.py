"""Runner operational-event emission — the failure funnel (issue #125, Phase 3).

Every surfaced attempt failure emits ONE ``event.recorded`` at ``Attempt.fail`` —
retry→``warning``, escalate→``critical``, abandon→``info``, paused-defer→nothing. Each
captured command failure emits a ``warning`` ``command-failed``; some catches RE-RAISE.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import HEARTBEAT_STALENESS_THRESHOLD, NewLease
from blizzard.runner.harness.adapter import HarnessSpawnError, WorkerHandle
from blizzard.runner.loop.internal.subprocess_worktree_git import WorktreeGitError
from blizzard.runner.loop.steps import Advance, Fill, Reap
from blizzard.wire.chunk import ChunkDetail, RouteView
from blizzard.wire.facts import ESCALATION_RECORDED, EVENT_RECORDED, LEASE_MINTED
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

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_ALIVE = (100, "start-100")
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_lease(store, *, retries_max: int, chunk="ch_1", lease="lease_1", epoch=1):  # type: ignore[no-untyped-def]
    """A build lease already spawned into env e1, with a configurable retry budget."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            runner_id="r1",
            retries_max=retries_max,
            created_at=_NOW,
        )
    )
    store.record_spawn(lease, pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _events(store):  # type: ignore[no-untyped-def]
    return [json.loads(b.payload) for b in store.pending_outbound() if b.kind == EVENT_RECORDED]


def _dead_worker_ctx(store, **kwargs):  # type: ignore[no-untyped-def]
    """A context whose worker is dead (empty alive set) and whose judgement is verdict-less
    — driving ADVANCE into ``Attempt.fail(via="advance")`` on its collect pass (the second
    ``Advance().run()`` after the launch/collect split, blizzard#443)."""
    hub = FakeHub()
    hub.envelopes = {"ch_1": make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])}
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),  # no parseable <Choice> -> failure
        probe=FakeProbe(),  # empty alive set -> worker dead
        **kwargs,
    )


# --- change K: the Attempt.fail funnel ------------------------------------- #


def test_retry_branch_emits_a_warning_attempt_failed(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store, retries_max=2)  # retried=0 < 2 -> retry
    ctx = _dead_worker_ctx(store)
    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default

    events = _events(store)
    assert len(events) == 1
    ev = events[0]
    assert (ev["severity"], ev["kind"]) == ("warning", "attempt-failed")
    assert ev["chunk_id"] == "ch_1"
    assert ev["node_name"] == "build"
    assert ev["detail"]["via"] == "advance"
    # The retry also mints a fresh lease — the event rode atomically alongside the closure.
    assert LEASE_MINTED in {b.kind for b in store.pending_outbound()}


def test_escalate_branch_emits_a_critical_worker_lost(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store, retries_max=0)  # retried=0, not < 0 -> escalate
    ctx = _dead_worker_ctx(store)
    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default

    events = _events(store)
    assert len(events) == 1
    assert (events[0]["severity"], events[0]["kind"]) == ("critical", "worker-lost")
    # ...alongside the escalation.recorded fact the escalate branch already buffered.
    assert ESCALATION_RECORDED in {b.kind for b in store.pending_outbound()}


def test_locally_paused_defer_emits_nothing(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_lease(store, retries_max=0)  # would escalate...
    store.record_local_pause(
        "r1", paused=True, at=_NOW, by="operator", report_kind="runner.locally_paused", report_payload="{}"
    )
    Advance(_dead_worker_ctx(store)).run()

    # ...but the locally-paused defer surfaces nothing (and no escalation either).
    assert _events(store) == []
    assert ESCALATION_RECORDED not in {b.kind for b in store.pending_outbound()}


def test_reap_stalled_but_alive_worker_emits_via_reap(tmp_path):  # type: ignore[no-untyped-def]
    """The other stall path (AC#7): a hung-but-live worker whose heartbeat went stale is
    reaped as a failure via REAP's `is_heartbeat_stale` branch — surfaced within one
    liveness window (the clock is one threshold + a margin past its last beat)."""
    store = _store(tmp_path)
    _seed_lease(store, retries_max=2)
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    later = _NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(minutes=5)
    hub = FakeHub()
    hub.envelopes = {"ch_1": make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])}
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(alive={_ALIVE}),  # pid still alive — hung, not dead
        clock=FixedClock(later),
    )

    Reap(ctx).run()

    events = _events(store)
    assert len(events) == 1
    assert (events[0]["severity"], events[0]["kind"]) == ("warning", "attempt-failed")
    assert events[0]["detail"]["via"] == "reap"


def test_reassign_abandon_branch_emits_an_info_attempt_abandoned(tmp_path):  # type: ignore[no-untyped-def]
    """The abandon branch (SF-6): retries exhausted AND the hub now routes the chunk
    elsewhere → `info` `attempt-abandoned`, emitted in `Attempt.fail` itself, not the
    shared `Attempt.abandon` (which RESUME/PULL detach also reach and stays silent)."""
    store = _store(tmp_path)
    _seed_lease(store, retries_max=0)  # exhausted -> the reassign check runs
    hub = FakeHub()
    hub.envelopes = {"ch_1": make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])}
    # The hub now routes ch_1 to a DIFFERENT runner — a reassignment, not a detach.
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.RUNNING,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id="r2", workspace_id="ws1", environment_ids=["e1"]),
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
    )

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default

    events = _events(store)
    assert len(events) == 1
    assert (events[0]["severity"], events[0]["kind"]) == ("info", "attempt-abandoned")
    # No escalation — an abandon is not a needs-human hand-off.
    assert ESCALATION_RECORDED not in {b.kind for b in store.pending_outbound()}


def test_at_most_once_a_second_tick_emits_no_duplicate(tmp_path):  # type: ignore[no-untyped-def]
    """At-most-once is structural: `Attempt.fail` runs once per attempt (it closes the
    lease), so a second ADVANCE over the now-closed lease emits nothing more (AC#7)."""
    store = _store(tmp_path)
    _seed_lease(store, retries_max=0)  # escalate — no fresh lease to re-fail
    ctx = _dead_worker_ctx(store)

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the attempt is closed/escalated here
    Advance(ctx).run()  # nothing left to fail — no in-flight elicitation, no exited worker

    assert len(_events(store)) == 1


# --- change L: per-adapter command failures --------------------------------- #


def test_env_prep_failure_emits_a_command_failed(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}, prepare_fail=True),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )
    Fill(ctx).run()

    events = _events(store)
    assert len(events) == 1
    assert (events[0]["severity"], events[0]["kind"]) == ("warning", "command-failed")
    assert events[0]["chunk_id"] == "ch_1"
    assert events[0]["lease_id"] is None  # no lease yet — the chunk was never claimed
    assert "checkout-base" in events[0]["detail"]["command"]  # the failing step
    assert "reset step failed" in events[0]["detail"]["stderr_tail"]


class _VerifyFailsWorktreeGit(FakeWorktreeGit):
    """A worktree git whose verify always raises — L(ii)'s catch site (issue #143, Phase 4)."""

    def verify(self, origin_url: str, branch: str, commit: str) -> bool:
        raise WorktreeGitError("git ls-remote origin feat/x failed: remote rejected (no SSH_AUTH_SOCK)")


def test_git_verify_failure_emits_a_command_failed_and_continues(tmp_path):  # type: ignore[no-untyped-def]
    """A verify failure is informational only (issue #143): a read-only re-derivation
    opens no unsafe window, so it is never re-raised — the declaration is simply
    dropped and ADVANCE proceeds into its ordinary verdict-less-exit failure path."""
    store = _store(tmp_path)
    _seed_lease(store, retries_max=2)
    store.record_git_commit_declaration(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        environment_id="e1",
        repo="toy-api",
        branch="feat/x",
        commit="abc",
        declared_at=_NOW,
    )
    wt = _VerifyFailsWorktreeGit()
    ctx = _dead_worker_ctx(store, worktree_git=wt)

    Advance(ctx).run()  # no exception — a read-only verify failure never crash-loops the tick
    Advance(ctx).run()  # collects the detached elicitation — the fake pid reads dead by default

    events = _events(store)
    assert [(e["severity"], e["kind"]) for e in events] == [
        ("warning", "command-failed"),
        ("warning", "attempt-failed"),
    ]
    assert events[0]["chunk_id"] == "ch_1"
    assert "SSH_AUTH_SOCK" in events[0]["detail"]["stderr_tail"]


class _SpawnFailsHarness(FakeHarness):
    """A harness whose spawn fails to launch — L(iii)'s catch site."""

    def spawn(
        self, envelope, preamble, session_hint, resume_from=None, *, model=None, effort=None, compaction_window=None
    ):  # type: ignore[no-untyped-def]
        raise HarnessSpawnError("failed to spawn claude in /ws/e1: [Errno 2] No such file or directory")


def test_spawn_launch_failure_emits_a_command_failed_and_reraises(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    hub = FakeHub()
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=_SpawnFailsHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
    )

    with pytest.raises(HarnessSpawnError):  # re-raised — no worker started, retries next tick
        Fill(ctx).run()

    events = _events(store)
    assert len(events) == 1
    assert (events[0]["severity"], events[0]["kind"]) == ("warning", "command-failed")
    assert "No such file" in events[0]["detail"]["stderr_tail"]
