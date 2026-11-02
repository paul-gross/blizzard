"""Lifecycle writes serialize with the default graph's name decision."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

import blizzard.hub.store.internal.graph_store as graph_module
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.graph_store import GraphStore, LockedGraphName
from blizzard.hub.tenancy import TenantId
from tests.support import make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("retired", [True, False])
@pytest.mark.parametrize("omit_lock", [False, True])
def test_lifecycle_waits_for_name_decision(
    database_url: str, retired: bool, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "graph_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "graph_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    try:
        scoped = scoped_connections(holder, TenantId("ten_graph_lifecycle"))
        with scoped.founding("found", name="graph-lifecycle", created_at=now, created_by="test"):
            pass
        graphs = GraphStore(scoped)
        graphs.mint(make_graph("gr_1", "g"), definition_yaml="", at=now)
        graphs.record_lifecycle("gr_1", retired=not retired, at=now, by="test")
        writer = GraphStore(scoped_connections(waiter, TenantId("ten_graph_lifecycle")))
        with ThreadPoolExecutor(max_workers=1) as actors:
            # Hold only the decision key, not the scoped write's tenant lock: the
            # latter would conceal a missing key lock in the negative control.
            with scoped.read("hold_decision") as conn:
                lock_keys(conn, "graph_name", ["g"])
                decision = LockedGraphName(conn, graphs)
                assert (decision.get_enabled_by_name("g") is None) is (not retired)
                if omit_lock:
                    monkeypatch.setattr(graph_module, "lock_keys", lambda *args: None)
                future = actors.submit(writer.record_lifecycle, "gr_1", retired=retired, at=now, by="test")
                if omit_lock:
                    future.result(timeout=5)
                    assert (decision.get_enabled_by_name("g") is None) is retired
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                  WHERE w.application_name = 'graph_waiter' AND h.application_name = 'graph_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "lifecycle writer escaped the name lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("lifecycle writer did not block")
                    assert (decision.get_enabled_by_name("g") is None) is (not retired)
            future.result(timeout=10)
        assert graphs.is_retired("gr_1") is retired
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
