"""Pinning tests for runner-loop decisions that were defended only by prose.

Each test here fences one decision a long comment used to argue for: the comment now
names the test, and reverting the decision fails the named assertion below.
Driven against a real (tmp sqlite) runner store with the seam fakes, per the tier rules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from tests.runner_fakes import (
    SqlAlchemyRunnerStore,
    make_store,
)

_NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=UTC)


_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)


def _store(tmp_path: Path):  # pyright: ignore[reportUnusedFunction]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_running_lease(  # pyright: ignore[reportUnusedFunction]
    store: SqlAlchemyRunnerStore,
    *,
    lease: str = "lease_1",
    chunk: str = "ch_1",
    node_id: str = "nd_build",
    epoch: int = 1,
) -> None:
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id=node_id,
            node_name="build",
            epoch=epoch,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease,
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
