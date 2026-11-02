"""Claim's graph and prerequisite locks, observed without tenant admission on the blocker."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store
from blizzard.hub.store.internal.chunk_dependencies_store import (
    DEPENDENCY_GRAPH_LOCK_KEY,
    DEPENDENCY_GRAPH_LOCK_NAMESPACE,
)
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("kind", ["graph", "prerequisite"])
@pytest.mark.parametrize("control", ["none", "unrelated", "omit"])
def test_claim_waits_before_guard(database_url: str, kind: str, control: str, monkeypatch: pytest.MonkeyPatch) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "claim_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "claim_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    tenant = TenantId("ten_claim_lock")
    scope = scoped_connections(waiter, tenant)
    guard = Event()
    try:
        with scope.founding("found", name="claim", created_at=_AT, created_by="test"):
            pass
        with scope.write("seed") as conn:
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.projects).values(
                    project_id="project", slug="p", name="p", description="", revision=1, color="blue", created_at=_AT
                )
            )
            for chunk_id in ("dependent", "prerequisite", "other"):
                conn.execute(
                    scope.insert(s.chunks).values(
                        chunk_id=chunk_id, graph_id="graph", project_id="project", minted_at=_AT
                    )
                )
        stores = build_chunk_stores(scope, FixedClock(_AT), registry=RunnerRegistryStore(scope))
        with stores.exclusive.locked_dependency_graph(["dependent", "prerequisite"]) as handle:
            stores.dependencies.declare_locked(handle, "dependent", "prerequisite", by="test", at=_AT)
        # Materialize the unrelated key before the blocker takes it.
        with scope.write("other_key") as conn:
            chunk_exclusive_store.lock_keys(conn, DEPENDENCY_GRAPH_LOCK_NAMESPACE, ["other"])

        original_row = chunk_exclusive_store.lock_chunk_row
        if control == "omit" and kind == "graph":
            monkeypatch.setattr(chunk_exclusive_store, "lock_keys", lambda conn, namespace, keys: None)
        if control == "omit" and kind == "prerequisite":
            monkeypatch.setattr(
                chunk_exclusive_store,
                "lock_chunk_row",
                lambda conn, chunk: None if chunk == "prerequisite" else original_row(conn, chunk),
            )

        def claim() -> None:
            with stores.exclusive.locked_claim("dependent") as handle:
                guard.set()
                assert len(handle.standing_edges_touching("dependent")) == 1

        with ThreadPoolExecutor(max_workers=1) as pool:
            with holder.begin() as conn:
                if kind == "graph":
                    key = "other" if control == "unrelated" else DEPENDENCY_GRAPH_LOCK_KEY
                    result = conn.execute(
                        sa.text(
                            "UPDATE keyed_locks SET key=key WHERE tenant_id=:tenant AND namespace=:namespace AND key=:key"
                        ),
                        {"tenant": str(tenant), "namespace": DEPENDENCY_GRAPH_LOCK_NAMESPACE, "key": key},
                    )
                else:
                    chunk = "other" if control == "unrelated" else "prerequisite"
                    result = conn.execute(
                        sa.text("UPDATE chunks SET chunk_id=chunk_id WHERE tenant_id=:tenant AND chunk_id=:chunk"),
                        {"tenant": str(tenant), "chunk": chunk},
                    )
                assert result.rowcount == 1
                future = pool.submit(claim)
                try:
                    if control == "none":
                        deadline = monotonic() + 5
                        with observer.connect() as watch:
                            while monotonic() < deadline:
                                blocked = watch.execute(
                                    sa.text(
                                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity w JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid)) WHERE w.application_name='claim_waiter' AND h.application_name='claim_holder')"
                                    )
                                ).scalar_one()
                                if blocked:
                                    break
                                assert not future.done(), "claim passed the blocker"
                                Event().wait(0.02)
                            else:
                                pytest.fail("claim did not block")
                        assert not guard.is_set()
                    else:
                        future.result(timeout=5)
                        assert guard.is_set()
                finally:
                    conn.commit()
            future.result(timeout=10)
        assert guard.is_set()
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
