"""Chunk usage and retirement contend on the registry decision lock."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.runners.host import HostAddition, HostRetired
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerRetired
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_usage_store
from blizzard.hub.store.internal.chunk_usage_store import ChunkUsageStore
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore, lock_host_registry
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("target", ["host", "runner"])
@pytest.mark.parametrize("omit_lock", [False, True])
def test_retirement_fences_chunk_usage(database_url, target, omit_lock, monkeypatch):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "chunk_usage_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "chunk_usage_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    try:
        scoped = scoped_connections(holder, TenantId("ten_chunk_usage_fence"))
        with scoped.founding("found", name="usage", created_at=now, created_by="test"):
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
        usage = ChunkUsageStore(scoped_connections(waiter, TenantId("ten_chunk_usage_fence")), FixedClock(now))
        if omit_lock:
            monkeypatch.setattr(chunk_usage_store, "lock_host_registry", lambda conn: None)

        def record():
            usage.record_usage(
                "chunk",
                node_id="node",
                epoch=1,
                runner_id="runner",
                kind="spawn",
                model="model",
                input_tokens=1,
                output_tokens=2,
                cache_read_tokens=0,
                cache_create_tokens=0,
                cost_usd=0.1,
                at=now,
            )

        with ThreadPoolExecutor(max_workers=1) as actors:
            # A read transaction avoids tenant admission, leaving only the registry key as the fence.
            with scoped.read("retirement") as conn:
                lock_host_registry(conn)
                table = s.host_lifecycle_facts if target == "host" else s.runner_lifecycle_facts
                key = {"host_id": "host"} if target == "host" else {"runner_id": "runner"}
                conn.execute(scoped.insert(table).values(**key, retired=True, set_at=now, set_by="test"))
                future = actors.submit(record)
                if omit_lock:
                    future.result(timeout=5)
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name = 'chunk_usage_waiter'
                                  AND h.application_name = 'chunk_usage_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "usage completed without waiting for retirement"
                            Event().wait(0.02)
                        else:
                            pytest.fail("PostgreSQL did not report the expected registry wait")
                conn.commit()
            if not omit_lock:
                with pytest.raises(HostRetired if target == "host" else RunnerRetired):
                    future.result(timeout=10)
        with scoped.read("verify") as conn:
            rows = conn.execute(scoped.select(s.usage_facts)).all()
        assert len(rows) == (1 if omit_lock else 0)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
