"""Routine-run decisions wait for configuration changes without tenant admission masking the key."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.routines import RoutineAuthoring, RoutineGraphUnresolvedError
from blizzard.hub.domain.garden.scopes import ScopeRegistry, ScopeSlug
from blizzard.hub.store import routine_run_participants, schema
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.routine_authoring_participants import RoutineAuthoringParticipants
from blizzard.hub.store.routine_run_participants import RoutineRunParticipants
from tests.postgres_locks.test_config_apply_unit import _wait_for_key
from tests.postgres_locks.test_project_landing_order import _seed
from tests.support import make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 29, tzinfo=UTC)


@pytest.mark.parametrize("omit_lock", [False, True], ids=["locked", "negative-control"])
def test_run_observes_retirement_after_config_key_wait(
    database_url: str, monkeypatch: pytest.MonkeyPatch, omit_lock: bool
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "apply_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "apply_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        scope, project, _ = _seed(holder)
        graph = make_graph("gr_run_guard", "run-guard")
        GraphStore(scope).mint(graph, definition_yaml="", at=_AT)
        clock = FixedClock(_AT)
        authoring = RoutineAuthoring(
            routines=RoutineStore(scope),
            graphs=GraphStore(scope),
            scope_registry=ScopeRegistry(scopes=ScopeStore(scope), clock=clock),
            clock=clock,
            participants=RoutineAuthoringParticipants(scope),
        )
        routine = authoring.create(
            project=project,
            name="nightly",
            graph_name=graph.name,
            default_scope_slug=ScopeSlug.parse("fresh"),
            ctx=ChangeContext("test", Door.API),
        )
        participants = RoutineRunParticipants(scoped_connections(waiter, scope.scope.tenant), clock)
        if omit_lock:
            monkeypatch.setattr(routine_run_participants, "lock_config_authoring", lambda conn: None)

        def run() -> str:
            with participants.open() as admitted:
                fresh_routine = admitted.get_routine(routine.routine_id)
                assert fresh_routine is not None
                fresh_scope = admitted.get_scope(fresh_routine.project_id, fresh_routine.default_scope_slug)
                assert fresh_scope is not None
                return admitted.run(
                    fresh_routine,
                    scope=fresh_scope,
                    mode=RunMode.FULL,
                    note=None,
                    author=WorkItemAuthor.user("test"),
                ).chunk_id

        with ThreadPoolExecutor(max_workers=1) as pool, scope.read("retirement") as conn:
            lock_config_authoring(conn)
            conn.execute(
                scope.insert(schema.graph_lifecycle_facts).values(
                    graph_id=graph.graph_id, retired=True, set_at=_AT, set_by="test"
                )
            )
            future = pool.submit(run)
            if omit_lock:
                assert future.result(timeout=5)
                assert run()
            else:
                _wait_for_key(observer, future)
            conn.commit()
            if not omit_lock:
                with pytest.raises(RoutineGraphUnresolvedError):
                    future.result(timeout=10)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
