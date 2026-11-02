"""Admission fences the chunk facts of earlier submissions, independently of tenant admission."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import SystemClock
from blizzard.hub.domain.delivery.lines import FlowLineFactory
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.graph.actions import PASS, HubActionCatalogue
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.store.internal.graph_store import GraphStore
from tests.postgres_locks.test_flow_decisions import _AT, _RESOURCE, _TENANT, _seed
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("omit", [False, True], ids=["lock", "negative-control"])
def test_earlier_chunk_becoming_eligible_blocks_candidate(database_url: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "contender_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "contender_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    held, waiting = scoped_connections(holder, _TENANT), scoped_connections(waiter, _TENANT)
    clock = SystemClock()
    stores = build_chunk_stores(waiting, clock, registry=Mock())
    flows, graphs = FlowStore(waiting, clock), GraphStore(waiting)
    read_old, resume = Event(), Event()
    try:
        _seed(held)
        with held.write("seed") as conn:
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
            conn.execute(
                held.insert(s.chunks).values(chunk_id="ch_0", graph_id="gr_1", project_id="proj_1", minted_at=_AT)
            )
            for chunk_id in ("ch_0", "ch_1"):
                conn.execute(held.insert(s.chunk_promoted).values(chunk_id=chunk_id, promoted_at=_AT))
                conn.execute(
                    held.insert(s.lease_facts).values(chunk_id=chunk_id, epoch=1, runner_id="runner", minted_at=_AT)
                )
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
        earlier = flows.submit("ch_0", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT)
        candidate = flows.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT)
        assert earlier is not None and candidate is not None
        repositories, projects, links = Mock(), Mock(), Mock()
        repositories.list_all.return_value = []
        projects.get.return_value = None
        links.repositories_of.return_value = []
        reconciler = FlowLineReconciler(
            flows=flows,
            facts=stores.facts,
            record=stores.record,
            graphs=graphs,
            artifacts=stores.artifacts,
            repositories=repositories,
            forge_records=Mock(),
            projects=projects,
            links=links,
            catalogue=HubActionCatalogue.of([PASS]),
            refusals=Mock(),
            exclusive=stores.exclusive,
            lines=FlowLineFactory(facts=stores.facts, record=stores.record, graphs=graphs),
            clock=clock,
        )
        real_lock = chunk_exclusive_store.lock_chunk_row
        real_facts = chunk_exclusive_store.LockedChunkTransaction.facts_for

        def selective_lock(conn, chunk_id):
            if not (omit and chunk_id == "ch_0"):
                real_lock(conn, chunk_id)

        def pause_after_read(handle, chunk_ids):
            result = real_facts(handle, chunk_ids)
            if omit:
                read_old.set()
                assert resume.wait(10)
            return result

        with (
            patch.object(chunk_exclusive_store, "lock_chunk_row", selective_lock),
            patch.object(chunk_exclusive_store.LockedChunkTransaction, "facts_for", pause_after_read),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            with held.read("movement_without_admission") as conn:
                lock_chunk_row(conn, "ch_0")
                conn.execute(
                    held.insert(s.transitions).values(
                        transition_id="tr_0",
                        chunk_id="ch_0",
                        graph_id="gr_1",
                        to_node_id="nd_1",
                        epoch=1,
                        runner_id="runner",
                        recorded_at=_AT + timedelta(seconds=1),
                    )
                )
                future = pool.submit(reconciler.admit, 0, clock.now())
                try:
                    if omit:
                        assert read_old.wait(8), "admission did not read stale earlier facts"
                    else:
                        deadline = monotonic() + 5
                        with observer.connect() as watch:
                            while monotonic() < deadline:
                                watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                                blocked = watch.execute(
                                    sa.text("""
                                    SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                    JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                    WHERE w.application_name = 'contender_waiter' AND h.application_name = 'contender_holder')
                                """)
                                ).scalar_one()
                                if blocked:
                                    break
                                assert not future.done(), "admission bypassed the earlier chunk"
                                Event().wait(0.02)
                            else:
                                pytest.fail("admission did not wait for the earlier chunk")
                    conn.commit()
                finally:
                    resume.set()
            admitted = future.result(timeout=10)
        assert [turn.submission.submission_id for turn in admitted] == ([candidate.submission_id] if omit else [])
        assert [turn.submission_id for turn in flows.open_turns()] == ([candidate.submission_id] if omit else [])
        assert [fact.kind for fact in flows.facts_of(candidate.submission_id)] == ["submitted"] + (
            ["turn-started"] if omit else []
        )
    finally:
        resume.set()
        holder.dispose()
        waiter.dispose()
        observer.dispose()
