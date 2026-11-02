"""Decision locks are observable without a competing tenant-admission lock."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import SystemClock
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.delivery.flows import FlowLanding, FlowResource, Submission, TurnEvent
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import flow_rows, flow_store
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row, lock_keys
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 27, tzinfo=UTC)
_TENANT = TenantId("ten_flow_lock")
_RESOURCE = FlowResource("repo_a", "main")
_EVENT = TurnEvent(kind="turn-interrupted", chunk_id="ch_1", node_name="deliver", message="ended", detail={})


def _seed(scoped: ScopedConnections) -> None:
    with scoped.founding("found", name="flow-test", created_at=_AT, created_by="test"):
        pass
    with scoped.write("seed") as conn:
        conn.execute(
            scoped.insert(s.graphs).values(
                graph_id="gr_1", name="graph", entry_node_id="nd_1", definition_yaml="", created_at=_AT
            )
        )
        conn.execute(
            scoped.insert(s.projects).values(
                project_id="proj_1",
                slug="project",
                name="project",
                description="",
                revision=1,
                created_at=_AT,
                color="blue",
            )
        )
        conn.execute(
            scoped.insert(s.chunks).values(chunk_id="ch_1", graph_id="gr_1", project_id="proj_1", minted_at=_AT)
        )


@pytest.fixture
def clean_flow_database(database_url: str) -> str:
    return database_url


def test_locked_submit_rechecks_latest_generation(database_url: str) -> None:
    engine = sa.create_engine(database_url)
    scoped = scoped_connections(engine, _TENANT)
    flows = flow_store.FlowStore(scoped, SystemClock())
    try:
        _seed(scoped)

        def submit(generation: int) -> Submission | None:
            with scoped.write("locked_submit") as conn:
                lock_chunk_row(conn, "ch_1")
                handle = cast(ILockedChunkRead, SimpleNamespace(conn=conn, locked_ids=frozenset({"ch_1"})))
                return flows.submit_locked(
                    handle, "ch_1", node_id="nd_1", epoch=1, generation=generation, resources=[], at=_AT
                )

        def next_generation() -> int | None:
            with scoped.write("generation") as conn:
                lock_chunk_row(conn, "ch_1")
                handle = cast(ILockedChunkRead, SimpleNamespace(conn=conn, locked_ids=frozenset({"ch_1"})))
                return flows.generation_locked(handle, "ch_1", node_id="nd_1", epoch=1)

        assert next_generation() == 0
        assert submit(1) is None
        first = submit(0)
        assert first is not None
        assert next_generation() is None
        assert submit(1) is None
        assert flows.close(first.submission_id, outcome="superseded", at=_AT) is not None
        assert next_generation() == 1
        assert submit(0) is None
        second = submit(1)
        assert second is not None
        assert flows.close(second.submission_id, outcome="stopped", at=_AT) is not None
        assert next_generation() is None
        assert submit(2) is None
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "operation,namespace",
    [
        ("submit", "flow-chunk"),
        ("submit", "flow-arrival"),
        ("submit", "flow"),
        ("close", "flow-chunk"),
        ("close", "flow"),
        ("take", "flow-chunk"),
        ("take", "flow"),
        ("end", "flow-chunk"),
        ("end", "flow"),
        ("locked-end", "flow-chunk"),
        ("locked-end", "flow"),
        ("withdraw", "flow-chunk"),
        ("withdraw", "flow"),
        ("landing", "flow-chunk"),
        ("landing", "flow"),
        ("free-take", "flow-chunk"),
    ],
)
@pytest.mark.parametrize("omit", [False, True], ids=["lock", "negative-control"])
def test_flow_decision_locks(clean_flow_database: str, operation: str, namespace: str, omit: bool) -> None:
    database_url = clean_flow_database
    options = "-c statement_timeout=8000"
    engine = sa.create_engine(database_url, connect_args={"application_name": "flow_waiter", "options": options})
    holder = sa.create_engine(database_url, connect_args={"application_name": "flow_holder", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    scoped = scoped_connections(engine, _TENANT)
    flows = flow_store.FlowStore(scoped, SystemClock())
    try:
        _seed(scoped)
        resources = [] if operation == "free-take" else [_RESOURCE]
        submission = flows.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=resources, at=_AT)
        assert submission is not None
        turn = None
        if operation in {"end", "locked-end"}:
            turn = flows.take_turn(submission.submission_id, passed_over=frozenset(), terms=(), at=_AT)
            assert turn is not None

        key = {"flow-chunk": "ch_1", "flow-arrival": "arrival", "flow": _RESOURCE.resource}[namespace]

        def actor() -> object:
            if operation == "submit":
                return flows.submit("ch_1", node_id="nd_1", epoch=1, generation=1, resources=[_RESOURCE], at=_AT)
            if operation == "close":
                return flows.close(submission.submission_id, outcome="stopped", at=_AT)
            if operation in {"take", "free-take"}:
                return flows.take_turn(submission.submission_id, passed_over=frozenset(), terms=(), at=_AT)
            if operation == "end":
                assert turn is not None
                return flows.end_turn_interrupted(turn.turn_id, withdrawal="stopped", event=_EVENT, at=_AT)
            if operation == "landing":
                landing = FlowLanding.of(
                    submission.flows[0], submission, project_id="proj_1", node_name="deliver", revision="m1"
                )
                return flows.record_landing(
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
            with scoped.write("flow_test") as conn:
                if operation == "locked-end":
                    assert turn is not None
                    lock_chunk_row(conn, "ch_1")
                    return flows.end_turn_locked(
                        cast(ILockedChunkRead, SimpleNamespace(conn=conn, locked_ids=frozenset({"ch_1"}))),
                        turn.turn_id,
                        outcome="interrupted",
                        closure="stopped",
                        event=_EVENT,
                        at=_AT,
                    )
                lock_chunk_row(conn, "ch_1")
                return flow_rows.withdraw_waiting(conn, "ch_1", outcome="stopped", at=_AT)

        def selective_lock(conn: sa.Connection, name: str, keys: list[str]) -> None:
            if not (omit and name == namespace):
                lock_keys(conn, name, keys)

        with holder.begin() as conn:
            result = conn.execute(
                sa.text(
                    "UPDATE keyed_locks SET key = key WHERE tenant_id = :tenant AND namespace = :namespace AND key = :key"
                ),
                {"tenant": str(_TENANT), "namespace": namespace, "key": key},
            )
            assert result.rowcount == 1
            with (
                patch.object(flow_store, "lock_keys", selective_lock),
                patch.object(flow_rows, "lock_keys", selective_lock),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                future = pool.submit(actor)
                try:
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                            blocked = watch.execute(
                                sa.text("""
                                    SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiter
                                    JOIN pg_stat_activity AS holder ON holder.pid = ANY(pg_blocking_pids(waiter.pid))
                                    WHERE waiter.application_name = 'flow_waiter' AND holder.application_name = 'flow_holder')
                                """)
                            ).scalar_one()
                            if blocked or future.done():
                                break
                            Event().wait(0.02)
                        else:
                            pytest.fail("actor neither completed nor blocked")
                    assert blocked is not omit
                finally:
                    conn.commit()
                assert future.result(timeout=10) is not None
    finally:
        engine.dispose()
        holder.dispose()
        observer.dispose()


@pytest.mark.parametrize(
    "first,second",
    [
        ("submit", "submit"),
        ("take", "take"),
        ("take", "close"),
        ("close", "take"),
        ("end", "end"),
    ],
)
def test_competing_flow_decisions_without_tenant_admission(clean_flow_database: str, first: str, second: str) -> None:
    options = "-c statement_timeout=8000"
    a = sa.create_engine(clean_flow_database, connect_args={"application_name": "race_first", "options": options})
    b = sa.create_engine(clean_flow_database, connect_args={"application_name": "race_second", "options": options})
    observer = sa.create_engine(clean_flow_database, connect_args={"options": options})
    sa_scope, sb_scope = scoped_connections(a, _TENANT), scoped_connections(b, _TENANT)
    first_store, second_store = (
        flow_store.FlowStore(sa_scope, SystemClock()),
        flow_store.FlowStore(sb_scope, SystemClock()),
    )
    entered, release = Event(), Event()
    try:
        _seed(sa_scope)
        sub = None
        turn = None
        if first != "submit":
            sub = first_store.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[], at=_AT)
            assert sub is not None
        if first == "end":
            assert sub is not None
            turn = first_store.take_turn(sub.submission_id, passed_over=frozenset(), terms=(), at=_AT)
            assert turn is not None

        def call(store: flow_store.FlowStore, action: str) -> object:
            if action == "submit":
                return store.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[], at=_AT)
            assert sub is not None
            if action == "take":
                return store.take_turn(sub.submission_id, passed_over=frozenset(), terms=(), at=_AT)
            if action == "close":
                return store.close(sub.submission_id, outcome="stopped", at=_AT)
            assert turn is not None
            return store.end_turn_interrupted(turn.turn_id, withdrawal="stopped", event=_EVENT, at=_AT)

        real_lock = flow_rows.lock_flow_chunk

        def hold_first(conn: sa.Connection, chunk_id: str) -> None:
            real_lock(conn, chunk_id)
            if conn.engine is a:
                entered.set()
                assert release.wait(10), "first actor timed out"

        with (
            patch.object(sa_scope, "_lock_open_tenant"),
            patch.object(sb_scope, "_lock_open_tenant"),
            patch.object(flow_store, "lock_flow_chunk", hold_first),
            patch.object(flow_rows, "lock_flow_chunk", hold_first),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            one = pool.submit(call, first_store, first)
            try:
                assert entered.wait(10), "first actor did not hold decision lock"
                two = pool.submit(call, second_store, second)
                deadline = monotonic() + 5
                with observer.connect() as conn:
                    while monotonic() < deadline:
                        conn.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = conn.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiter
                            JOIN pg_stat_activity AS holder ON holder.pid = ANY(pg_blocking_pids(waiter.pid))
                            WHERE waiter.application_name = 'race_second' AND holder.application_name = 'race_first')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not two.done(), "competitor completed before the first decision"
                        Event().wait(0.02)
                    else:
                        pytest.fail("competing decision did not block")
            finally:
                release.set()
            assert one.result(timeout=10) is not None
            assert two.result(timeout=10) is None
    finally:
        a.dispose()
        b.dispose()
        observer.dispose()


@pytest.mark.parametrize("operation", ["submit", "end"])
def test_chunk_row_precedes_flow_key_without_tenant_admission(clean_flow_database: str, operation: str) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(
        clean_flow_database, connect_args={"application_name": "order_holder", "options": options}
    )
    waiter = sa.create_engine(
        clean_flow_database, connect_args={"application_name": "order_waiter", "options": options}
    )
    observer = sa.create_engine(clean_flow_database, connect_args={"options": options})
    held_scope, wait_scope = scoped_connections(holder, _TENANT), scoped_connections(waiter, _TENANT)
    flows = flow_store.FlowStore(wait_scope, SystemClock())
    try:
        _seed(held_scope)
        turn = None
        if operation == "end":
            sub = flows.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[], at=_AT)
            assert sub is not None
            turn = flows.take_turn(sub.submission_id, passed_over=frozenset(), terms=(), at=_AT)
            assert turn is not None

        def act() -> object:
            if operation == "submit":
                return flows.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[], at=_AT)
            assert turn is not None
            return flows.end_turn_interrupted(turn.turn_id, withdrawal="stopped", event=_EVENT, at=_AT)

        with (
            patch.object(held_scope, "_lock_open_tenant"),
            patch.object(wait_scope, "_lock_open_tenant"),
            held_scope.write("order_holder") as conn,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            lock_chunk_row(conn, "ch_1")
            future = pool.submit(act)
            try:
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = watch.execute(
                            sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiter
                                JOIN pg_stat_activity AS holder ON holder.pid = ANY(pg_blocking_pids(waiter.pid))
                                WHERE waiter.application_name = 'order_waiter' AND holder.application_name = 'order_holder')
                            """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not future.done(), "flow write did not wait for the chunk row"
                        Event().wait(0.02)
                    else:
                        pytest.fail("flow write did not wait for the chunk row")
                # The waiter cannot hold this key while waiting for the row we hold.
                flow_rows.lock_flow_chunk(conn, "ch_1")
            finally:
                conn.commit()
            assert future.result(timeout=10) is not None
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("first", ["take", "withdraw"])
def test_withdraw_and_take_serialize_without_tenant_admission(clean_flow_database: str, first: str) -> None:
    options = "-c statement_timeout=8000"
    a = sa.create_engine(clean_flow_database, connect_args={"application_name": "lever_first", "options": options})
    b = sa.create_engine(clean_flow_database, connect_args={"application_name": "lever_second", "options": options})
    observer = sa.create_engine(clean_flow_database, connect_args={"options": options})
    first_scope, second_scope = scoped_connections(a, _TENANT), scoped_connections(b, _TENANT)
    first_store, second_store = (
        flow_store.FlowStore(first_scope, SystemClock()),
        flow_store.FlowStore(second_scope, SystemClock()),
    )
    entered, release = Event(), Event()
    try:
        _seed(first_scope)
        sub = first_store.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT)
        assert sub is not None

        def act(scope: ScopedConnections, store: flow_store.FlowStore, action: str) -> object:
            if action == "take":
                return store.take_turn(sub.submission_id, passed_over=frozenset(), terms=(), at=_AT)
            with scope.write("lever") as conn:
                lock_chunk_row(conn, "ch_1")
                return flow_rows.withdraw_waiting(conn, "ch_1", outcome="stopped", at=_AT)

        real_lock = flow_rows.lock_flow_chunk

        def hold_first(conn: sa.Connection, chunk_id: str) -> None:
            real_lock(conn, chunk_id)
            if conn.engine is a:
                entered.set()
                assert release.wait(10)

        with (
            patch.object(first_scope, "_lock_open_tenant"),
            patch.object(second_scope, "_lock_open_tenant"),
            patch.object(flow_rows, "lock_flow_chunk", hold_first),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            one = pool.submit(act, first_scope, first_store, first)
            try:
                assert entered.wait(10)
                two = pool.submit(act, second_scope, second_store, "withdraw" if first == "take" else "take")
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = watch.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiter
                            JOIN pg_stat_activity AS holder ON holder.pid = ANY(pg_blocking_pids(waiter.pid))
                            WHERE waiter.application_name = 'lever_second' AND holder.application_name = 'lever_first')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not two.done()
                        Event().wait(0.02)
                    else:
                        pytest.fail("competing lever and turn did not block")
            finally:
                release.set()
            first_result, second_result = one.result(timeout=10), two.result(timeout=10)
        if first == "take":
            assert first_result is not None and second_result == ()
            assert len(first_store.open_turns()) == 1
            submission = first_store.submission_for_visit("ch_1", node_id="nd_1", epoch=1)
            assert submission is not None
            assert submission.closed is None
        else:
            assert first_result and second_result is None
            assert first_store.open_turns() == []
            submission = first_store.submission_for_visit("ch_1", node_id="nd_1", epoch=1)
            assert submission is not None
            assert submission.closed == "stopped"
    finally:
        a.dispose()
        b.dispose()
        observer.dispose()


def test_shared_resource_competitors_take_only_one_turn(clean_flow_database: str) -> None:
    options = "-c statement_timeout=8000"
    a = sa.create_engine(clean_flow_database, connect_args={"application_name": "resource_first", "options": options})
    b = sa.create_engine(clean_flow_database, connect_args={"application_name": "resource_second", "options": options})
    observer = sa.create_engine(clean_flow_database, connect_args={"options": options})
    first_scope, second_scope = scoped_connections(a, _TENANT), scoped_connections(b, _TENANT)
    first_store = flow_store.FlowStore(first_scope, SystemClock())
    second_store = flow_store.FlowStore(second_scope, SystemClock())
    entered, release = Event(), Event()
    try:
        _seed(first_scope)
        with first_scope.write("seed_second") as conn:
            conn.execute(
                first_scope.insert(s.chunks).values(
                    chunk_id="ch_2", graph_id="gr_1", project_id="proj_1", minted_at=_AT
                )
            )
        first = first_store.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT)
        second = first_store.submit("ch_2", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT)
        assert first is not None and second is not None

        def hold_resource(conn: sa.Connection, name: str, keys: list[str]) -> None:
            lock_keys(conn, name, keys)
            if name == "flow" and conn.engine is a:
                entered.set()
                assert release.wait(10), "resource holder timed out"

        with (
            patch.object(first_scope, "_lock_open_tenant"),
            patch.object(second_scope, "_lock_open_tenant"),
            patch.object(flow_rows, "lock_keys", hold_resource),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            one = pool.submit(first_store.take_turn, first.submission_id, passed_over=frozenset(), terms=(), at=_AT)
            try:
                assert entered.wait(10)
                two = pool.submit(
                    second_store.take_turn,
                    second.submission_id,
                    passed_over=frozenset({first.submission_id}),
                    terms=(),
                    at=_AT,
                )
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = watch.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiter
                            JOIN pg_stat_activity AS holder ON holder.pid = ANY(pg_blocking_pids(waiter.pid))
                            WHERE waiter.application_name = 'resource_second' AND holder.application_name = 'resource_first')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not two.done(), "second resource decision did not wait"
                        Event().wait(0.02)
                    else:
                        pytest.fail("second resource decision did not block")
            finally:
                release.set()
            assert one.result(timeout=10) is not None
            assert two.result(timeout=10) is None
        assert len(first_store.open_turns()) == 1
    finally:
        a.dispose()
        b.dispose()
        observer.dispose()


def test_arrivals_are_allocated_once_across_chunks_without_tenant_admission(clean_flow_database: str) -> None:
    options = "-c statement_timeout=8000"
    a = sa.create_engine(clean_flow_database, connect_args={"application_name": "arrival_first", "options": options})
    b = sa.create_engine(clean_flow_database, connect_args={"application_name": "arrival_second", "options": options})
    observer = sa.create_engine(clean_flow_database, connect_args={"options": options})
    first_scope, second_scope = scoped_connections(a, _TENANT), scoped_connections(b, _TENANT)
    first_store = flow_store.FlowStore(first_scope, SystemClock())
    second_store = flow_store.FlowStore(second_scope, SystemClock())
    entered, release = Event(), Event()
    try:
        _seed(first_scope)
        with first_scope.write("seed_second") as conn:
            conn.execute(
                first_scope.insert(s.chunks).values(
                    chunk_id="ch_2", graph_id="gr_1", project_id="proj_1", minted_at=_AT
                )
            )

        def hold_arrival(conn: sa.Connection, name: str, keys: list[str]) -> None:
            lock_keys(conn, name, keys)
            if name == "flow-arrival" and conn.engine is a:
                entered.set()
                assert release.wait(10), "arrival holder timed out"

        with (
            patch.object(first_scope, "_lock_open_tenant"),
            patch.object(second_scope, "_lock_open_tenant"),
            patch.object(flow_store, "lock_keys", hold_arrival),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            one = pool.submit(first_store.submit, "ch_1", node_id="nd_1", epoch=1, generation=0, resources=[], at=_AT)
            try:
                assert entered.wait(10)
                two = pool.submit(
                    second_store.submit, "ch_2", node_id="nd_1", epoch=1, generation=0, resources=[], at=_AT
                )
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = watch.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity AS waiter
                            JOIN pg_stat_activity AS holder ON holder.pid = ANY(pg_blocking_pids(waiter.pid))
                            WHERE waiter.application_name = 'arrival_second' AND holder.application_name = 'arrival_first')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not two.done(), "second arrival did not wait"
                        Event().wait(0.02)
                    else:
                        pytest.fail("second arrival did not block")
            finally:
                release.set()
            first, second = one.result(timeout=10), two.result(timeout=10)
            assert first is not None and second is not None
            assert (first.arrival, second.arrival) == (1, 2)
    finally:
        a.dispose()
        b.dispose()
        observer.dispose()


@pytest.mark.parametrize("operation", ["landing", "locked-end"])
def test_mismatched_chunk_never_waits_for_submission_chunk(database_url: str, operation: str) -> None:
    options = "-c statement_timeout=8000"
    engine = sa.create_engine(database_url, connect_args={"options": options})
    holder = sa.create_engine(database_url, connect_args={"options": options})
    scoped = scoped_connections(engine, _TENANT)
    held_scope = scoped_connections(holder, _TENANT)
    flows = flow_store.FlowStore(scoped, SystemClock())
    try:
        _seed(scoped)
        with scoped.write("second_chunk") as conn:
            conn.execute(
                scoped.insert(s.chunks).values(chunk_id="ch_2", graph_id="gr_1", project_id="proj_1", minted_at=_AT)
            )
        sub = flows.submit("ch_2", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT)
        assert sub is not None
        turn = flows.take_turn(sub.submission_id, passed_over=frozenset(), terms=(), at=_AT)
        assert turn is not None
        landing = replace(
            FlowLanding.of(sub.flows[0], sub, project_id="proj_1", node_name="deliver", revision="m1"), chunk_id="ch_1"
        )

        def act() -> None:
            with pytest.raises(ValueError, match="chunk"):
                if operation == "landing":
                    flows.record_landing(
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
                else:
                    with scoped.write("wrong_handle") as conn:
                        lock_chunk_row(conn, "ch_1")
                        flows.end_turn_locked(
                            cast(ILockedChunkRead, SimpleNamespace(conn=conn, locked_ids=frozenset({"ch_1"}))),
                            turn.turn_id,
                            outcome="interrupted",
                            closure="stopped",
                            event=_EVENT,
                            at=_AT,
                        )

        with (
            patch.object(scoped, "_lock_open_tenant"),
            patch.object(held_scope, "_lock_open_tenant"),
            held_scope.write("hold_other_chunk") as conn,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            lock_chunk_row(conn, "ch_2")
            pool.submit(act).result(timeout=3)
        assert [t.turn_id for t in flows.open_turns()] == [turn.turn_id]
        assert [f.kind for f in flows.facts_of(sub.submission_id)] == ["submitted", "turn-started"]
        with scoped.read("verify") as conn:
            for table in (
                s.artifacts,
                s.delivery_repo_landed,
                s.close_intents,
                s.event_log,
                s.flow_submission_closures,
            ):
                assert conn.execute(scoped.select(table)).first() is None
    finally:
        engine.dispose()
        holder.dispose()
