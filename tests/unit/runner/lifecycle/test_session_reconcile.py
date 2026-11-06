"""Reconciling sessions as the daemon starts: each harness owner is handed only the sessions the runner's own records
hold unfinished, with no live worker or judge, and no takeover — and an owner that fails never fails the start."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from structlog.testing import capture_logs

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.session_reconcile import SessionReconcile
from tests.support.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_store,
)

_NOW = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-x", pid=900, process_start_time="start-900", pgid=900)


def _lease(store: SqlAlchemyRunnerStore, n: int, *, spawned: bool = True) -> None:
    store.record_lease(
        NewLease(
            lease_id=f"lease_{n}",
            chunk_id=f"ch_{n}",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    if spawned:
        store.record_spawn(
            f"lease_{n}",
            pid=100 + n,
            process_start_time=f"start-{100 + n}",
            pgid=100 + n,
            session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{n}"),
            spawned_at=_NOW,
        )


def _reconcile(store: SqlAlchemyRunnerStore, harness: FakeHarness, probe: FakeProbe) -> SessionReconcile:
    ctx = make_context(store, hub=FakeHub(), provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=probe)
    return SessionReconcile(ctx.stores, ctx.harnesses, probe)


def test_only_unfinished_workerless_untaken_sessions_are_handed_to_their_owner(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    for n in range(1, 8):
        _lease(store, n, spawned=n != 6)
    # 1: a turn whose driver died with the daemon — reconciled.
    # 2: its worker survived the daemon.
    alive: set[tuple[int, str | None]] = {(102, "start-102")}
    # 3: its worker declared its session done.
    store.record_session_end(lease_id="lease_3", ended_at=_NOW)
    # 4: a person holds it.
    store.record_takeover(
        takeover_id="tk_4",
        chunk_id="ch_4",
        lease_id="lease_4",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-4"),
        workdir="/ws/e1",
        fence_epoch=2,
        opened_at=_NOW,
    )
    # 5: its judge survived the daemon.
    store.record_elicitation_launch("lease_5", 1, output_path=str(tmp_path / "judge"), at=_NOW)
    store.record_elicitation_started("lease_5", 1, pid=505, process_start_time="start-505", pgid=505)
    alive.add((505, "start-505"))
    # 6: never spawned, so it holds no session. 7: closed — its generation is finished.
    store.record_closure(lease_id="lease_7", chunk_id="ch_7", node_id="nd_build", reason="done", closed_at=_NOW)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    harness.reconcile_stops = 1

    stopped = _reconcile(store, harness, FakeProbe(alive=alive)).run()

    assert harness.reconciled == [("sess-1",)] and stopped == 1


def test_a_judge_that_died_with_the_daemon_leaves_its_session_reconciled(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _lease(store, 1)
    store.record_elicitation_launch("lease_1", 1, output_path=str(tmp_path / "judge"), at=_NOW)
    store.record_elicitation_started("lease_1", 1, pid=505, process_start_time="start-505", pgid=505)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")

    _reconcile(store, harness, FakeProbe()).run()

    assert harness.reconciled == [("sess-1",)]


def test_an_owner_that_stops_nothing_reports_nothing_stopped(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _lease(store, 1)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    assert _reconcile(store, harness, FakeProbe()).run() == 0
    assert harness.reconciled == [("sess-1",)]


def test_nothing_to_reconcile_calls_no_owner(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    assert _reconcile(store, harness, FakeProbe()).run() == 0
    assert harness.reconciled == []


def test_an_owner_that_fails_is_logged_and_never_fails_the_start(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _lease(store, 1)

    class _Failing(FakeHarness):
        def reconcile(self, session_ids: object) -> int:
            raise RuntimeError("service gone")

    with capture_logs() as logs:
        stopped = _reconcile(store, _Failing(handle=_HANDLE, verdict="pass"), FakeProbe()).run()

    assert stopped == 0, "a failed owner stopped nothing"
    assert any(entry["event"] == "reconciling sessions failed" for entry in logs)
