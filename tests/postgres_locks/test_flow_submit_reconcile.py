"""The reconciler rechecks a candidate after a competing chunk movement."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import SystemClock
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.graph.actions import PASS, HubActionCatalogue
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.store.internal.graph_store import GraphStore
from tests.postgres_locks.test_flow_decisions import _AT, _TENANT, _seed
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("movement", ["stop", "restart"])
@pytest.mark.parametrize("omit", [False, True], ids=["lock", "negative-control"])
def test_submit_rechecks_candidate_after_movement(database_url: str, movement: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "submit_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "submit_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    held, waiting = scoped_connections(holder, _TENANT), scoped_connections(waiter, _TENANT)
    clock = SystemClock()
    stores = build_chunk_stores(waiting, clock, registry=Mock())
    flows = FlowStore(waiting, clock)
    read_old, resume = Event(), Event()
    try:
        _seed(held)
        with held.write("seed_delivering") as conn:
            conn.execute(
                held.insert(s.graph_nodes).values(
                    node_id="nd_1",
                    graph_id="gr_1",
                    name="deliver",
                    executor="hub",
                    session="fresh",
                    judged_by="worker",
                    action="pass",
                )
            )
            conn.execute(held.insert(s.chunk_promoted).values(chunk_id="ch_1", promoted_at=_AT))
            conn.execute(held.insert(s.lease_facts).values(chunk_id="ch_1", epoch=1, runner_id="runner", minted_at=_AT))
            conn.execute(
                held.insert(s.transitions).values(
                    transition_id="tr_1",
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    to_node_id="nd_1",
                    epoch=1,
                    runner_id="runner",
                    recorded_at=_AT,
                )
            )
        initial = stores.facts.load_facts_for(["ch_1"])["ch_1"]
        assert (initial.status(), initial.current_node_id(), initial.latest_epoch()) == (
            ChunkStatus.DELIVERING,
            "nd_1",
            1,
        )
        repositories, forges, projects, links = Mock(), Mock(), Mock(), Mock()
        repositories.list_all.return_value = []
        repositories.list_all_locked.return_value = []
        forges.list_all.return_value = []
        forges.list_all_locked.return_value = []
        projects.get.return_value = None
        projects.get_locked.return_value = None
        links.repositories_of.return_value = []
        links.repositories_of_locked.return_value = []
        reconciler = FlowLineReconciler(
            flows=flows,
            facts=stores.facts,
            record=stores.record,
            graphs=GraphStore(waiting),
            artifacts=stores.artifacts,
            repositories=repositories,
            forge_records=forges,
            projects=projects,
            links=links,
            catalogue=HubActionCatalogue.of([PASS]),
            refusals=Mock(),
            exclusive=stores.exclusive,
            lines=Mock(),
            clock=clock,
        )
        real_lock = chunk_exclusive_store.lock_chunk_row
        real_facts = chunk_exclusive_store.LockedChunkTransaction.facts

        def selective_lock(conn, chunk_id):
            if not omit:
                real_lock(conn, chunk_id)

        def pause_after_read(handle, chunk_id):
            result = real_facts(handle, chunk_id)
            if omit:
                read_old.set()
                assert resume.wait(10), "stale candidate read timed out"
            return result

        with (
            patch.object(chunk_exclusive_store, "lock_chunk_row", selective_lock),
            patch.object(chunk_exclusive_store.LockedChunkTransaction, "facts", pause_after_read),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            # A direct scoped transaction holds the row without tenant write admission.
            with held.read("movement_without_admission") as conn:
                lock_chunk_row(conn, "ch_1")
                if movement == "stop":
                    conn.execute(
                        held.insert(s.chunk_stopped).values(
                            chunk_id="ch_1", stopped_at=_AT + timedelta(seconds=1), stopped_by="operator"
                        )
                    )
                else:
                    conn.execute(
                        held.insert(s.chunk_restarts).values(
                            chunk_id="ch_1",
                            graph_id="gr_1",
                            from_node_id="nd_1",
                            to_node_id="nd_1",
                            epoch=2,
                            restarted_by="operator",
                            recorded_at=_AT + timedelta(seconds=1),
                        )
                    )
                future = pool.submit(reconciler.reconcile, 0)
                try:
                    if omit:
                        assert read_old.wait(8), "reconciler did not read the stale candidate"
                        assert not future.done()
                    else:
                        deadline = monotonic() + 5
                        with observer.connect() as watch:
                            while monotonic() < deadline:
                                watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                                blocked = watch.execute(
                                    sa.text("""
                                    SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                    JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                    WHERE w.application_name = 'submit_waiter' AND h.application_name = 'submit_holder')
                                """)
                                ).scalar_one()
                                if blocked:
                                    break
                                assert not future.done(), "reconciler bypassed the chunk row"
                                Event().wait(0.02)
                            else:
                                pytest.fail("reconciler did not wait for the chunk row")
                    conn.commit()
                finally:
                    resume.set()
            result = future.result(timeout=10)
        assert result.submitted == int(omit)
        submissions = flows.submissions_of("ch_1")
        assert [(sub.node_id, sub.epoch, sub.generation) for sub in submissions] == ([("nd_1", 1, 0)] if omit else [])
        current = stores.facts.load_facts_for(["ch_1"])["ch_1"]
        if movement == "stop":
            assert current.status() is ChunkStatus.STOPPED
        else:
            assert current.status() is ChunkStatus.DELIVERING
            assert current.latest_epoch() == 2
    finally:
        resume.set()
        holder.dispose()
        waiter.dispose()
        observer.dispose()
