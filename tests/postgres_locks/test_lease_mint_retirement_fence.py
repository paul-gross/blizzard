"""Lease mint rechecks retirement after acquiring the host registry decision key."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.ports.fence import Claimant
from blizzard.hub.domain.runners.host import HostAddition, HostRetired
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerRetired
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_route_store
from blizzard.hub.store.internal.chunk_route_store import ChunkRouteStore
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("target", ["host", "runner"])
@pytest.mark.parametrize("omit_lock", [False, True], ids=["registry-lock", "omission-control"])
def test_lease_mint_waits_for_retirement_decision(database_url, target, omit_lock, monkeypatch):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "lease_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "lease_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_lease_retirement")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="lease", created_at=now, created_by="test"):
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
            conn.execute(
                scoped.insert(s.route_created).values(
                    route_id="route",
                    chunk_id="chunk",
                    runner_id="runner",
                    workspace_id="workspace",
                    created_at=now,
                    seq=1,
                )
            )
        route = ChunkRouteStore(scoped_connections(waiter, tenant), FixedClock(now))
        if omit_lock:
            monkeypatch.setattr(chunk_route_store, "lock_host_registry", lambda conn: None)

        with ThreadPoolExecutor(max_workers=1) as pool:
            # No tenant admission: the actual registry key is the only held decision lock.
            with scoped.read("retirement") as conn:
                lock_keys(conn, "host-live-name-registry", ["registry"])
                table = s.host_lifecycle_facts if target == "host" else s.runner_lifecycle_facts
                key = {"host_id": "host"} if target == "host" else {"runner_id": "runner"}
                conn.execute(scoped.insert(table).values(**key, retired=True, set_at=now, set_by="test"))
                future = pool.submit(
                    route.record_lease_minted, "chunk", epoch=1, claimant=Claimant("runner", "lease"), at=now
                )
                if omit_lock:
                    assert future.result(timeout=5) is None
                else:
                    deadline = monotonic() + 5
                    with observer.connect().execution_options(isolation_level="AUTOCOMMIT") as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                  WHERE w.application_name = 'lease_waiter'
                                    AND h.application_name = 'lease_holder'
                                    AND w.query ILIKE '%keyed_locks%')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "lease mint completed before retirement committed"
                            Event().wait(0.02)
                        else:
                            pytest.fail("lease mint did not wait on the registry key")
                conn.commit()
            if not omit_lock:
                with pytest.raises(HostRetired if target == "host" else RunnerRetired, match="fact ingest"):
                    future.result(timeout=10)
        with scoped.read("verify") as conn:
            for table in (s.lease_facts, s.epoch_owners):
                assert conn.execute(scoped.select(table, sa.func.count())).scalar_one() == (1 if omit_lock else 0)
        assert route.runner_high_water("runner") == 0
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
