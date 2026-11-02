"""Withdrawal races real ingest and group transactions without tenant admission masking their locks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event, Lock
from time import monotonic
from typing import cast
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.ingest import IngestService, IngestWithdrawnItem
from blizzard.hub.domain.chunk.model import Chunk, WorkItemAuthor
from blizzard.hub.domain.operations.delete import DeleteService
from blizzard.hub.domain.operations.queue import GroupService
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.work_items.editing import WithdrawnWorkItem, WorkItemEditService
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store
from blizzard.hub.store.internal.chunk_dependencies_store import DEPENDENCY_GRAPH_LOCK_NAMESPACE
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.tenancy import TenantId
from tests.postgres_locks.test_group_pointer_decisions import _Labels
from tests.support import make_graph, scoped_connections, seed_chunk_record, seed_work_item

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)
_PROJECT = ProjectId("project")


@pytest.mark.parametrize(
    ("competitor", "omit"), [("ingest", False), ("ingest", True), ("group", False), ("group", True)]
)
@pytest.mark.parametrize("first", ["withdraw", "competitor"])
def test_withdrawal_orders(database_url: str, competitor: str, first: str, omit: bool) -> None:
    if competitor == "group" and omit and first == "withdraw":
        pytest.skip("the row lock alone serializes this order; the other order exposes omission")
    options = "-c statement_timeout=8000"
    engines = [
        sa.create_engine(database_url, connect_args={"application_name": name, "options": options})
        for name in ("withdraw_first", "withdraw_second")
    ]
    observer = sa.create_engine(database_url, isolation_level="AUTOCOMMIT")
    scopes = [scoped_connections(engine, TenantId("ten_withdraw_race")) for engine in engines]
    clock = FixedClock(_AT)
    stores = [build_chunk_stores(scope, clock, registry=RunnerRegistryStore(scope)) for scope in scopes]
    items = [WorkItemStore(scope) for scope in scopes]
    entered, release = Event(), Event()
    locked_by_waiter: list[str] = []
    try:
        with scopes[0].founding("found", name="withdraw race", created_at=_AT, created_by="test"):
            pass
        with scopes[0].write("seed") as conn:
            conn.execute(
                scopes[0]
                .insert(s.graphs)
                .values(graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=_AT)
            )
            conn.execute(
                scopes[0]
                .insert(s.projects)
                .values(
                    project_id=_PROJECT, slug="p", name="p", description="", revision=1, color="blue", created_at=_AT
                )
            )
        item = seed_work_item(items[0], graph_id="graph", project_id=_PROJECT, author=WorkItemAuthor.user("u"), at=_AT)
        target = f"ch_{item.ref}"
        if competitor == "ingest":
            with stores[0].exclusive.locked([target]) as handle:
                stores[0].lifecycle.record_stop_locked(handle, target, by="test", at=_AT)
        else:
            seed_chunk_record(
                stores[0],
                Chunk(chunk_id="survivor", graph_id="graph", project_id=_PROJECT, work_refs=[], minted_at=_AT),
            )

        def editor(index: int) -> WorkItemEditService:
            store = stores[index]
            delete = DeleteService(items=items[index], clock=clock, exclusive=store.exclusive, cycle_lock=Lock())
            return WorkItemEditService(
                items=items[index],
                work_refs=store.work_refs,
                record=store.record,
                facts=store.facts,
                clock=clock,
                delete=delete,
                exclusive=store.exclusive,
            )

        def run_withdraw(index: int) -> object:
            return editor(index).withdraw(item, by="test")

        def run_competitor(index: int) -> object:
            store = stores[index]
            if competitor == "ingest":
                ingest = IngestService(
                    record=store.record,
                    exclusive=store.exclusive,
                    links=ProjectLinkStore(scopes[index]),
                    items=items[index],
                    labels=_Labels(),
                    clock=clock,
                )
                return ingest.ingest([item.pointer], graph=make_graph("graph", "g"), project=_PROJECT)
            return GroupService(
                work_refs=store.work_refs,
                dependencies=store.dependencies,
                exclusive=store.exclusive,
                clock=clock,
                cycle_lock=Lock(),
            ).group("survivor", [target])

        if first == "withdraw":
            obj = items[0]
            method = "close_locked" if competitor == "ingest" else "delete_chunk_and_withdraw_hub_items_locked"
        elif competitor == "ingest":
            obj, method = stores[0].record, "mint_locked"
        else:
            obj, method = stores[0].dependencies, "record_fold_locked"
        original = getattr(obj, method)

        def pause(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.set()
            assert release.wait(10)
            return result

        def selective_lock(conn: sa.Connection, namespace: str, keys: list[str]) -> None:
            if not (omit and conn.engine is engines[1] and namespace == DEPENDENCY_GRAPH_LOCK_NAMESPACE):
                lock_keys(conn, namespace, keys)

        row_lock = chunk_exclusive_store.lock_chunk_row

        def record_lock(conn: sa.Connection, chunk_id: str) -> None:
            if conn.engine is engines[1]:
                locked_by_waiter.append(chunk_id)
            row_lock(conn, chunk_id)

        with (
            patch.object(scopes[0], "_lock_open_tenant"),
            patch.object(scopes[1], "_lock_open_tenant"),
            patch.object(obj, method, side_effect=pause),
            patch.object(chunk_exclusive_store, "lock_keys", selective_lock),
            patch.object(chunk_exclusive_store, "lock_chunk_row", record_lock),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            winner = pool.submit(run_withdraw if first == "withdraw" else run_competitor, 0)
            try:
                assert entered.wait(10)
                loser = pool.submit(run_competitor if first == "withdraw" else run_withdraw, 1)
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        blocked = watch.execute(
                            sa.text("""SELECT EXISTS (
                            SELECT 1 FROM pg_stat_activity w JOIN pg_stat_activity h
                            ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name='withdraw_second' AND h.application_name='withdraw_first')""")
                        ).scalar_one()
                        if blocked or loser.done():
                            break
                        Event().wait(0.02)
                    else:
                        pytest.fail("competitor did not block or finish")
                assert blocked is (not omit or competitor == "group")
                if omit and competitor == "ingest":
                    loser.result(timeout=5)
            finally:
                release.set()
            result = winner.result(timeout=10)
            if omit and competitor == "ingest":
                assert loser.result(timeout=10) is not None
            elif first == "withdraw":
                with pytest.raises(IngestWithdrawnItem if competitor == "ingest" else ChunkNotFound):
                    loser.result(timeout=10)
            else:
                withdrawn = cast(WithdrawnWorkItem, loser.result(timeout=10))
                expected = result if competitor == "ingest" else "survivor"
                assert withdrawn.deleted_chunk_id == expected
                if omit:
                    assert target in locked_by_waiter and "survivor" not in locked_by_waiter
        current = items[0].get("hub", item.ref)
        assert current is not None and current.closure is WorkItemClosure.WITHDRAWN
        holder = stores[0].work_refs.find_live_holder(item.pointer)
        assert (holder is not None) is (omit and competitor == "ingest")
    finally:
        for engine in [*engines, observer]:
            engine.dispose()
