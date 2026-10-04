"""Closed leases written through the runner store's own writers, for the lease trace store and sweep suites."""

from __future__ import annotations

from datetime import datetime

from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases import NewLease
from tests.runner_fakes import SqlAlchemyRunnerStore

NODE_ID = "g1-build"


def closed_lease(
    store: SqlAlchemyRunnerStore, lease_id: str, *, opened: datetime, closed: datetime, identified: bool = True
) -> None:
    """A lease minted at ``opened`` and closed at ``closed``; one never ``identified`` assembles no spans."""
    chunk_id = f"ch-{lease_id}"
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="g1",
            node_id=NODE_ID,
            node_name="build",
            epoch=1,
            runner_id="r-1",
            retries_max=2,
            created_at=opened,
        )
    )
    if identified:
        store.record_spawn(
            lease_id,
            pid=101,
            process_start_time="t",
            spawned_at=opened,
            session=SessionReference(harness_id="claude-code", session_id=f"{lease_id}-sess"),
        )
    close(store, lease_id, closed)


def close(store: SqlAlchemyRunnerStore, lease_id: str, at: datetime) -> None:
    store.record_closure(
        lease_id=lease_id, chunk_id=f"ch-{lease_id}", node_id=NODE_ID, reason="transitioned", closed_at=at
    )
