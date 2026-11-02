"""Real PostgreSQL interleavings of a route write and retirement."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.runners.host import HostAddition, HostHoldsRoutes, HostLifecycleFact, HostRetired
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerHoldsRoutes, RunnerRetired
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_route_store, host_registry_store, runner_registry_store
from blizzard.hub.store.internal.chunk_route_store import ChunkRouteStore
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore, lock_host_registry
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("winner", ["claim-host", "claim-runner", "host-retire", "runner-retire"])
def test_route_and_retirement_serialize(database_url: str, winner: str) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "phase2_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "phase2_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_phase2")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="phase2", created_at=now, created_by="test"):
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
        route = Route(chunk_id="chunk", runner_id="runner", workspace_id="w", environment_ids=[], created_at=now)
        holder_routes = ChunkRouteStore(scoped, FixedClock(now))
        waiter_scoped = scoped_connections(waiter, tenant)
        waiter_routes = ChunkRouteStore(waiter_scoped, FixedClock(now))

        def act() -> None:
            if winner.startswith("claim-"):
                with pytest.raises(HostHoldsRoutes if winner == "claim-host" else RunnerHoldsRoutes):
                    if winner == "claim-host":
                        HostRegistryStore(waiter_scoped).record_retirement(
                            "host", fact=HostLifecycleFact("host", True, now, "test"), at=now, by="test", force=False
                        )
                    else:
                        RunnerRegistryStore(waiter_scoped).record_retirement("runner", at=now, by="test", force=False)
            else:
                with (
                    pytest.raises(HostRetired if winner == "host-retire" else RunnerRetired),
                    waiter_scoped.write("claim") as conn,
                ):
                    lock_chunk_row(conn, "chunk")
                    waiter_routes._record_route_conn(conn, route, token_hash="token", at=now)

        with ThreadPoolExecutor(max_workers=1) as actors:
            # Avoid tenant admission: the observed wait must be on the decision lock.
            with scoped.read("winner") as conn:
                if winner.startswith("claim-"):
                    lock_chunk_row(conn, "chunk")
                    holder_routes._record_route_conn(conn, route, token_hash="token", at=now)
                else:
                    lock_host_registry(conn)
                    table = s.host_lifecycle_facts if winner == "host-retire" else s.runner_lifecycle_facts
                    key = {"host_id": "host"} if winner == "host-retire" else {"runner_id": "runner"}
                    conn.execute(scoped.insert(table).values(**key, retired=True, set_at=now, set_by="test"))
                future = actors.submit(act)
                deadline = monotonic() + 5
                with observer.connect() as monitor:
                    while monotonic() < deadline:
                        blocked = monitor.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                            JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name = 'phase2_waiter' AND h.application_name = 'phase2_holder')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not future.done(), "operation did not wait for the competing transaction"
                        Event().wait(0.02)
                    else:
                        pytest.fail("PostgreSQL did not report the expected wait")
                conn.commit()
            future.result(timeout=10)
        assert (holder_routes.route_of("chunk") is not None) == winner.startswith("claim-")
        host = hosts.get_host("host")
        runner = runners.get_runner("runner")
        assert host is not None and host.retired == (winner == "host-retire")
        assert runner is not None and runner.retired == (winner == "runner-retire")
        if winner.startswith("claim-"):
            assert host.token_hash == "host-hash" and runner.token_hash == "runner-hash"
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("target,writer", [("host", "host"), ("host", "runner"), ("runner", "runner")])
def test_retirement_blocks_stale_usage_write(database_url: str, target: str, writer: str) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "usage_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "usage_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_usage_fence")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="usage", created_at=now, created_by="test"):
            pass
        HostRegistryStore(scoped).add(HostAddition("host", "host", "host-hash", now, "test"))
        RunnerRegistryStore(scoped).add(RunnerAddition("runner", "runner", "runner-hash", now, "test", host_id="host"))
        waiting_runners = RunnerRegistryStore(scoped_connections(waiter, tenant))
        waiting_hosts = HostRegistryStore(scoped_connections(waiter, tenant))

        def write_usage() -> None:
            with pytest.raises(HostRetired if target == "host" else RunnerRetired):
                if writer == "host":
                    waiting_hosts.record_external_usage(
                        "host", slug="s", name="S", sampled_at=now, windows_json="[]", at=now
                    )
                else:
                    waiting_runners.record_external_usage(
                        "runner", slug="s", name="S", sampled_at=now, windows_json="[]", at=now
                    )

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("retire") as conn:
                lock_host_registry(conn)
                table = s.host_lifecycle_facts if target == "host" else s.runner_lifecycle_facts
                key = {"host_id": "host"} if target == "host" else {"runner_id": "runner"}
                conn.execute(scoped.insert(table).values(**key, retired=True, set_at=now, set_by="test"))
                future = actors.submit(write_usage)
                deadline = monotonic() + 5
                with observer.connect() as monitor:
                    while monotonic() < deadline:
                        blocked = monitor.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                            JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name = 'usage_waiter' AND h.application_name = 'usage_holder')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not future.done(), "usage write did not wait for retirement"
                        Event().wait(0.02)
                    else:
                        pytest.fail("PostgreSQL did not report the expected wait")
                conn.commit()
            future.result(timeout=10)
        with scoped.read("verify") as conn:
            table = s.host_external_usage if writer == "host" else s.runner_external_usage
            assert conn.execute(scoped.select(table)).first() is None
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("operation", ["host-retire", "runner-retire", "claim", "usage"])
def test_registry_lock_omission_control(database_url: str, operation: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """With only the registry key held, omitting the actor's lock lets it finish before release."""
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"options": options})
    waiter = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_retirement_control")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="control", created_at=now, created_by="test"):
            pass
        HostRegistryStore(scoped).add(HostAddition("host", "host", "host-hash", now, "test"))
        RunnerRegistryStore(scoped).add(RunnerAddition("runner", "runner", "runner-hash", now, "test", host_id="host"))
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
        waiting = scoped_connections(waiter, tenant)
        module = {
            "host-retire": host_registry_store,
            "runner-retire": runner_registry_store,
            "claim": chunk_route_store,
            "usage": runner_registry_store,
        }[operation]
        monkeypatch.setattr(module, "lock_host_registry", lambda conn: None)

        def act() -> None:
            if operation == "host-retire":
                HostRegistryStore(waiting).record_retirement(
                    "host", fact=HostLifecycleFact("host", True, now, "test"), at=now, by="test", force=False
                )
            elif operation == "runner-retire":
                RunnerRegistryStore(waiting).record_retirement("runner", at=now, by="test", force=False)
            elif operation == "usage":
                RunnerRegistryStore(waiting).record_external_usage(
                    "runner", slug="s", name="S", sampled_at=now, windows_json="[]", at=now
                )
            else:
                route = Route(
                    chunk_id="chunk", runner_id="runner", workspace_id="w", environment_ids=[], created_at=now
                )
                with waiting.write("claim") as conn:
                    lock_chunk_row(conn, "chunk")
                    ChunkRouteStore(waiting, FixedClock(now))._record_route_conn(
                        conn, route, token_hash="token", at=now
                    )

        with ThreadPoolExecutor(max_workers=1) as actors, scoped.read("hold_registry") as conn:
            lock_host_registry(conn)
            future = actors.submit(act)
            future.result(timeout=5)
            conn.commit()
        if operation == "host-retire":
            host = HostRegistryStore(scoped).get_host("host")
            assert host is not None and host.retired
        elif operation == "runner-retire":
            assert RunnerRegistryStore(scoped).is_retired("runner")
        elif operation == "claim":
            assert ChunkRouteStore(scoped, FixedClock(now)).route_of("chunk") is not None
        else:
            with scoped.read("verify") as conn:
                assert conn.execute(scoped.select(s.runner_external_usage)).first() is not None
    finally:
        holder.dispose()
        waiter.dispose()
