"""Resolution rechecks the gate and chunk after acquiring the chunk lock."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from unittest.mock import Mock

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import DecisionClosed
from blizzard.hub.domain.execution.decisions import DecisionService
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("closing", ["stop", "transition"])
@pytest.mark.parametrize("omit", [False, True], ids=["locked", "omission"])
def test_resolve_rechecks_after_chunk_contention(database_url, closing, omit, monkeypatch):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "resolve_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "resolve_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    tenant = TenantId("ten_resolve_contention")
    scope = scoped_connections(holder, tenant)
    try:
        with scope.founding("found", name="resolve-contention", created_at=_AT, created_by="test"):
            pass
        with scope.write("seed") as conn:
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.projects).values(
                    project_id="project", slug="p", name="p", description="", revision=1, color="blue", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.chunks).values(chunk_id="chunk", graph_id="graph", project_id="project", minted_at=_AT)
            )
            conn.execute(
                scope.insert(s.decisions).values(
                    decision_id="decision",
                    chunk_id="chunk",
                    project_id="project",
                    node_id="n",
                    node_name="n",
                    epoch=1,
                    choices=json.dumps([{"name": "ok", "description": "d"}]),
                    check_results="[]",
                    submitted_at=_AT,
                )
            )
        waiter_scope = scoped_connections(waiter, tenant)
        clock = FixedClock(_AT)
        stores = build_chunk_stores(waiter_scope, clock, registry=RunnerRegistryStore(waiter_scope))
        service = DecisionService(
            facts=stores.facts,
            route=stores.route,
            decisions=stores.decisions,
            exclusive=stores.exclusive,
            retired=Mock(),
            clock=clock,
            closer_available=lambda _: True,
        )
        decision = stores.decisions.get_decision("decision")
        assert decision is not None
        if omit:
            monkeypatch.setattr(chunk_exclusive_store, "lock_chunk_row", lambda conn, chunk_id: None)
        with ThreadPoolExecutor(max_workers=1) as pool:
            # Raw transaction holds the chunk row but never takes tenant admission.
            with holder.begin() as conn:
                conn.execute(
                    sa.text("UPDATE chunks SET chunk_id = chunk_id WHERE tenant_id = :tenant AND chunk_id = 'chunk'"),
                    {"tenant": str(tenant)},
                )
                if closing == "stop":
                    conn.execute(
                        scope.insert(s.chunk_stopped).values(chunk_id="chunk", stopped_at=_AT, stopped_by="other")
                    )
                else:
                    conn.execute(
                        scope.insert(s.transitions).values(
                            transition_id="transition",
                            chunk_id="chunk",
                            graph_id="graph",
                            from_node_id="n",
                            to_node_id="next",
                            decision_id="decision",
                            epoch=1,
                            runner_id="runner",
                            recorded_at=_AT,
                        )
                    )
                future = pool.submit(service.resolve, decision, choice="ok", resolved_by="operator")
                if omit:
                    assert future.result(timeout=5).resolved
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity w JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid)) WHERE w.application_name = 'resolve_waiter' AND h.application_name = 'resolve_holder')"
                                )
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "resolve bypassed the chunk lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("resolve did not wait on the chunk lock")
            if not omit:
                with pytest.raises(DecisionClosed):
                    future.result(timeout=10)
        current = stores.decisions.get_decision("decision")
        assert current is not None
        assert current.resolved_choice == ("ok" if omit else None)
        assert stores.decisions.list_open_decisions() == []
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
