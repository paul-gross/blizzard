"""Runner restart-resume — the graceful-restart re-attach, component tier.

Resuming in place keeps the lease epoch and session and rewrites the pid, against a real tmp store with fakes at the
seams."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.steps import Resume, ResumeIntents
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_store,
    make_stores,
)
from tests.support.runner_resume_world import running_chunk

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)


def _store(tmp_path: Path):
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_running_lease(
    store: SqlAlchemyRunnerStore,
    *,
    chunk: str = "ch_1",
    lease: str = "lease_1",
    pid: int = 100,
    start: str = "start-100",
    session: str = "sess-a",
    epoch: int = 1,
) -> None:
    """A build lease spawned into env e1 with a live worker, plus its binding."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease,
        pid=pid,
        process_start_time=start,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, session),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")


# --- RESUME — resume in place ---


def test_resume_in_place_keeps_lease_epoch_session_rewrites_pid(tmp_path: Path):
    store = _store(tmp_path)
    _seed_running_lease(store)
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)
    assert store.resume_intent_lease_ids() == {"lease_1"}

    hub = FakeHub()
    hub.chunks["ch_1"] = running_chunk()
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.resume_pid = 4321
    probe = FakeProbe(alive={(100, "start-100"), (4321, "start-4321")})
    ctx = make_context(store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)

    Resume(ctx).run()

    # Same session resumed in place with the restart message; the survivor pid was killed first.
    assert harness.resumed == [
        ("/ws/e1", "sess-a", "# The supervisor restarted; continue your task where you left off.")
    ]
    assert probe.killed == [100]
    # Same lease/epoch/session, only the pid rewritten to the resumed process.
    lease = store.active_lease("lease_1")
    assert lease is not None
    assert (lease.lease_id, lease.epoch, lease.session_id, lease.pid) == ("lease_1", 1, "sess-a", 4321)
    # The resumed process's own group is durable too — every launch, resume included,
    # gets a fresh session/group leader, so the resumed pid IS its own pgid.
    assert lease.pgid == 4321
    # No retry consumed — no new lease minted, no closure recorded.
    assert store.attempt_count("ch_1", "nd_build") == 1
    # Intent consumed — a second RESUME pass is a no-op.
    assert store.resume_intent_lease_ids() == set()
