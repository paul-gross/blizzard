"""A fenced writer must wait for a concurrent stop or restart before deciding."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event
from time import monotonic
from types import SimpleNamespace
from typing import cast

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission, FenceRefusal
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_questions_store
from blizzard.hub.store.internal.chunk_lifecycle_store import ChunkLifecycleStore
from blizzard.hub.store.internal.chunk_movement_store import ChunkMovementStore
from blizzard.hub.store.internal.chunk_questions_store import ChunkQuestionsStore
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("guard", ["stop", "restart"])
@pytest.mark.parametrize("omit_lock", [False, True], ids=["fenced", "lock-omitted"])
def test_question_writer_rechecks_concurrent_guard(database_url, monkeypatch, guard, omit_lock):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "epoch_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "epoch_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    scope = scoped_connections(holder, TenantId("ten_epoch"))
    clock = FixedClock(now)
    try:
        with scope.founding("found", name="epoch", created_at=now, created_by="test"):
            pass
        with scope.write("seed") as conn:
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=now
                )
            )
            conn.execute(
                scope.insert(s.projects).values(
                    project_id="project", slug="p", name="p", description="", revision=1, color="blue", created_at=now
                )
            )
            conn.execute(
                scope.insert(s.chunks).values(chunk_id="chunk", graph_id="graph", project_id="project", minted_at=now)
            )
            conn.execute(
                scope.insert(s.lease_facts).values(chunk_id="chunk", epoch=1, runner_id="runner", minted_at=now)
            )
        writer = ChunkQuestionsStore(scoped_connections(waiter, TenantId("ten_epoch")), clock)
        if omit_lock:
            monkeypatch.setattr(chunk_questions_store, "lock_chunk_row", lambda conn, chunk_id: None)

        def write():
            return writer.record_question(
                question_id="question",
                chunk_id="chunk",
                node_id="n",
                session_id=None,
                runner_id="runner",
                epoch=1,
                admission=EpochAdmission.CURRENT,
                question="What next?",
                options=[],
                asked_at=now,
            )

        with ThreadPoolExecutor(max_workers=1) as actors:
            # A direct transaction deliberately does not acquire tenant write admission.
            with scope.read("guard_without_admission") as conn:
                lock_chunk_row(conn, "chunk")
                handle = cast(ILockedChunkRead, SimpleNamespace(conn=conn))
                if guard == "stop":
                    ChunkLifecycleStore(scope).record_stop_locked(handle, "chunk", by="operator", at=now)
                else:
                    ChunkMovementStore(scope, clock).record_restart_locked(
                        handle,
                        "chunk",
                        from_node_id="n",
                        to_node_id="n",
                        by="operator",
                        at=now + timedelta(seconds=1),
                    )
                future = actors.submit(write)
                if omit_lock:
                    assert future.result(timeout=5) is None
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name = 'epoch_waiter' AND h.application_name = 'epoch_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "writer bypassed the chunk lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("PostgreSQL did not report the expected chunk-row wait")
                conn.commit()
            result = future.result(timeout=10)
        if not omit_lock:
            assert result == (FenceRefusal.terminal(1) if guard == "stop" else FenceRefusal.stale(1, latest=2))
        with scope.read("verify") as conn:
            questions = conn.execute(scope.select(s.questions)).all()
            facts = conn.execute(scope.select(s.chunk_stopped if guard == "stop" else s.chunk_restarts)).all()
        assert len(facts) == 1
        assert [row.question_id for row in questions] == (["question"] if omit_lock else [])
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
