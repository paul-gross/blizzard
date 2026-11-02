"""Restart and direct answers share first-write-wins under concurrent admission."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from types import SimpleNamespace
from typing import cast

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal import chunk_questions_store
from blizzard.hub.store.internal.chunk_movement_store import ChunkMovementStore
from blizzard.hub.store.internal.chunk_questions_store import ChunkQuestionsStore
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("order", ["direct", "restart", "omission", "locked"])
def test_restart_answer_race(database_url: str, order: str, monkeypatch: pytest.MonkeyPatch) -> None:
    restart_engine = sa.create_engine(database_url, connect_args={"options": "-c statement_timeout=8000"})
    direct_engine = sa.create_engine(
        database_url, connect_args={"application_name": "answer_waiter", "options": "-c statement_timeout=8000"}
    )
    observer = sa.create_engine(database_url)
    tenant = TenantId("ten_restart_answer")
    scope = scoped_connections(restart_engine, tenant)
    clock = FixedClock(_AT)
    reached, release = Event(), Event()
    try:
        with scope.founding("found", name="restart-answer", created_at=_AT, created_by="test"):
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
                scope.insert(s.questions).values(
                    question_id="question",
                    chunk_id="chunk",
                    project_id="project",
                    runner_id="runner",
                    epoch=1,
                    question="?",
                    options="[]",
                    asked_at=_AT,
                )
            )

        movement = ChunkMovementStore(scope, clock)
        direct = ChunkQuestionsStore(scoped_connections(direct_engine, tenant), clock)

        def shared_admission(store, conn):
            conn.execute(
                sa.select(s.tenants.c.tenant_id)
                .where(s.tenants.c.tenant_id == store.scope.tenant)
                .with_for_update(read=True)
            ).one()

        monkeypatch.setattr(ScopedConnections, "_lock_open_tenant", shared_admission)
        # Exercise the answer-row CAS/savepoint independently of the chunk lock.
        if order != "locked":
            monkeypatch.setattr(chunk_questions_store, "lock_chunk_row", lambda conn, chunk_id: None)
        if order == "omission":
            monkeypatch.setattr(sa.Connection, "begin_nested", lambda self: nullcontext())

        @event.listens_for(restart_engine, "after_cursor_execute")
        def pause(conn, cursor, statement, parameters, context, executemany):
            sql = statement.lower().lstrip()
            target = "insert into question_answers" if order == "restart" else "select question_answers.question_id"
            if sql.startswith(target) and not reached.is_set():
                reached.set()
                assert release.wait(8)

        def restart():
            with scope.write("restart") as conn:
                lock_chunk_row(conn, "chunk")
                handle = cast(ILockedChunkRead, SimpleNamespace(conn=conn))
                return movement.record_restart_locked(
                    handle,
                    "chunk",
                    from_node_id="n",
                    to_node_id="n",
                    by="restart",
                    at=_AT,
                    answered_question_ids=["question"],
                    answer="automatic",
                )

        def answer():
            return direct.answer_question("question", answer="direct", answered_by="person", at=_AT)

        with ThreadPoolExecutor(max_workers=2) as pool:
            restarted = pool.submit(restart)
            try:
                assert reached.wait(8), "restart did not reach the answer race"
                answered = pool.submit(answer)
                if order in {"direct", "omission"}:
                    assert answered.result(timeout=5).won
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                    "WHERE application_name = 'answer_waiter' AND cardinality(pg_blocking_pids(pid)) > 0)"
                                )
                            ).scalar_one()
                            if blocked:
                                break
                            assert not answered.done(), "direct answer did not wait for restart"
                            Event().wait(0.02)
                        else:
                            pytest.fail("direct answer did not block")
            finally:
                release.set()
            if order == "omission":
                with pytest.raises(HubStoreError):
                    restarted.result(timeout=10)
            else:
                restarted.result(timeout=10)
            outcome = answered.result(timeout=10)
        assert outcome.won is (order in {"direct", "omission"})
        assert outcome.answer == ("automatic" if order in {"restart", "locked"} else "direct")
        with scope.read("verify") as conn:
            answers = conn.execute(scope.select(s.question_answers)).all()
            restarts = conn.execute(scope.select(s.chunk_restarts)).all()
        assert [(row.answer, row.answered_by) for row in answers] == [
            (outcome.answer, "restart" if order in {"restart", "locked"} else "person")
        ]
        assert len(restarts) == (0 if order == "omission" else 1)
        if order == "direct":
            with pytest.raises(HubStoreError), scope.write("invalid_restart") as conn:
                lock_chunk_row(conn, "chunk")
                handle = cast(ILockedChunkRead, SimpleNamespace(conn=conn))
                movement.record_restart_locked(
                    handle,
                    "chunk",
                    from_node_id="n",
                    to_node_id="n",
                    by="restart",
                    at=_AT,
                    answered_question_ids=["missing"],
                    answer="automatic",
                )
            with scope.read("verify_invalid") as conn:
                assert len(conn.execute(scope.select(s.chunk_restarts)).all()) == 1
    finally:
        release.set()
        restart_engine.dispose()
        direct_engine.dispose()
        observer.dispose()
