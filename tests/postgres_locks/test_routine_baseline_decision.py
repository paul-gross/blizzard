"""A real routine run and direct creation serialize their baseline decision."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.routines import RoutineAuthoring
from blizzard.hub.domain.garden.scopes import ScopeRegistry, ScopeSlug
from blizzard.hub.store import routine_run_participants
from blizzard.hub.store import schema as s
from blizzard.hub.store.borrowed import BorrowedScopedConnections
from blizzard.hub.store.internal import finding_store
from blizzard.hub.store.internal.finding_store import FindingSetStore
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


class _UnadmittedConnections(BorrowedScopedConnections):
    """Test-only transaction binding, deliberately bypassing tenant admission."""

    @contextmanager
    def write(self, operation, *, expect=()):
        with self.read(operation) as conn:
            yield conn

    def borrow(self, conn):
        return self


@pytest.mark.parametrize("winner", ["create", "run"])
@pytest.mark.parametrize("omit_lock", [False, True], ids=["locked", "negative-control"])
def test_run_baseline_against_direct_creation(database_url, monkeypatch, winner, omit_lock):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "apply_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "apply_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        scope, project, _ = _seed(holder)
        other = scoped_connections(waiter, scope.scope.tenant)
        graph = make_graph("gr_baseline_run", "baseline-run")
        GraphStore(scope).mint(graph, definition_yaml="", at=_AT)
        clock = FixedClock(_AT)
        routine = RoutineAuthoring(
            routines=RoutineStore(scope),
            graphs=GraphStore(scope),
            scope_registry=ScopeRegistry(scopes=ScopeStore(scope), clock=clock),
            clock=clock,
            participants=RoutineAuthoringParticipants(scope),
        ).create(
            project=project,
            name="nightly",
            graph_name=graph.name,
            default_scope_slug=ScopeSlug.parse("fresh"),
            ctx=ChangeContext("test", Door.API),
        )
        with scope.write("seed artifact") as conn:
            conn.execute(
                scope.insert(s.chunks).values(
                    chunk_id="seed_chunk", graph_id=graph.graph_id, project_id=project.project_id, minted_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.artifacts).values(
                    artifact_id="artifact",
                    chunk_id="seed_chunk",
                    node_id="node",
                    node_name="garden",
                    epoch=1,
                    name="delta",
                    kind="asset",
                    data="{}",
                    produced_at=_AT,
                    seq=1,
                )
            )

        if omit_lock:
            module = routine_run_participants if winner == "create" else finding_store
            monkeypatch.setattr(module, "lock_finding_set_baselines", lambda conn, pairs: None)

        def create(connections):
            return FindingSetStore(connections).create(
                "set_new",
                artifact_id="artifact",
                chunk_id="seed_chunk",
                project_id=project.project_id,
                scope_slug="fresh",
                routine_name="nightly",
                revisions={},
                measurement=None,
            )

        def run(connections):
            with RoutineRunParticipants(connections, clock).open() as admitted:
                fresh_routine = admitted.get_routine(routine.routine_id)
                assert fresh_routine is not None
                fresh_scope = admitted.get_scope(project.project_id, "fresh")
                assert fresh_scope is not None
                return admitted.run(
                    fresh_routine, scope=fresh_scope, mode=RunMode.DELTA, note=None, author=WorkItemAuthor.user("test")
                )

        first, second = (create, run) if winner == "create" else (run, create)
        # Both real operations borrow read transactions: neither can acquire the tenant
        # write-admission row, so only their shared baseline key can serialize them.
        with ThreadPoolExecutor(max_workers=1) as pool, scope.read("first operation") as conn:
            first_result = first(_UnadmittedConnections(conn, scope._errors, scope.scope))

            def compete():
                with other.read("second operation") as other_conn:
                    result = second(_UnadmittedConnections(other_conn, other._errors, other.scope))
                    other_conn.commit()
                    return result

            future = pool.submit(compete)
            if omit_lock:
                second_result = future.result(timeout=5)
            else:
                _wait_for_key(observer, future)
            conn.commit()
            if not omit_lock:
                second_result = future.result(timeout=10)

        result = second_result if winner == "create" else first_result
        has_baseline = winner == "create" and not omit_lock
        assert (result.baseline.finding_set_id if result.baseline else None) == ("set_new" if has_baseline else None)
        assert result.effective_mode is (RunMode.DELTA if has_baseline else RunMode.FULL)
        assert result.downgraded is not has_baseline
        assert ("baseline set_new" in result.item.body) is has_baseline
        assert (
            FindingSetStore(scope).newest_for_routine_scope(project.project_id, "nightly", "fresh").finding_set_id
            == "set_new"
        )
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
