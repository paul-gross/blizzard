"""The refusal holds each independent decision lock until routing completes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import SystemClock
from blizzard.hub.domain.actions.visit import VisitRefusal
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.graph.actions import PASS, HubActionCatalogue
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store, flow_store
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row, lock_keys
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.graph_store import GraphStore
from tests.postgres_locks.test_flow_decisions import _AT, _TENANT, _seed
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("key", ["config", "chunk", "flow-chunk"])
@pytest.mark.parametrize("omit", [False, True], ids=["lock", "negative-control"])
def test_refusal_retains_decision_locks(database_url: str, key: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    actor = sa.create_engine(database_url, connect_args={"application_name": "refusal_actor", "options": options})
    competitor = sa.create_engine(
        database_url, connect_args={"application_name": "refusal_competitor", "options": options}
    )
    observer = sa.create_engine(database_url)
    scope, other = scoped_connections(actor, _TENANT), scoped_connections(competitor, _TENANT)
    entered, release = Event(), Event()
    try:
        _seed(scope)
        with scope.write("seed_visit") as conn:
            conn.execute(
                scope.insert(s.graph_nodes).values(
                    node_id="nd_1",
                    graph_id="gr_1",
                    name="deliver",
                    executor="hub",
                    session="fresh",
                    judged_by="worker",
                    action="pass",
                )
            )
            conn.execute(scope.insert(s.chunk_promoted).values(chunk_id="ch_1", promoted_at=_AT))
            conn.execute(
                scope.insert(s.lease_facts).values(chunk_id="ch_1", epoch=1, runner_id="runner", minted_at=_AT)
            )
            conn.execute(
                scope.insert(s.transitions).values(
                    transition_id="tr_1",
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    to_node_id="nd_1",
                    epoch=1,
                    runner_id="runner",
                    recorded_at=_AT,
                )
            )
        clock = SystemClock()
        chunks = build_chunk_stores(scope, clock, registry=Mock())
        refusals = Mock()

        def refuse(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return []

        refusals.refuse_locked.side_effect = refuse
        repositories, forges, projects, links = Mock(), Mock(), Mock(), Mock()
        repositories.list_all_locked.return_value = []
        forges.list_all_locked.return_value = []
        projects.get_locked.return_value = None
        links.repositories_of_locked.return_value = []
        reconciler = FlowLineReconciler(
            flows=flow_store.FlowStore(scope, clock),
            facts=chunks.facts,
            record=chunks.record,
            graphs=GraphStore(scope),
            artifacts=chunks.artifacts,
            repositories=repositories,
            forge_records=forges,
            projects=projects,
            links=links,
            catalogue=HubActionCatalogue.of([PASS]),
            refusals=refusals,
            exclusive=chunks.exclusive,
            lines=Mock(),
            clock=clock,
        )
        config_lock, chunk_lock, flow_lock = (
            chunk_exclusive_store.lock_config_authoring,
            chunk_exclusive_store.lock_chunk_row,
            flow_store.lock_flow_chunk,
        )

        def maybe_config(conn):
            if not (omit and key == "config"):
                config_lock(conn)

        def maybe_chunk(conn, chunk_id):
            if not (omit and key == "chunk"):
                chunk_lock(conn, chunk_id)

        def maybe_flow(conn, chunk_id):
            if not (omit and key == "flow-chunk"):
                flow_lock(conn, chunk_id)

        def contend():
            with other.read("independent_decision") as conn:
                if key == "config":
                    lock_config_authoring(conn)
                elif key == "chunk":
                    lock_chunk_row(conn, "ch_1")
                else:
                    lock_keys(conn, "flow-chunk", ["ch_1"])
                conn.commit()

        with (
            patch.object(chunk_exclusive_store, "lock_config_authoring", maybe_config),
            patch.object(chunk_exclusive_store, "lock_chunk_row", maybe_chunk),
            patch.object(flow_store, "lock_flow_chunk", maybe_flow),
            patch("blizzard.hub.domain.delivery.reconcile.resolve_visit", return_value=VisitRefusal("test", "refused")),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            decision = pool.submit(reconciler.reconcile, 0)
            assert entered.wait(8), "refusal was not reached"
            waiting = pool.submit(contend)
            try:
                if omit:
                    waiting.result(timeout=5)
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as conn:
                        while monotonic() < deadline:
                            conn.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                            blocked = conn.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name = 'refusal_competitor' AND h.application_name = 'refusal_actor')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not waiting.done(), "competitor bypassed the decision lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("competitor did not wait for the decision lock")
            finally:
                release.set()
            assert decision.result(timeout=10).submitted == 0
            waiting.result(timeout=10)
    finally:
        release.set()
        actor.dispose()
        competitor.dispose()
        observer.dispose()
