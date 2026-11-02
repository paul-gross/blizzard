"""PostgreSQL contention between admission and the reconciler's departure pass."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import SystemClock
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import flow_store
from tests.postgres_locks.test_flow_decisions import _AT, _RESOURCE, _TENANT, _seed
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("omit", [False, True], ids=["lock", "negative-control"])
def test_departure_rechecks_turn_after_competing_admission(database_url: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "reconcile_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "reconcile_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    held, waiting = scoped_connections(holder, _TENANT), scoped_connections(waiter, _TENANT)
    clock = SystemClock()
    admitting, flows = flow_store.FlowStore(held, clock), flow_store.FlowStore(waiting, clock)
    entered, release = Event(), Event()
    try:
        _seed(held)
        sub = admitting.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT)
        assert sub is not None
        facts = Mock()
        facts.load_live_statuses.return_value = {}
        facts.load_facts_for.return_value = {}
        record = Mock()
        record.get_many.return_value = {}
        graphs = Mock()
        graphs.get_many.return_value = {}
        reconciler = FlowLineReconciler(
            flows=flows,
            facts=facts,
            record=record,
            graphs=graphs,
            artifacts=Mock(),
            repositories=Mock(),
            forge_records=Mock(),
            projects=Mock(),
            links=Mock(),
            catalogue=Mock(),
            refusals=Mock(),
            exclusive=Mock(),
            lines=Mock(),
            clock=clock,
        )
        write_facts = flow_store.write_facts
        lock_submission = flow_store.lock_flow_submission

        def hold_admission(conn, *args, **kwargs):
            result = write_facts(conn, *args, **kwargs)
            if conn.engine is holder and kwargs.get("kind") == "turn-started":
                entered.set()
                assert release.wait(10), "admission timed out"
            return result

        def selective_lock(conn, submission_id):
            if omit and conn.engine is waiter:
                return True
            return lock_submission(conn, submission_id)

        with (
            patch.object(held, "_lock_open_tenant"),
            patch.object(waiting, "_lock_open_tenant"),
            patch.object(flow_store, "write_facts", hold_admission),
            patch.object(flow_store, "lock_flow_submission", selective_lock),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            one = pool.submit(admitting.take_turn, sub.submission_id, passed_over=frozenset(), terms=(), at=_AT)
            try:
                assert entered.wait(10), "admission did not reach its uncommitted turn"
                two = pool.submit(reconciler.reconcile, 0)
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = watch.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiter
                            JOIN pg_stat_activity AS holder ON holder.pid = ANY(pg_blocking_pids(waiter.pid))
                            WHERE waiter.application_name = 'reconcile_waiter'
                              AND holder.application_name = 'reconcile_holder')
                        """)
                        ).scalar_one()
                        if blocked or two.done():
                            break
                        Event().wait(0.02)
                    else:
                        pytest.fail("reconciler neither blocked nor completed")
                assert blocked is not omit
                if omit:
                    result = two.result(timeout=5)
                    assert result.closed == 1
            finally:
                release.set()
            assert one.result(timeout=10) is not None
            if not omit:
                result = two.result(timeout=10)
                assert result.closed == 0
                assert result.written == ()
                assert result.floor == sub.arrival
                again = reconciler.reconcile(result.floor)
                assert again.floor == sub.arrival
                assert again.closed == 0

        assert len(flows.open_turns()) == 1
        assert len(flows.open_submissions_from(0)) == int(not omit)
        with waiting.read("verify_reconcile") as conn:
            assert conn.execute(waiting.select(s.flow_submission_closures, sa.func.count())).scalar_one() == int(omit)
        assert [fact.kind for fact in flows.facts_of(sub.submission_id)] == (
            ["submitted", "turn-started", "withdrawn"] if omit else ["submitted", "turn-started"]
        )
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
