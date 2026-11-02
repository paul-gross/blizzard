"""A sweep fences against a concurrent change of retirement generation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from types import SimpleNamespace
from typing import cast

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.runners.registration import RunnerAddition
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_route_store
from blizzard.hub.store.internal.chunk_route_store import ChunkRouteStore
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row
from blizzard.hub.store.internal.host_registry_store import lock_host_registry
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("omit_sweep_lock", [False, True], ids=["fenced", "omission-control"])
def test_changed_generation_fences_retirement_sweep(
    database_url: str, omit_sweep_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "sweep_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "sweep_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    now = datetime.now(UTC)
    tenant = TenantId("ten_sweep")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="sweep", created_at=now, created_by="test"):
            pass
        runners = RunnerRegistryStore(scoped)
        runners.add(RunnerAddition("runner", "runner", "hash", now, "test"))
        with scoped.write("seed") as conn:
            conn.execute(
                scoped.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=now
                )
            )
            conn.execute(
                scoped.insert(s.projects).values(
                    project_id="project", slug="p", name="p", description="", revision=1, color="blue", created_at=now
                )
            )
            conn.execute(
                scoped.insert(s.chunks).values(chunk_id="chunk", graph_id="graph", project_id="project", minted_at=now)
            )
        routes = ChunkRouteStore(scoped, FixedClock(now))
        route = Route(chunk_id="chunk", runner_id="runner", workspace_id="w", environment_ids=[], created_at=now)
        with scoped.write("claim") as conn:
            lock_chunk_row(conn, "chunk")
            routes._record_route_conn(conn, route, token_hash="hash", at=now)
        old = routes.route_of("chunk")
        assert old is not None
        retirement = runners.record_retirement("runner", at=now, by="test", force=True)
        assert retirement is not None
        _, _, generation = retirement
        waiter_scope = scoped_connections(waiter, tenant)
        waiter_routes = ChunkRouteStore(waiter_scope, FixedClock(now))
        if omit_sweep_lock:
            monkeypatch.setattr(chunk_route_store, "lock_host_registry", lambda conn: None)

        def sweep() -> int | None:
            with waiter_scope.write("sweep") as conn:
                lock_chunk_row(conn, "chunk")
                handle = cast(ILockedChunkRead, SimpleNamespace(conn=conn))
                return waiter_routes.record_retirement_release_locked(handle, old, retirement_id=generation, at=now)

        with ThreadPoolExecutor(max_workers=1) as actors:
            # Do not hold tenant admission: only the registry key can block the sweep.
            with scoped.read("reinstate_and_retire") as conn:
                lock_host_registry(conn)
                conn.execute(
                    scoped.insert(s.runner_lifecycle_facts).values(
                        runner_id="runner", retired=False, set_at=now, set_by="test"
                    )
                )
                primary_key = conn.execute(
                    scoped.insert(s.runner_lifecycle_facts).values(
                        runner_id="runner", retired=True, set_at=now, set_by="test"
                    )
                ).inserted_primary_key
                assert primary_key is not None
                new_generation = primary_key[0]
                assert new_generation != generation
                future = actors.submit(sweep)
                if omit_sweep_lock:
                    assert future.result(timeout=5) is not None
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                            JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name = 'sweep_waiter' AND h.application_name = 'sweep_holder')
                        """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "sweep did not wait for the lifecycle decision"
                            Event().wait(0.02)
                        else:
                            pytest.fail("PostgreSQL did not report the expected wait")
                conn.commit()
            if not omit_sweep_lock:
                assert future.result(timeout=10) is None
        assert routes.route_of("chunk") == (None if omit_sweep_lock else old)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
