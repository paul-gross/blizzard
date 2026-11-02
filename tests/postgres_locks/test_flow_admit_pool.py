"""Admission must not check out another connection while holding its write transaction."""

from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import TimeoutError as PoolTimeout

from blizzard.foundation.clock import SystemClock
from blizzard.hub.domain.delivery.flows import PollPolicy
from blizzard.hub.domain.delivery.lines import FlowLineFactory
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.graph.actions import PASS, HubActionCatalogue
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.store.internal.graph_store import GraphStore
from tests.postgres_locks.test_flow_decisions import _AT, _TENANT, _seed
from tests.support import count_queries, scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("nested_checkout", [False, True], ids=["same-connection", "negative-control"])
def test_admission_completes_with_one_connection(database_url: str, nested_checkout: bool) -> None:
    engine = sa.create_engine(database_url, pool_size=1, max_overflow=0, pool_timeout=0.5)
    scoped = scoped_connections(engine, _TENANT)
    clock = SystemClock()
    stores = build_chunk_stores(scoped, clock, registry=Mock())
    flows, graphs = FlowStore(scoped, clock), GraphStore(scoped)
    try:
        _seed(scoped)
        with scoped.write("seed_delivering") as conn:
            conn.execute(
                scoped.insert(s.graph_nodes).values(
                    node_id="nd_1",
                    graph_id="gr_1",
                    name="deliver",
                    executor="hub",
                    session="fresh",
                    judged_by="worker",
                    action="pass",
                    poll_interval_seconds=17,
                    poll_timeout_seconds=91,
                )
            )
            conn.execute(
                scoped.insert(s.graph_nodes).values(
                    node_id="nd_2",
                    graph_id="gr_1",
                    name="other",
                    executor="hub",
                    session="fresh",
                    judged_by="worker",
                    action="pass",
                )
            )
            conn.execute(scoped.insert(s.chunk_promoted).values(chunk_id="ch_1", promoted_at=_AT))
            conn.execute(
                scoped.insert(s.lease_facts).values(chunk_id="ch_1", epoch=1, runner_id="runner", minted_at=_AT)
            )
            conn.execute(
                scoped.insert(s.transitions).values(
                    transition_id="tr_1",
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    to_node_id="nd_1",
                    epoch=1,
                    runner_id="runner",
                    recorded_at=_AT,
                )
            )
        sub = flows.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[], at=_AT)
        assert sub is not None
        reconciler = FlowLineReconciler(
            flows=flows,
            facts=stores.facts,
            record=stores.record,
            graphs=graphs,
            artifacts=stores.artifacts,
            repositories=Mock(),
            forge_records=Mock(),
            projects=Mock(),
            links=Mock(),
            catalogue=HubActionCatalogue.of([PASS]),
            refusals=Mock(),
            exclusive=stores.exclusive,
            lines=FlowLineFactory(facts=stores.facts, record=stores.record, graphs=graphs),
            clock=clock,
        )
        with stores.exclusive.locked_flow_admission(["ch_1"]) as handle:
            nodes = [("gr_1", "nd_1"), ("gr_1", "nd_2"), ("wrong_graph", "nd_1")]
            policies = flows.poll_policies_locked(handle, nodes)
            assert policies == {
                ("gr_1", "nd_1"): PollPolicy(timedelta(seconds=17), timedelta(seconds=91)),
                ("gr_1", "nd_2"): PollPolicy(),
            }
            assert count_queries(engine, lambda: flows.poll_policies_locked(handle, nodes[:1])) == 1
            assert count_queries(engine, lambda: flows.poll_policies_locked(handle, nodes)) == 1

        real_policies = FlowStore.poll_policies_locked

        def with_nested_checkout(store, handle, nodes):
            if nested_checkout:
                graphs.get_many(["gr_1"])
            return real_policies(store, handle, nodes)

        with patch.object(FlowStore, "poll_policies_locked", with_nested_checkout):
            if nested_checkout:
                with pytest.raises(HubStoreError) as exc:
                    reconciler.admit(0, clock.now())
                assert isinstance(exc.value.__cause__, PoolTimeout)
                assert flows.open_turns() == []
            else:
                admitted = reconciler.admit(0, clock.now())
                assert len(admitted) == 1
                assert [t.turn_id for t in flows.open_turns()] == [admitted[0].turn.turn_id]
    finally:
        engine.dispose()
