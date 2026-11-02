"""Claim must judge dependency changes committed while it waits on PostgreSQL locks."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.execution.claim import ClaimDeniedDependency, ClaimService
from blizzard.hub.domain.graph.model import Node
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_dependencies_store import (
    DEPENDENCY_GRAPH_LOCK_KEY,
    DEPENDENCY_GRAPH_LOCK_NAMESPACE,
)
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("change", ["release", "complete"])
@pytest.mark.parametrize("commit", [True, False], ids=["committed", "rolled-back"])
def test_claim_races_dependency_change(database_url: str, change: str, commit: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "race_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "race_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    tenant = TenantId("ten_claim_race")
    scope = scoped_connections(waiter, tenant)
    clock = FixedClock(_AT)
    try:
        with scope.founding("found", name="claim race", created_at=_AT, created_by="test"):
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
            for chunk_id in ("dependent", "prerequisite"):
                conn.execute(
                    scope.insert(s.chunks).values(
                        chunk_id=chunk_id, graph_id="graph", project_id="project", minted_at=_AT
                    )
                )
            conn.execute(scope.insert(s.chunk_promoted).values(chunk_id="dependent", promoted_at=_AT))
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
        stores = build_chunk_stores(scope, clock, registry=registry)
        with stores.exclusive.locked_dependency_graph(["dependent", "prerequisite"]) as handle:
            stores.dependencies.declare_locked(handle, "dependent", "prerequisite", by="test", at=_AT)
        chunk = stores.record.get("dependent")
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

        with ThreadPoolExecutor(max_workers=1) as pool:
            with holder.connect() as conn:
                transaction = conn.begin()
                try:
                    if change == "release":
                        conn.execute(
                            sa.text(
                                "UPDATE keyed_locks SET key=key WHERE tenant_id=:tenant AND namespace=:namespace AND key=:key"
                            ),
                            {
                                "tenant": str(tenant),
                                "namespace": DEPENDENCY_GRAPH_LOCK_NAMESPACE,
                                "key": DEPENDENCY_GRAPH_LOCK_KEY,
                            },
                        )
                        conn.execute(
                            sa.text(
                                "UPDATE chunk_dependencies SET released_at=:at, released_by='test' WHERE tenant_id=:tenant"
                            ),
                            {"tenant": str(tenant), "at": _AT},
                        )
                    else:
                        conn.execute(
                            sa.text(
                                "UPDATE chunks SET chunk_id=chunk_id WHERE tenant_id=:tenant AND chunk_id='prerequisite'"
                            ),
                            {"tenant": str(tenant)},
                        )
                        conn.execute(
                            sa.text(
                                "INSERT INTO chunk_completed (tenant_id, chunk_id, completed_at, completed_by) VALUES (:tenant, 'prerequisite', :at, 'test')"
                            ),
                            {"tenant": str(tenant), "at": _AT},
                        )
                    future = pool.submit(
                        service.claim,
                        chunk,
                        graph,
                        runner_id="runner",
                        workspace_id="workspace",
                        environment_ids=["env"],
                    )
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity w JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid)) WHERE w.application_name='race_waiter' AND h.application_name='race_holder')"
                                )
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "claim passed the dependency lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("claim did not block on the dependency change")
                finally:
                    if commit:
                        transaction.commit()
                    else:
                        transaction.rollback()
            if commit:
                result = future.result(timeout=10)
                assert result.route.chunk_id == "dependent"
                assert result.route.runner_id == "runner"
                assert stores.route.route_of("dependent") is not None
            else:
                with pytest.raises(ClaimDeniedDependency) as denied:
                    future.result(timeout=10)
                assert denied.value.prerequisite_chunk_id == "prerequisite"
                assert stores.route.route_of("dependent") is None
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
