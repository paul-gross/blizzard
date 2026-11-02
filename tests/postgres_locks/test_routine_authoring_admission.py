"""Standalone routine authoring observes graph changes committed ahead of its admission."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.routines import RoutineAuthoring, RoutineGraphUnresolvedError
from blizzard.hub.domain.garden.scopes import ScopeRegistry, ScopeSlug
from blizzard.hub.store import routine_authoring_participants, schema
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.routine_authoring_participants import RoutineAuthoringParticipants
from tests.postgres_locks.test_config_apply_unit import _wait_for_key
from tests.postgres_locks.test_project_landing_order import _seed
from tests.support import make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 29, tzinfo=UTC)


@pytest.mark.parametrize("omit_lock", [False, True], ids=["locked", "negative-control"])
def test_create_waits_for_graph_retirement(database_url: str, monkeypatch: pytest.MonkeyPatch, omit_lock: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "apply_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "apply_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        scope, project, _ = _seed(holder)
        graph = make_graph("gr_routine_guard", "routine-guard")
        GraphStore(scope).mint(graph, definition_yaml="", at=_AT)
        scoped = scoped_connections(waiter, scope.scope.tenant)
        clock = FixedClock(_AT)
        authoring = RoutineAuthoring(
            routines=RoutineStore(scoped),
            graphs=GraphStore(scoped),
            scope_registry=ScopeRegistry(scopes=ScopeStore(scoped), clock=clock),
            clock=clock,
            participants=RoutineAuthoringParticipants(scoped),
        )
        if omit_lock:
            monkeypatch.setattr(routine_authoring_participants, "lock_config_authoring", lambda conn: None)
        with ThreadPoolExecutor(max_workers=1) as pool, scope.read("retire_under_key") as conn:
            lock_config_authoring(conn)
            lock_keys(conn, "graph_name", [graph.name])
            conn.execute(
                scope.insert(schema.graph_lifecycle_facts).values(
                    graph_id=graph.graph_id, retired=True, set_at=_AT, set_by="test"
                )
            )
            future = pool.submit(
                authoring.create,
                project=project,
                name="nightly",
                graph_name=graph.name,
                default_scope_slug=ScopeSlug.parse("fresh"),
                ctx=ChangeContext("test", Door.API),
            )
            if omit_lock:
                assert future.result(timeout=5).graph_name == graph.name
            else:
                _wait_for_key(observer, future)
            conn.commit()
            if not omit_lock:
                with pytest.raises(RoutineGraphUnresolvedError):
                    future.result(timeout=10)
                assert RoutineStore(scoped).get_by_name(project.project_id, "nightly") is None
                assert ScopeStore(scoped).get(project.project_id, "fresh") is None
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
