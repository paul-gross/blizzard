"""Non-forced retirement against ordinary chunk writes on PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, RouteReleaseCause
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.runners.host import HostAddition, HostHoldsRoutes, HostLifecycleFact
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerHoldsRoutes
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_lifecycle_store import ChunkLifecycleStore
from blizzard.hub.store.internal.chunk_route_store import ChunkRouteStore
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("target", ["host", "runner"])
@pytest.mark.parametrize("operation", ["release", "complete", "terminal-status"])
def test_retirement_reads_committed_holdings_without_waiting_for_chunk(database_url, target, operation):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "holdings_chunk", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "holdings_retire", "options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_retirement_holdings")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="holdings", created_at=now, created_by="test"):
            pass
        hosts = HostRegistryStore(scoped)
        runners = RunnerRegistryStore(scoped)
        hosts.add(HostAddition("host", "host", "host-hash", now, "test"))
        runners.add(RunnerAddition("runner", "runner", "runner-hash", now, "test", host_id="host"))
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
        with scoped.write("claim") as conn:
            lock_chunk_row(conn, "chunk")
            routes._record_route_conn(conn, Route("chunk", "runner", "w", [], now), token_hash="token", at=now)

        waiting = scoped_connections(waiter, tenant)

        def retire():
            if target == "host":
                return HostRegistryStore(waiting).record_retirement(
                    "host", fact=HostLifecycleFact("host", True, now, "test"), at=now, by="test", force=False
                )
            return RunnerRegistryStore(waiting).record_retirement("runner", at=now, by="test", force=False)

        # read() avoids the tenant admission lock: the real chunk lock is the only blocker.
        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("pending_chunk_write") as conn:
                lock_chunk_row(conn, "chunk")
                handle = cast(ILockedChunkRead, SimpleNamespace(conn=conn))
                if operation == "release":
                    routes.record_route_released_locked(handle, "chunk", at=now, cause=RouteReleaseCause.DETACH)
                elif operation == "complete":
                    ChunkLifecycleStore(scoped).record_completion_locked(
                        handle,
                        "chunk",
                        by="test",
                        at=now,
                        close_decision=CloseEnqueueDecision(frozenset(), frozenset()),
                    )
                else:
                    # Terminal transitions can leave a route behind; isolate the status decision.
                    conn.execute(
                        scoped.insert(s.chunk_completed).values(chunk_id="chunk", completed_at=now, completed_by="test")
                    )
                future = actors.submit(retire)
                with pytest.raises(HostHoldsRoutes if target == "host" else RunnerHoldsRoutes):
                    future.result(timeout=5)
                assert routes.route_of("chunk") is not None
                host = hosts.get_host("host")
                runner = runners.get_runner("runner")
                assert host is not None and not host.retired and host.token_hash == "host-hash"
                assert runner is not None and not runner.retired and runner.token_hash == "runner-hash"
                conn.commit()
            # Once the write commits, release or terminal status makes retirement permissible.
            assert retire() is not None
        assert (routes.route_of("chunk") is not None) == (operation == "terminal-status")
        if target == "host":
            host = hosts.get_host("host")
            assert host is not None and host.retired and host.token_hash is None
        else:
            runner = runners.get_runner("runner")
            assert runner is not None and runner.retired and runner.token_hash is None
    finally:
        holder.dispose()
        waiter.dispose()
