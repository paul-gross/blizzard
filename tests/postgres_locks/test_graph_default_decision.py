"""Default mint decisions serialize with same-name graph writers."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event, current_thread
from time import monotonic

import pytest
import sqlalchemy as sa

import blizzard.hub.domain.graph.authoring as authoring
import blizzard.hub.store.internal.graph_store as graph_module
from blizzard.foundation.clock import SystemClock
from blizzard.hub.domain.graph.authoring import DefaultGraphRetired, GraphMintService
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.graph_store import GraphStore, LockedGraphName
from blizzard.hub.tenancy import TenantId
from tests.support import TEST_CATALOGUE, make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("writer", ["mint", "unretire"])
@pytest.mark.parametrize("omit_lock", [False, True])
def test_default_decision_against_graph_writer(database_url, writer, omit_lock, monkeypatch):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "default_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "default_writer", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    tenant = TenantId("ten_default_decision")
    now = datetime.now(UTC)
    doc = PACKAGED.default.doc
    entered, release = Event(), Event()
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="default-decision", created_at=now, created_by="test"):
            pass
        graphs = GraphStore(scoped)
        competing = GraphStore(scoped_connections(waiter, tenant))
        if writer == "unretire":
            graphs.mint(make_graph("gr_other", doc.name), definition_yaml="", at=now)
            graphs.record_lifecycle("gr_other", retired=True, at=now, by="test")

        # Isolate the name lock from tenant admission while keeping the actual decision and transaction.
        @contextmanager
        def locked_name(name):
            with scoped.read("decision") as conn:
                lock_keys(conn, "graph_name", [name])
                yield LockedGraphName(conn, graphs)
                conn.commit()

        monkeypatch.setattr(graphs, "locked_name", locked_name)
        resolve = authoring.resolve_default

        def paused_resolve(*args, **kwargs):
            entered.set()
            assert release.wait(6)
            return resolve(*args, **kwargs)

        monkeypatch.setattr(authoring, "resolve_default", paused_resolve)
        original_lock = graph_module.lock_keys
        if omit_lock:

            def selective_lock(*args, **kwargs):
                if current_thread().name.startswith("writer"):
                    return None
                return original_lock(*args, **kwargs)

            monkeypatch.setattr(graph_module, "lock_keys", selective_lock)
        service = GraphMintService(graphs=graphs, clock=SystemClock(), catalogue=TEST_CATALOGUE)

        def write():
            if writer == "mint":
                competing.mint(make_graph("gr_other", doc.name), definition_yaml="", at=now)
            else:
                competing.record_lifecycle("gr_other", retired=False, at=now, by="test")

        with (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="decision") as decisions,
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="writer") as writers,
        ):
            decision = decisions.submit(service.ensure_default, doc, definition_yaml=PACKAGED.default.text)
            try:
                assert entered.wait(5)
                future = writers.submit(write)
                if omit_lock:
                    future.result(timeout=5)
                    enabled = competing.get_enabled_by_name(doc.name)
                    assert enabled is not None and enabled.graph_id == "gr_other"
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                  WHERE w.application_name = 'default_writer' AND h.application_name = 'default_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "writer escaped the decision lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("writer did not block")
            finally:
                release.set()
            if writer == "unretire":
                with pytest.raises(DefaultGraphRetired):
                    decision.result(timeout=10)
            else:
                minted = decision.result(timeout=10)
                assert minted.graph_id != "gr_other"
            future.result(timeout=10)
        names = [g for g in graphs.list_summaries() if g.name == doc.name]
        assert len(names) == (2 if writer == "mint" else 1)
        assert graphs.get_enabled_by_name(doc.name) is not None
    finally:
        release.set()
        holder.dispose()
        waiter.dispose()
        observer.dispose()
