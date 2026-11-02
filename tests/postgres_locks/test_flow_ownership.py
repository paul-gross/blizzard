"""PostgreSQL proofs for landing ownership and replay under competing writes."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from time import monotonic
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import SystemClock
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.delivery.flows import FlowLanding, FlowResource
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import flow_store
from blizzard.hub.store.internal.flow_rows import lock_flow_chunk
from tests.postgres_locks.test_flow_decisions import _AT, _RESOURCE, _TENANT, _seed
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


def _record(store: flow_store.FlowStore, landing: FlowLanding):
    return store.record_landing(
        landing,
        node_id="nd_1",
        node_name="deliver",
        epoch=1,
        admission=EpochAdmission.AT_OR_ABOVE,
        repo="repo",
        revision="m1",
        at=_AT,
        close_decision=CloseEnqueueDecision(frozenset(), frozenset()),
    )


def test_replays_and_mismatches_on_postgres(database_url: str) -> None:
    engine = sa.create_engine(database_url)
    scoped = scoped_connections(engine, _TENANT)
    flows = flow_store.FlowStore(scoped, SystemClock())
    try:
        _seed(scoped)
        with scoped.write("second_chunk") as conn:
            conn.execute(
                scoped.insert(s.chunks).values(chunk_id="ch_2", graph_id="gr_1", project_id="proj_1", minted_at=_AT)
            )
        first = flows.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT)
        second = flows.submit(
            "ch_2", node_id="nd_1", epoch=1, generation=0, resources=[FlowResource("repo_b", "main")], at=_AT
        )
        assert first is not None and second is not None
        assert flows.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT) is None
        landing = FlowLanding.of(first.flows[0], first, project_id="proj_1", node_name="deliver", revision="m1")
        for bad in (
            replace(landing, submission_id="missing"),
            replace(landing, submission_id=second.submission_id),
            replace(landing, flow=second.flows[0]),
            replace(landing, flow=replace(first.flows[0], branch="wrong")),
        ):
            with pytest.raises(ValueError, match="landing"):
                _record(flows, bad)
        assert _record(flows, landing).event_id is not None
        for replay in (
            landing,
            replace(landing, submission_id="missing"),
            replace(landing, submission_id=second.submission_id),
            replace(landing, flow=second.flows[0]),
        ):
            result = _record(flows, replay)
            assert result.event_id is None and result.written == ()
        with scoped.read("verify_replays") as conn:
            for table, expected in (
                (s.flow_submissions, 2),
                (s.artifacts, 1),
                (s.delivery_repo_landed, 1),
                (s.event_log, 1),
            ):
                assert conn.execute(scoped.select(table, sa.func.count())).scalar_one() == expected
            assert (
                conn.execute(
                    scoped.select(s.flow_facts, sa.func.count()).where(s.flow_facts.c.kind == "landed")
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_concurrent_landing_replays_write_once(database_url: str) -> None:
    options = "-c statement_timeout=8000"
    first_engine = sa.create_engine(
        database_url, connect_args={"application_name": "landing_first", "options": options}
    )
    second_engine = sa.create_engine(
        database_url, connect_args={"application_name": "landing_second", "options": options}
    )
    observer = sa.create_engine(database_url, connect_args={"options": options})
    first_scope, second_scope = scoped_connections(first_engine, _TENANT), scoped_connections(second_engine, _TENANT)
    first, second = flow_store.FlowStore(first_scope, SystemClock()), flow_store.FlowStore(second_scope, SystemClock())
    entered, release = Event(), Event()
    try:
        _seed(first_scope)
        sub = first.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT)
        assert sub is not None
        landing = FlowLanding.of(sub.flows[0], sub, project_id="proj_1", node_name="deliver", revision="m1")
        write_landing = flow_store.write_landing

        def hold_before_commit(conn, *args, **kwargs):
            result = write_landing(conn, *args, **kwargs)
            if conn.engine is first_engine:
                entered.set()
                assert release.wait(10)
            return result

        with (
            patch.object(first_scope, "_lock_open_tenant"),
            patch.object(second_scope, "_lock_open_tenant"),
            patch.object(flow_store, "write_landing", hold_before_commit),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            one = pool.submit(_record, first, landing)
            try:
                assert entered.wait(10)
                two = pool.submit(_record, second, landing)
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = watch.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiter
                            JOIN pg_stat_activity AS holder ON holder.pid = ANY(pg_blocking_pids(waiter.pid))
                            WHERE waiter.application_name = 'landing_second' AND holder.application_name = 'landing_first')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not two.done()
                        Event().wait(0.02)
                    else:
                        pytest.fail("concurrent landing did not wait")
            finally:
                release.set()
            assert one.result(timeout=10).event_id is not None
            replay = two.result(timeout=10)
            assert replay.event_id is None and replay.written == ()
        with first_scope.read("verify_race") as conn:
            for table in (s.artifacts, s.delivery_repo_landed, s.event_log):
                assert conn.execute(first_scope.select(table, sa.func.count())).scalar_one() == 1
            assert (
                conn.execute(
                    first_scope.select(s.flow_facts, sa.func.count()).where(s.flow_facts.c.kind == "landed")
                ).scalar_one()
                == 1
            )
    finally:
        first_engine.dispose()
        second_engine.dispose()
        observer.dispose()


@pytest.mark.parametrize("omit", [False, True], ids=["lock", "negative-control"])
def test_landing_checks_holdings_after_competing_mutation(database_url: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "ownership_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "ownership_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    held, waiting = scoped_connections(holder, _TENANT), scoped_connections(waiter, _TENANT)
    flows = flow_store.FlowStore(waiting, SystemClock())
    try:
        _seed(held)
        sub = flows.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT)
        assert sub is not None
        landing = FlowLanding.of(sub.flows[0], sub, project_id="proj_1", node_name="deliver", revision="m1")
        # This synthetic writer changes a holding under the decision key while the landing is in flight.
        with (
            patch.object(held, "_lock_open_tenant"),
            patch.object(waiting, "_lock_open_tenant"),
            patch.object(flow_store, "lock_flow_chunk", (lambda conn, chunk: None) if omit else lock_flow_chunk),
            held.write("mutate_holding") as conn,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            lock_flow_chunk(conn, "ch_1")
            future = pool.submit(_record, flows, landing)
            try:
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = watch.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiter
                            JOIN pg_stat_activity AS holder ON holder.pid = ANY(pg_blocking_pids(waiter.pid))
                            WHERE waiter.application_name = 'ownership_waiter' AND holder.application_name = 'ownership_holder')
                        """)
                        ).scalar_one()
                        if blocked or future.done():
                            break
                        Event().wait(0.02)
                    else:
                        pytest.fail("landing neither blocked nor completed")
                assert blocked is not omit
                if omit:
                    assert future.result(timeout=5).event_id is not None
                conn.execute(
                    held.delete(s.flow_submission_flows).where(
                        s.flow_submission_flows.c.submission_id == sub.submission_id
                    )
                )
            finally:
                conn.commit()
            if not omit:
                with pytest.raises(ValueError, match="flow is not held"):
                    future.result(timeout=10)
        with waiting.read("verify_mutation") as conn:
            assert conn.execute(
                waiting.select(s.flow_facts, sa.func.count()).where(s.flow_facts.c.kind == "landed")
            ).scalar_one() == int(omit)
            assert conn.execute(waiting.select(s.artifacts, sa.func.count())).scalar_one() == int(omit)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
