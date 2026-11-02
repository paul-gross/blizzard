"""The closure writers lock the chunk independently of tenant admission."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, WorkItemCloseOutcome, WorkRef
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_delivery_store, chunk_rows
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("writer_kind", ["closure", "enqueue"])
@pytest.mark.parametrize("omit_lock", [False, True])
def test_closure_writer_waits_for_chunk_lock(
    database_url: str, writer_kind: str, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "closure_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "closure_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_closure")
    ref = WorkRef("default", "1")
    try:
        scope = scoped_connections(holder, tenant)
        with scope.founding("found", name="closure", created_at=now, created_by="test"):
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
            conn.execute(
                scope.insert(s.chunks).values(chunk_id="chunk", graph_id="graph", project_id="project", minted_at=now)
            )
            conn.execute(scope.insert(s.chunk_work_refs).values(chunk_id="chunk", source=ref.source, ref=ref.ref))
        waiting = scoped_connections(waiter, tenant)

        def write() -> None:
            if writer_kind == "closure":
                chunk_delivery_store.ChunkDeliveryStore(waiting).record_work_item_closure(
                    "chunk", pointer=ref, outcome=WorkItemCloseOutcome.CLOSED, reason=None, at=now
                )
            else:
                with waiting.write("enqueue") as conn:
                    chunk_rows.enqueue_close_intents(
                        conn,
                        "chunk",
                        at=now,
                        close_decision=CloseEnqueueDecision(closable=frozenset(), skipped=frozenset({ref})),
                    )

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scope.read("hold_chunk") as conn:
                chunk_rows.lock_chunk_row(conn, "chunk")
                if omit_lock:
                    monkeypatch.setattr(
                        chunk_delivery_store if writer_kind == "closure" else chunk_rows,
                        "lock_chunk_row",
                        lambda *args: None,
                    )
                future = actors.submit(write)
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
                                WHERE w.application_name = 'closure_waiter' AND h.application_name = 'closure_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "writer escaped the chunk lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("writer did not block on the chunk")
            future.result(timeout=10)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("writer_kind", ["repo", "delivery"])
def test_landing_locks_chunk_before_inserting_fk_child(database_url: str, writer_kind: str) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "landing_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "landing_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_landing")
    inserted = Event()

    @event.listens_for(waiter, "before_cursor_execute")
    def track_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.lower().startswith("insert into delivery_"):
            inserted.set()

    try:
        scope = scoped_connections(holder, tenant)
        with scope.founding("found", name="landing", created_at=now, created_by="test"):
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
            conn.execute(
                scope.insert(s.chunks).values(chunk_id="chunk", graph_id="graph", project_id="project", minted_at=now)
            )
        store = chunk_delivery_store.ChunkDeliveryStore(scoped_connections(waiter, tenant))
        decision = CloseEnqueueDecision(closable=frozenset(), skipped=frozenset())

        def write() -> None:
            if writer_kind == "repo":
                store.record_delivery_repo_landed(
                    "chunk", repo="repo", commit_hash="abc", repository_id=None, at=now, close_decision=decision
                )
            else:
                store.record_delivery_landed("chunk", at=now, close_decision=decision)

        with ThreadPoolExecutor(max_workers=1) as actors:
            with holder.begin() as conn:
                conn.execute(sa.text("SELECT chunk_id FROM chunks WHERE chunk_id = 'chunk' FOR NO KEY UPDATE"))
                future = actors.submit(write)
                deadline = monotonic() + 5
                with observer.connect() as monitor:
                    while monotonic() < deadline:
                        blocked = monitor.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                            JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name = 'landing_waiter' AND h.application_name = 'landing_holder')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not future.done(), "writer escaped the chunk lock"
                        Event().wait(0.02)
                    else:
                        pytest.fail("writer did not block on the chunk")
                assert not inserted.is_set(), "landing child inserted before acquiring chunk lock"
            future.result(timeout=10)
        assert inserted.is_set()
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
