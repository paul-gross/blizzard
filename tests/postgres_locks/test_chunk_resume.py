"""Resume serializes on its chunk, not an unrelated chunk."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.operations.pause import PauseService
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("held_chunk", ["chunk", "other"], ids=["blocker", "negative-control"])
def test_resume_chunk_lock_and_timestamp(database_url: str, held_chunk: str) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "resume_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "resume_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = FixedClock(now)
    tenant = TenantId("ten_resume")
    scope = scoped_connections(holder, tenant)
    try:
        with scope.founding("found", name="resume", created_at=now, created_by="test"):
            pass
        with scope.write("seed") as conn:
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=now
                )
            )
            conn.execute(
                scope.insert(s.projects).values(
                    project_id="project", slug="p", name="p", description="", revision=1, color="blue", created_at=now
                )
            )
            for chunk_id in ("chunk", "other"):
                conn.execute(
                    scope.insert(s.chunks).values(
                        chunk_id=chunk_id, graph_id="graph", project_id="project", minted_at=now
                    )
                )
        waiter_scope = scoped_connections(waiter, tenant)
        stores = build_chunk_stores(waiter_scope, clock, registry=RunnerRegistryStore(waiter_scope))
        service = PauseService(lifecycle=stores.lifecycle, exclusive=stores.exclusive, clock=clock)
        chunk = Chunk(chunk_id="chunk", graph_id="graph", work_refs=[], minted_at=now)
        with ThreadPoolExecutor(max_workers=1) as actors:
            with holder.begin() as conn:
                conn.execute(
                    sa.text("UPDATE chunks SET chunk_id = chunk_id WHERE tenant_id = :tenant AND chunk_id = :chunk"),
                    {"tenant": str(tenant), "chunk": held_chunk},
                )
                future = actors.submit(service.resume, chunk, by="operator")
                if held_chunk == "chunk":
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name = 'resume_waiter' AND h.application_name = 'resume_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "resume bypassed the chunk lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("PostgreSQL did not report the expected wait")
                    clock.advance(timedelta(seconds=1))
                else:
                    assert future.result(timeout=5) > 0
            fact_id = future.result(timeout=10)
        with scope.read("check") as conn:
            row = conn.execute(scope.select(s.chunk_pause_facts).where(s.chunk_pause_facts.c.id == fact_id)).one()
        assert row.paused is False
        assert row.set_at == clock.now()
        assert row.set_by == "operator"
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
