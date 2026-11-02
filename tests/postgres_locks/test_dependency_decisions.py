"""The graph decision key excludes writers without holding tenant admission."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import SystemClock
from blizzard.hub.domain.chunk.model import WorkRef, mint_chunk
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store.internal import chunk_dependencies_store, chunk_exclusive_store, work_item_store
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 27, tzinfo=UTC)


@pytest.mark.parametrize("operation", ["graph-transaction", "release", "work-ref", "authored", "withdrawal"])
@pytest.mark.parametrize("omit", [False, True], ids=["lock", "negative-control"])
def test_graph_decision_key(database_url: str, operation: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    engine = sa.create_engine(database_url, connect_args={"application_name": "dependency_waiter", "options": options})
    holder = sa.create_engine(database_url, connect_args={"application_name": "dependency_holder", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    scoped = scoped_connections(engine, TenantId("ten_dependency_lock"))
    dependencies = chunk_dependencies_store.ChunkDependenciesStore(scoped, SystemClock())
    exclusive = chunk_exclusive_store.ChunkExclusiveWrites(
        scoped,
        facts=cast(Any, None),
        record=cast(Any, None),
        dependencies=dependencies,
        registry=cast(Any, None),
        work_refs=cast(
            Any, Mock(live_holders_conn=Mock(return_value={}), holder_candidates_conn=Mock(return_value=set()))
        ),
    )
    namespace = chunk_dependencies_store.DEPENDENCY_GRAPH_LOCK_NAMESPACE
    key = chunk_dependencies_store.DEPENDENCY_GRAPH_LOCK_KEY
    try:
        with scoped.founding("found", name="dependency-test", created_at=_AT, created_by="test"):
            pass
        with scoped.write("seed") as conn:
            lock_keys(conn, namespace, [key])

        def actor() -> None:
            if operation == "release":
                assert dependencies.release("missing", "missing", by="test", at=_AT) is None
            elif operation == "work-ref":
                with exclusive.locked_work_refs([]):
                    pass
            elif operation == "authored":
                chunk = mint_chunk([WorkRef("hub", "1")], graph_id="graph", at=_AT, project_id=ProjectId("project"))
                with scoped.write("authored") as conn:
                    work_item_store.WorkItemStore(scoped)._admit_chunk(conn, chunk, _AT)
            elif operation == "withdrawal":
                with exclusive.locked_withdrawal(WorkRef("hub", "1")):
                    pass
            else:
                with exclusive.locked_dependency_graph([]):
                    pass

        def selective_lock(conn: sa.Connection, name: str, keys: list[str]) -> None:
            if not (omit and name == namespace):
                lock_keys(conn, name, keys)

        with holder.begin() as conn:
            result = conn.execute(
                sa.text(
                    "UPDATE keyed_locks SET key = key WHERE tenant_id = :tenant AND namespace = :namespace AND key = :key"
                ),
                {"tenant": "ten_dependency_lock", "namespace": namespace, "key": key},
            )
            assert result.rowcount == 1
            with (
                patch.object(chunk_dependencies_store, "lock_keys", selective_lock),
                patch.object(chunk_exclusive_store, "lock_keys", selective_lock),
                patch.object(work_item_store, "lock_keys", selective_lock),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                future = pool.submit(actor)
                try:
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiter
                                JOIN pg_stat_activity AS holder ON holder.pid = ANY(pg_blocking_pids(waiter.pid))
                                WHERE waiter.application_name = 'dependency_waiter'
                                AND holder.application_name = 'dependency_holder')
                            """)
                            ).scalar_one()
                            if blocked or future.done():
                                break
                            Event().wait(0.02)
                        else:
                            pytest.fail("actor neither completed nor blocked")
                    assert blocked is not omit
                finally:
                    conn.commit()
                future.result(timeout=10)
    finally:
        engine.dispose()
        holder.dispose()
        observer.dispose()
