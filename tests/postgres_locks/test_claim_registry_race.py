"""Claim eligibility and its competing writers serialize on the registry key."""

import json
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.foundation.runner_version import VersionRefusalReason
from blizzard.hub.domain.execution.claim import (
    ClaimDeniedIncompatible,
    ClaimDeniedPaused,
    ClaimDeniedVersionRefused,
    ClaimService,
)
from blizzard.hub.domain.graph.model import Node
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store, runner_registry_store
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.host_registry_store import lock_host_registry
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _wait_for_block(observer: sa.Engine, future: Future) -> None:
    deadline = monotonic() + 5
    with observer.connect() as watch:
        while monotonic() < deadline:
            blocked = watch.execute(
                sa.text("""
                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                WHERE w.application_name='eligibility_waiter' AND h.application_name='eligibility_holder')
            """)
            ).scalar_one()
            if blocked:
                return
            assert not future.done()
            Event().wait(0.02)
    pytest.fail("operation did not wait on the registry blocker")


@pytest.mark.parametrize("change", ["capabilities", "projects", "version", "pause"])
@pytest.mark.parametrize("mode", ["commit", "rollback", "omit"])
def test_claim_reads_registry_after_blocker(
    database_url: str, change: str, mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "eligibility_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "eligibility_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    scope = scoped_connections(waiter, TenantId("ten_eligibility"))
    read_guard = Event()
    try:
        with scope.founding("found", name="claim", created_at=_AT, created_by="test"):
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
        registry = RunnerRegistryStore(scope)
        stores = build_chunk_stores(scope, FixedClock(_AT), registry=registry)
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
            clock=FixedClock(_AT),
            label=lambda ref: ref.ref,
        )
        original = chunk_exclusive_store.LockedChunkTransaction.runner_registration

        def observed(handle, runner_id):
            registration = original(handle, runner_id)
            read_guard.set()
            return registration

        monkeypatch.setattr(chunk_exclusive_store.LockedChunkTransaction, "runner_registration", observed)
        if mode == "omit":
            monkeypatch.setattr(chunk_exclusive_store, "lock_host_registry", lambda conn: None)

        holder_scope = scoped_connections(holder, TenantId("ten_eligibility"))
        with ThreadPoolExecutor(max_workers=1) as pool, holder_scope.read("blocker") as conn:
            lock_host_registry(conn)
            if change == "pause":
                conn.execute(
                    holder_scope.insert(s.runner_pause_facts).values(
                        runner_id="runner", paused=True, set_at=_AT, set_by="test"
                    )
                )
            else:
                values = {
                    "capabilities": {"capabilities": None},
                    "projects": {"projects": "[]"},
                    "version": {"version_refusal": VersionRefusalReason.TOO_OLD.value},
                }[change]
                conn.execute(
                    holder_scope.update(s.runner_registrations)
                    .where(s.runner_registrations.c.runner_id == "runner")
                    .values(**values)
                )
            future = pool.submit(
                service.claim, chunk, graph, runner_id="runner", workspace_id="workspace", environment_ids=[]
            )
            try:
                if mode == "omit":
                    assert read_guard.wait(5), "claim did not read the stale registration"
                else:
                    _wait_for_block(observer, future)
                    assert not read_guard.is_set()
            finally:
                if mode == "rollback":
                    conn.rollback()
                else:
                    conn.commit()
            if mode == "commit":
                error = {"pause": ClaimDeniedPaused, "version": ClaimDeniedVersionRefused}.get(
                    change, ClaimDeniedIncompatible
                )
                with pytest.raises(error):
                    future.result(timeout=10)
                assert stores.route.route_of("chunk") is None
            else:
                assert future.result(timeout=10).route.runner_id == "runner"
                assert stores.route.route_of("chunk") is not None
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("writer", ["pause", "registration", "version"])
@pytest.mark.parametrize("omit", [False, True])
def test_eligibility_writers_take_registry_key(
    database_url: str, writer: str, omit: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "eligibility_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "eligibility_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    scope = scoped_connections(holder, TenantId("ten_writer"))
    try:
        with scope.founding("found", name="writer", created_at=_AT, created_by="test"):
            pass
        with scope.write("seed") as conn:
            conn.execute(scope.insert(s.runner_registrations).values(runner_id="runner", name="runner", added_at=_AT))
        registry = RunnerRegistryStore(scoped_connections(waiter, TenantId("ten_writer")))
        if omit:
            monkeypatch.setattr(runner_registry_store, "lock_host_registry", lambda conn: None)

        def write() -> None:
            if writer == "pause":
                registry.record_pause("runner", paused=True, at=_AT, by="test")
            elif writer == "registration":
                registry.record_registration("runner", workspace_id="w", env_capacity=None, at=_AT)
            else:
                registry.record_version_refusal("runner", version="old", reason=VersionRefusalReason.TOO_OLD, at=_AT)

        with ThreadPoolExecutor(max_workers=1) as pool, scope.read("blocker") as conn:
            lock_host_registry(conn)
            future = pool.submit(write)
            try:
                if omit:
                    future.result(timeout=5)
                else:
                    _wait_for_block(observer, future)
            finally:
                conn.commit()
            future.result(timeout=10)
        runner = registry.get_runner("runner")
        assert runner is not None
        assert {
            "pause": runner.hub_paused,
            "registration": runner.registered_at is not None,
            "version": runner.version_refusal is not None,
        }[writer]
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
