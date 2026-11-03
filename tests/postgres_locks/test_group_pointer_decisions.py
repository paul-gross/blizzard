"""Group and pointer admission decide against the same committed fold."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event, Lock
from time import monotonic
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.ingest import IngestConflict, IngestService
from blizzard.hub.domain.chunk.model import Chunk, WorkItemAuthor, WorkRef
from blizzard.hub.domain.chunk.ports.dependencies import FoldTarget
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.operations.queue import GroupService
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store, work_item_store
from blizzard.hub.store.internal.chunk_dependencies_store import (
    DEPENDENCY_GRAPH_LOCK_KEY,
    DEPENDENCY_GRAPH_LOCK_NAMESPACE,
)
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.tenancy import TenantId
from tests.support import make_graph, scoped_connections, seed_chunk_record

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)
_PROJECT = ProjectId("project")
_POINTER = WorkRef("hub", "1")


class _Labels:
    def source_label(self, source: str) -> str:
        return source

    def label(self, pointer: WorkRef) -> str:
        return f"{pointer.source}:{pointer.ref}"


class _Rollback(Exception):
    pass


@pytest.mark.parametrize("omit", [False, True], ids=["locked", "negative-control"])
def test_group_waits_for_pointer_decision_key(database_url: str, omit: bool) -> None:
    """A group must acquire the graph key before reading or moving pointer holders."""
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "pointer_key_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "group_key_waiter", "options": options})
    observer = sa.create_engine(database_url, isolation_level="AUTOCOMMIT")
    scope = scoped_connections(waiter, TenantId("ten_group_key"))
    clock = FixedClock(_AT)
    stores = build_chunk_stores(scope, clock, registry=RunnerRegistryStore(scope))
    try:
        with scope.founding("found", name="group key", created_at=_AT, created_by="test"):
            pass
        with scope.write("seed") as conn:
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.projects).values(
                    project_id=_PROJECT, slug="p", name="p", description="", revision=1, color="blue", created_at=_AT
                )
            )
        for chunk_id, refs in [("survivor", []), ("target", [_POINTER])]:
            seed_chunk_record(
                stores, Chunk(chunk_id=chunk_id, graph_id="graph", project_id=_PROJECT, work_refs=refs, minted_at=_AT)
            )
        # Materialize the key before the independent holder locks its row.
        with scope.write("key") as conn:
            lock_keys(conn, DEPENDENCY_GRAPH_LOCK_NAMESPACE, [DEPENDENCY_GRAPH_LOCK_KEY])
        group = GroupService(
            work_refs=stores.work_refs,
            dependencies=stores.dependencies,
            exclusive=stores.exclusive,
            clock=clock,
            cycle_lock=Lock(),
        )

        def selective_lock(conn: sa.Connection, name: str, keys: list[str]) -> None:
            if not (omit and name == DEPENDENCY_GRAPH_LOCK_NAMESPACE):
                lock_keys(conn, name, keys)

        with holder.begin() as conn:
            result = conn.execute(
                sa.text(
                    "UPDATE keyed_locks SET key = key WHERE tenant_id = :tenant AND namespace = :namespace AND key = :key"
                ),
                {
                    "tenant": "ten_group_key",
                    "namespace": DEPENDENCY_GRAPH_LOCK_NAMESPACE,
                    "key": DEPENDENCY_GRAPH_LOCK_KEY,
                },
            )
            assert result.rowcount == 1
            with (
                patch.object(scope, "_lock_open_tenant"),
                patch.object(chunk_exclusive_store, "lock_keys", selective_lock),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                future = pool.submit(group.group, "survivor", ["target"])
                try:
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text("""SELECT EXISTS (
                                SELECT 1 FROM pg_stat_activity w JOIN pg_stat_activity h
                                ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name = 'group_key_waiter'
                                AND h.application_name = 'pointer_key_holder')""")
                            ).scalar_one()
                            if blocked or future.done():
                                break
                            Event().wait(0.02)
                        else:
                            pytest.fail("group neither blocked nor completed")
                    assert blocked is not omit
                    if omit:
                        future.result(timeout=5)
                finally:
                    conn.commit()
                future.result(timeout=10)
        assert stores.work_refs.find_live_holder(_POINTER) == "survivor"
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("operation", ["authored", "ingest"])
@pytest.mark.parametrize("commit", [True, False], ids=["group-commits", "group-rolls-back"])
@pytest.mark.parametrize("omit", [False, True], ids=["locked", "negative-control"])
def test_group_against_pointer_admission(database_url: str, operation: str, commit: bool, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    first = sa.create_engine(database_url, connect_args={"application_name": "group_first", "options": options})
    second = sa.create_engine(database_url, connect_args={"application_name": "pointer_second", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    group_scope = scoped_connections(first, TenantId("ten_group_pointer"))
    pointer_scope = scoped_connections(second, TenantId("ten_group_pointer"))
    clock = FixedClock(_AT)
    group_stores = build_chunk_stores(group_scope, clock, registry=RunnerRegistryStore(group_scope))
    pointer_stores = build_chunk_stores(pointer_scope, clock, registry=RunnerRegistryStore(pointer_scope))
    items = WorkItemStore(pointer_scope)
    entered, release = Event(), Event()
    try:
        with group_scope.founding("found", name="group pointer", created_at=_AT, created_by="test"):
            pass
        with group_scope.write("seed") as conn:
            conn.execute(
                group_scope.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=_AT
                )
            )
            conn.execute(
                group_scope.insert(s.projects).values(
                    project_id=_PROJECT, slug="p", name="p", description="", revision=1, color="blue", created_at=_AT
                )
            )
        seed_chunk_record(
            group_stores, Chunk(chunk_id="survivor", graph_id="graph", project_id=_PROJECT, work_refs=[], minted_at=_AT)
        )
        seed_chunk_record(
            group_stores,
            Chunk(chunk_id="target", graph_id="graph", project_id=_PROJECT, work_refs=[_POINTER], minted_at=_AT),
        )
        group = GroupService(
            work_refs=group_stores.work_refs,
            dependencies=group_stores.dependencies,
            exclusive=group_stores.exclusive,
            clock=clock,
            cycle_lock=Lock(),
        )
        ingest = IngestService(
            record=pointer_stores.record,
            exclusive=pointer_stores.exclusive,
            links=ProjectLinkStore(pointer_scope),
            items=items,
            labels=_Labels(),
            clock=clock,
        )
        original_fold = group_stores.dependencies.record_fold_locked

        def hold_fold(
            handle: ILockedChunkRead, targets: list[FoldTarget], *, grouped_into: str, by: str, at: datetime
        ) -> dict[str, int]:
            result = original_fold(handle, targets, grouped_into=grouped_into, by=by, at=at)
            entered.set()
            assert release.wait(10), "group timed out"
            if not commit:
                raise _Rollback
            return result

        def selective_lock(conn: sa.Connection, name: str, keys: list[str]) -> None:
            if not (omit and conn.engine is second and name == DEPENDENCY_GRAPH_LOCK_NAMESPACE):
                lock_keys(conn, name, keys)

        def admit() -> object:
            if operation == "ingest":
                return ingest.ingest([_POINTER], graph=make_graph("graph", "g"), project=_PROJECT)
            chunk = Chunk(
                chunk_id="authored", graph_id="graph", project_id=_PROJECT, work_refs=[_POINTER], minted_at=_AT
            )
            return items.create_with_chunk(
                pointer=_POINTER,
                title="item",
                body="",
                author=WorkItemAuthor.user("user"),
                stated_priority=None,
                at=_AT,
                chunk=chunk,
            )

        with (
            patch.object(group_scope, "_lock_open_tenant"),
            patch.object(pointer_scope, "_lock_open_tenant"),
            patch.object(group_stores.dependencies, "record_fold_locked", side_effect=hold_fold),
            patch.object(chunk_exclusive_store, "lock_keys", selective_lock),
            patch.object(work_item_store, "lock_keys", selective_lock),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            one = pool.submit(group.group, "survivor", ["target"])
            try:
                assert entered.wait(10), "group did not reach its uncommitted fold"
                two = pool.submit(admit)
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = watch.execute(
                            sa.text("""SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                            JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name = 'pointer_second' AND h.application_name = 'group_first')""")
                        ).scalar_one()
                        if blocked or two.done():
                            break
                        Event().wait(0.02)
                    else:
                        pytest.fail("pointer decision neither blocked nor completed")
                assert blocked is not omit
                if omit:
                    with pytest.raises(IngestConflict) as conflict:
                        two.result(timeout=5)
                    assert conflict.value.existing_chunk_id == "target"
            finally:
                release.set()
            if commit:
                one.result(timeout=10)
            else:
                with pytest.raises(_Rollback):
                    one.result(timeout=10)
            if not omit:
                with pytest.raises(IngestConflict) as conflict:
                    two.result(timeout=10)
                assert conflict.value.existing_chunk_id == ("survivor" if commit else "target")
        assert group_stores.work_refs.find_live_holder(_POINTER) == ("survivor" if commit else "target")
        assert items.get("hub", "1") is None
        assert pointer_stores.record.get("authored") is None
    finally:
        first.dispose()
        second.dispose()
        observer.dispose()
