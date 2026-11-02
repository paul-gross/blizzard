"""Queue decisions wait for a non-queue writer of an unnamed candidate."""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.hub.domain.chunk.model import Chunk, WorkItemAuthor, WorkRef
from blizzard.hub.domain.operations.queue import QueueList, QueueService
from blizzard.hub.store.internal import chunk_exclusive_store, work_item_store
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.queue_lock import lock_queue_positions
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import chunk_stores, hub_store_connections, seed_chunk_record, seed_graph, seed_scope

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("omit", [False, True], ids=["roster-lock", "negative-control"])
def test_reorder_waits_for_pausing_unnamed_candidate(database_url: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(
        database_url, connect_args={"application_name": "queue_decision_holder", "options": options}
    )
    waiter = sa.create_engine(
        database_url, connect_args={"application_name": "queue_decision_waiter", "options": options}
    )
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        with waiter.begin() as conn:
            seed_graph(conn, "gr_queue", at=_AT)
        stores = chunk_stores(waiter, FixedClock(_AT))
        for cid in ("ch_a", "ch_b"):
            seed_chunk_record(stores, Chunk(chunk_id=cid, graph_id="gr_queue", work_refs=[], minted_at=_AT))
            stores.queue.record_promote(cid, at=_AT)
        a = stores.record.get("ch_a")
        assert a is not None
        statuses = stores.facts.load_live_statuses()
        service = QueueService(
            queue=stores.queue, record=stores.record, exclusive=stores.exclusive, clock=FixedClock(_AT)
        )
        original = chunk_exclusive_store.lock_chunk_row

        def lock_except_b(conn: sa.Connection, cid: str) -> None:
            if not omit or cid != "ch_b":
                original(conn, cid)

        # The holder models a row-locked pause *after* admission. It deliberately does
        # not hold the tenant row, so that admission cannot mask a missing candidate lock.
        with holder.connect() as conn, ThreadPoolExecutor(max_workers=1) as pool:
            tx = conn.begin()
            try:
                conn.execute(sa.text("UPDATE chunks SET graph_id=graph_id WHERE chunk_id='ch_b'"))
                conn.execute(
                    sa.text(
                        "INSERT INTO chunk_pause_facts (tenant_id, chunk_id, paused, set_at, set_by) "
                        "SELECT tenant_id, chunk_id, true, :at, 'test' FROM chunks WHERE chunk_id='ch_b'"
                    ),
                    {"at": _AT},
                )
                with patch.object(chunk_exclusive_store, "lock_chunk_row", lock_except_b):
                    future = pool.submit(service.reorder, QueueList.READY, [a], statuses=statuses)
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name='queue_decision_waiter'
                                AND h.application_name='queue_decision_holder')
                            """)
                            ).scalar_one()
                            if blocked or future.done():
                                break
                            Event().wait(0.02)
                        else:
                            pytest.fail("queue decision neither blocked nor completed")
                    assert blocked is not omit
                    if omit:
                        assert [c.chunk_id for c in future.result(timeout=5).ordered] == ["ch_a", "ch_b"]
                    tx.commit()
                    if not omit:
                        assert [c.chunk_id for c in future.result(timeout=5).ordered] == ["ch_a"]
            finally:
                if tx.is_active:
                    tx.rollback()
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("omit", [False, True], ids=["queue-key", "negative-control"])
def test_ingest_mint_waits_for_queue_decision(database_url: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "queue_mint_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "queue_mint_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        with waiter.begin() as conn:
            seed_graph(conn, "gr_queue", at=_AT)
        stores = chunk_stores(waiter, FixedClock(_AT))
        seed_chunk_record(stores, Chunk(chunk_id="ch_seed", graph_id="gr_queue", work_refs=[], minted_at=_AT))
        seeded = stores.record.get("ch_seed")
        assert seeded is not None and seeded.project_id is not None
        chunk = Chunk(chunk_id="ch_new", graph_id="gr_queue", work_refs=[], minted_at=_AT, project_id=seeded.project_id)

        def mint() -> None:
            with stores.exclusive.locked_work_refs([]) as handle:
                stores.record.mint_locked(handle, chunk)

        original = chunk_exclusive_store.lock_queue_positions
        with hub_store_connections(holder).read("hold queue key") as conn, ThreadPoolExecutor(max_workers=1) as pool:
            lock_queue_positions(conn)
            try:
                with patch.object(
                    chunk_exclusive_store, "lock_queue_positions", (lambda _: None) if omit else original
                ):
                    future = pool.submit(mint)
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name='queue_mint_waiter'
                                AND h.application_name='queue_mint_holder')
                            """)
                            ).scalar_one()
                            if blocked or future.done():
                                break
                            Event().wait(0.02)
                        else:
                            pytest.fail("mint neither blocked nor completed")
                    assert blocked is not omit
                    if omit:
                        future.result(timeout=5)
                        assert stores.record.get(chunk.chunk_id) is not None
                    conn.commit()
                    future.result(timeout=5)
            finally:
                conn.rollback()
        assert stores.record.get(chunk.chunk_id) is not None
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("operation", ["create", "run", "accept"])
@pytest.mark.parametrize("omit", [False, True], ids=["queue-key", "negative-control"])
def test_work_item_mint_takes_queue_before_graph(database_url: str, operation: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "item_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "item_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        with waiter.begin() as conn:
            seed_graph(conn, "gr_queue", at=_AT)
        stores = chunk_stores(waiter, FixedClock(_AT))
        seed_chunk_record(stores, Chunk(chunk_id="ch_seed", graph_id="gr_queue", work_refs=[], minted_at=_AT))
        seeded = stores.record.get("ch_seed")
        assert seeded is not None and seeded.project_id is not None
        project_id = seeded.project_id
        seed_scope(hub_store_connections(waiter), "s", at=_AT)
        if operation == "accept":
            scoped = hub_store_connections(waiter)
            FindingStore(scoped).add(
                "fin_test",
                project_id=project_id,
                routine_name="r",
                scope_slug="s",
                class_="c",
                locus="file",
                summary="summary",
                introduced=None,
                at=_AT,
            )
            GardenProposalStore(scoped).create(
                "proposal",
                project_id=project_id,
                origin=GardenProposalOrigin.ROUTINE_RUN,
                routine_name="r",
                class_="c",
                title="t",
                body="b",
                findings=["fin_test"],
                at=_AT,
            )
        pointer = WorkRef(source="hub", ref="queue-test")
        chunk = Chunk(
            chunk_id="ch_new", graph_id="gr_queue", work_refs=[pointer], minted_at=_AT, project_id=seeded.project_id
        )
        items = WorkItemStore(hub_store_connections(waiter))
        graph_entered = Event()
        original_graph_lock = work_item_store.lock_keys

        def graph_lock(conn: sa.Connection, namespace: str, keys: Sequence[str]) -> None:
            graph_entered.set()
            original_graph_lock(conn, namespace, keys)

        def mint() -> None:
            common = {
                "pointer": pointer,
                "title": "t",
                "body": "b",
                "author": WorkItemAuthor.user("test"),
                "at": _AT,
                "chunk": chunk,
            }
            if operation == "create":
                items.create_with_chunk(**common, stated_priority=None)
            elif operation == "run":
                items.create_run_with_chunk(
                    **common, routine_name="r", scope_slug="s", run_mode="dry-run", project_id=project_id
                )
            else:
                items.accept_create(**common, proposal_id="proposal", reason=None, closed_by="test")

        original_queue_lock = work_item_store.lock_queue_positions
        with hub_store_connections(holder).read("hold queue key") as conn, ThreadPoolExecutor(max_workers=1) as pool:
            lock_queue_positions(conn)
            try:
                with (
                    patch.object(
                        work_item_store, "lock_queue_positions", (lambda _: None) if omit else original_queue_lock
                    ),
                    patch.object(work_item_store, "lock_keys", graph_lock),
                ):
                    future = pool.submit(mint)
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name='item_waiter' AND h.application_name='item_holder')
                            """)
                            ).scalar_one()
                            if blocked or future.done():
                                break
                            Event().wait(0.02)
                        else:
                            pytest.fail("work-item mint neither blocked nor completed")
                    assert blocked is not omit
                    if omit:
                        future.result(timeout=5)
                        assert graph_entered.is_set()
                    else:
                        assert not graph_entered.is_set()
                    conn.commit()
                    future.result(timeout=5)
                    assert graph_entered.is_set()
            finally:
                conn.rollback()
        assert stores.record.get("ch_new") is not None
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
