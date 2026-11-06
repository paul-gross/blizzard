"""Orphaning a killed worker's session: every runner path that kills a lease's worker, or closes one gone without a
result, hands the lease's own session to its harness owner — preempt, abandon, reap, and restart-resume's kill — while
a forced takeover and a lease an open takeover holds never do, and an owner that fails never fails the closure."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from structlog.testing import capture_logs

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import SessionMode
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.leases.activity import HEARTBEAT_STALENESS_THRESHOLD
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.orphan import orphan_session
from blizzard.runner.lifecycle.takeover import TakeoverOpenScope, TakeoverService
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.steps import Advance, Pull, Reap, Resume, ResumeIntents
from blizzard.runner.node_steps.chunk_state import ChunkState
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    make_store,
    make_stores,
    single_workspace,
)
from tests.support.runner_mint import new_mint

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-b", pid=200, process_start_time="start-200", pgid=200)
_SESSION = SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a")
_ALIVE = (100, "start-100")


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_mint(
        new_mint(
            NewLease(
                lease_id="lease_1",
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                session_name="main",
                created_at=_NOW,
            )
        )
    )
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", pgid=100, session=_SESSION, spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")
    return store


def _hub(*, epoch: int = 1, runner_id: str = "r1") -> FakeHub:
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkState(
        chunk_id="ch_1", status=ChunkStatus.RUNNING, latest_epoch=epoch, route_runner_id=runner_id
    )
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], epoch=epoch, session=SessionMode.FRESH
    )
    return hub


def _ctx(
    store: SqlAlchemyRunnerStore,
    hub: FakeHub,
    harness: FakeHarness,
    *,
    probe: FakeProbe | None = None,
    clock: FixedClock | None = None,
) -> LoopContext:
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=probe if probe is not None else FakeProbe(alive={_ALIVE}),
        clock=clock or FixedClock(_NOW),
    )


def _harness() -> FakeHarness:
    return FakeHarness(handle=_HANDLE, verdict="pass")


def _hold_by_takeover(store: SqlAlchemyRunnerStore, *, fence_epoch: int = 2) -> None:
    store.record_takeover(
        takeover_id="tk_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session=_SESSION,
        workdir="/ws/e1",
        fence_epoch=fence_epoch,
        opened_at=_NOW,
    )


def test_a_preempted_worker_is_killed_and_its_session_orphaned(tmp_path: Path) -> None:
    store, harness = _store(tmp_path), _harness()
    probe = FakeProbe(alive={_ALIVE})
    Pull(_ctx(store, _hub(epoch=2), harness, probe=probe)).run()
    assert probe.killed_groups == [100] and harness.orphaned == ["sess-a"]


def test_an_abandoned_worker_is_killed_and_its_session_orphaned(tmp_path: Path) -> None:
    store, harness = _store(tmp_path), _harness()
    probe = FakeProbe(alive={_ALIVE})
    Pull(_ctx(store, _hub(runner_id="other-runner"), harness, probe=probe)).run()
    assert probe.killed_groups == [100] and harness.orphaned == ["sess-a"]
    assert store.active_lease("lease_1") is None


def test_a_stalled_worker_reaped_is_killed_and_its_session_orphaned(tmp_path: Path) -> None:
    store, harness = _store(tmp_path), _harness()
    store.record_heartbeat(lease_id="lease_1", beat_at=_NOW)
    later = FixedClock(_NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(minutes=5))
    probe = FakeProbe(alive={_ALIVE})
    Reap(_ctx(store, _hub(), harness, probe=probe, clock=later)).run()
    assert probe.killed_groups == [100] and harness.orphaned == ["sess-a"]


def test_an_exit_without_a_result_orphans_the_session_and_is_still_judged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", judge_output_usable=False)
    Advance(_ctx(store, _hub(), harness, probe=FakeProbe())).run()
    assert harness.orphaned == ["sess-a"] and len(harness.judged) == 1


def test_an_exit_without_a_result_that_parks_on_an_ask_is_orphaned(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Which API?",
        options=["rest", "graphql"],
        session=_SESSION,
        asked_at=_NOW,
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass", judge_output_usable=False)
    Advance(_ctx(store, _hub(), harness, probe=FakeProbe())).run()
    assert harness.orphaned == ["sess-a"] and harness.judged == []


def test_an_exit_with_its_result_orphans_nothing(tmp_path: Path) -> None:
    store, harness = _store(tmp_path), _harness()
    Advance(_ctx(store, _hub(), harness, probe=FakeProbe())).run()
    assert harness.orphaned == [] and len(harness.judged) == 1


def test_a_lost_judge_is_orphaned_and_relaunched(tmp_path: Path) -> None:
    store = _store(tmp_path)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", judge_output="cut off mid-", judge_output_usable=False)
    ctx = _ctx(store, _hub(), harness, probe=FakeProbe())
    Advance(ctx).run()
    Advance(ctx).run()
    assert len(harness.judged) == 2 and harness.orphaned == ["sess-a", "sess-a"]


def test_restart_resume_orphans_the_killed_generations_session_and_resumes_it(tmp_path: Path) -> None:
    store, harness = _store(tmp_path), _harness()
    ResumeIntents(make_stores(store)).mark_graceful(now=_NOW)
    harness.resume_pid = 4321
    probe = FakeProbe(alive={_ALIVE, (4321, "start-4321")})
    Resume(_ctx(store, _hub(), harness, probe=probe)).run()
    assert probe.killed_groups == [100] and harness.orphaned == ["sess-a"]
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 4321


def test_a_lease_an_open_takeover_holds_is_neither_killed_nor_orphaned(tmp_path: Path) -> None:
    store, harness = _store(tmp_path), _harness()
    _hold_by_takeover(store)
    probe = FakeProbe(alive={_ALIVE})
    Pull(_ctx(store, _hub(epoch=2), harness, probe=probe)).run()
    assert probe.killed == [] and probe.killed_groups == [] and harness.orphaned == []


def test_a_forced_takeover_kills_the_worker_and_orphans_nothing(tmp_path: Path) -> None:
    store, harness = _store(tmp_path), _harness()
    probe = FakeProbe(alive={_ALIVE})
    service = TakeoverService(
        FixedClock(_NOW),
        probe,
        takeover=store,
        outbound=store,
        tokens=store,
        elicitations=store,
        local_api_url="http://127.0.0.1:8431",
        workspaces=single_workspace(FakeProvider({})),
        harnesses=HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness)}),
    )
    scope = TakeoverOpenScope(
        chunk_id="ch_1",
        open_takeover=None,
        bindings=store.bindings_for_chunk("ch_1"),
        active_lease=store.active_lease_for_chunk("ch_1"),
        latest_lease_with_session=store.latest_lease_with_session_for_chunk("ch_1"),
        latest_epoch=store.latest_epoch("ch_1"),
        active_parked=False,
        submission_pending=False,
    )
    service.open(scope, force=True)
    assert probe.killed_groups == [100] and harness.orphaned == []


def test_the_session_an_open_takeover_holds_is_never_handed_over(tmp_path: Path) -> None:
    store, harness = _store(tmp_path), _harness()
    lease = store.active_lease("lease_1")
    assert lease is not None
    _hold_by_takeover(store)
    orphan_session(_ctx(store, _hub(), harness), lease, via="test")
    assert harness.orphaned == []


def test_a_lease_minted_above_an_open_takeovers_reach_is_the_loops_to_orphan(tmp_path: Path) -> None:
    store, harness = _store(tmp_path), _harness()
    lease = store.active_lease("lease_1")
    assert lease is not None
    _hold_by_takeover(store, fence_epoch=2)
    reach = replace(lease, epoch=3)
    orphan_session(_ctx(store, _hub(), harness), reach, via="test")
    assert harness.orphaned == ["sess-a"]


def test_an_owner_whose_orphan_fails_is_logged_and_the_closure_still_lands(tmp_path: Path) -> None:
    store, harness = _store(tmp_path), _harness()
    harness.orphan_error = RuntimeError("service gone")
    with capture_logs() as logs:
        Pull(_ctx(store, _hub(runner_id="other-runner"), harness)).run()
    assert harness.orphaned == ["sess-a"] and store.active_lease("lease_1") is None
    assert any(entry["event"] == "orphaning a killed worker's session failed" for entry in logs)


def test_a_session_whose_owner_is_unknown_is_left_to_reconcile(tmp_path: Path) -> None:
    store, harness = _store(tmp_path), _harness()
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        pgid=100,
        session=SessionReference("no-such-harness", "sess-z"),
        spawned_at=_NOW,
    )
    lease = store.active_lease("lease_1")
    assert lease is not None
    with capture_logs() as logs:
        orphan_session(_ctx(store, _hub(), harness), lease, via="test")
    assert harness.orphaned == []
    assert any(entry["event"] == "orphaned session's owner unavailable — left to reconcile" for entry in logs)
