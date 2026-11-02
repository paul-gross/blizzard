"""Direct answers serialize with claim's status guard on the chunk row."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa
from sqlalchemy import event

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.execution.claim import ClaimDeniedNotReady, ClaimService
from blizzard.hub.domain.graph.model import Node
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_questions_store
from blizzard.hub.store.internal.chunk_questions_store import ChunkQuestionsStore
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import TenantId
from tests.support import make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("omit", [False, True], ids=["locked", "omission"])
def test_claim_status_guard_races_direct_answer(database_url: str, omit: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    options = "-c statement_timeout=8000"
    claim_engine = sa.create_engine(
        database_url, connect_args={"application_name": "question_claim", "options": options}
    )
    answer_engine = sa.create_engine(
        database_url, connect_args={"application_name": "question_answer", "options": options}
    )
    observer = sa.create_engine(database_url, connect_args={"options": options})
    tenant = TenantId("ten_claim_answer")
    scope = scoped_connections(claim_engine, tenant)
    reached, release = Event(), Event()
    try:
        with scope.founding("found", name="claim-answer", created_at=_AT, created_by="test"):
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
            conn.execute(scope.insert(s.chunk_promoted).values(chunk_id="chunk", promoted_at=_AT))
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
            conn.execute(
                scope.insert(s.runner_registrations).values(
                    runner_id="runner",
                    name="runner",
                    added_at=_AT,
                    registered_at=_AT,
                    last_seen_at=_AT,
                    workspace_id="workspace",
                    capabilities=json.dumps([{"harness_id": "claude_code", "default": True}]),
                    projects=json.dumps([{"entry": "p", "project_id": "project"}]),
                )
            )
        clock = FixedClock(_AT)
        registry = RunnerRegistryStore(scope)
        stores = build_chunk_stores(scope, clock, registry=registry)
        answer = ChunkQuestionsStore(scoped_connections(answer_engine, tenant), clock)
        chunk = stores.record.get("chunk")
        assert chunk is not None
        node = Node(
            node_id="n",
            graph_id="graph",
            name="build",
            executor=Executor.RUNNER,
            prompt="p",
            checks=[],
            produces=[],
            session=SessionMode.FRESH,
            judged_by=JudgedBy.WORKER,
            retries_max=None,
            retries_exhausted=None,
            choices=[],
        )
        graph = make_graph("graph", "g", entry_node_id="n", nodes=[node])
        service = ClaimService(
            route=stores.route,
            artifacts=stores.artifacts,
            graphs=GraphStore(scope),
            registry=registry,
            retired=RetiredRunnerGuard(registry=registry),
            exclusive=stores.exclusive,
            clock=clock,
            label=lambda ref: ref.ref,
        )

        # Do not let tenant admission mask the chunk-row dependency under test.
        def shared_admission(store, conn):
            conn.execute(
                sa.select(s.tenants.c.tenant_id)
                .where(s.tenants.c.tenant_id == store.scope.tenant)
                .with_for_update(read=True)
            ).one()

        monkeypatch.setattr(ScopedConnections, "_lock_open_tenant", shared_admission)
        if omit:
            monkeypatch.setattr(chunk_questions_store, "lock_chunk_row", lambda conn, chunk_id: None)

        @event.listens_for(claim_engine, "after_cursor_execute")
        def pause(conn, cursor, statement, parameters, context, executemany):
            if statement.lower().lstrip().startswith("update chunks set") and not reached.is_set():
                reached.set()
                assert release.wait(8)

        with ThreadPoolExecutor(max_workers=2) as pool:
            claim = pool.submit(
                service.claim, chunk, graph, runner_id="runner", workspace_id="workspace", environment_ids=["env"]
            )
            try:
                assert reached.wait(8)
                direct = pool.submit(answer.answer_question, "question", answer="yes", answered_by="person", at=_AT)
                if omit:
                    assert direct.result(timeout=5).won
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity w JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid)) WHERE w.application_name='question_answer' AND h.application_name='question_claim')"
                                )
                            ).scalar_one()
                            if blocked:
                                break
                            assert not direct.done(), "answer passed the claim lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("answer did not block on the claim's chunk")
            finally:
                release.set()
            if omit:
                assert claim.result(timeout=10).route.chunk_id == "chunk"
            else:
                with pytest.raises(ClaimDeniedNotReady) as denied:
                    claim.result(timeout=10)
                assert denied.value.status.value == "waiting_on_human"
            assert direct.result(timeout=10).won
        assert (stores.route.route_of("chunk") is not None) is omit
    finally:
        release.set()
        claim_engine.dispose()
        answer_engine.dispose()
        observer.dispose()
