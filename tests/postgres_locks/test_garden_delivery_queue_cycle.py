"""Delivery, routine admission and the queue roster share a lock order."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.delivery.materialize import (
    DeliveryOutcome,
    DeliveryPlan,
    DeltaMaterialization,
    NewFindingSet,
)
from blizzard.hub.domain.garden.routines import RoutineAuthoring
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.garden.scopes import ScopeRegistry, ScopeSlug
from blizzard.hub.store import routine_run_participants
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.queue_lock import lock_queue_positions
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.routine_authoring_participants import RoutineAuthoringParticipants
from blizzard.hub.store.routine_run_participants import RoutineRunParticipants
from tests.postgres_locks.test_project_landing_order import _seed
from tests.support import make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 29, tzinfo=UTC)


def _wait_blocked(observer, waiter, holder, future):
    deadline = monotonic() + 5
    with observer.connect() as conn:
        while monotonic() < deadline:
            if conn.execute(
                sa.text("""
                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                  WHERE w.datname = current_database() AND h.datname = current_database()
                    AND w.application_name = :waiter AND h.application_name = :holder)
            """),
                {"waiter": waiter, "holder": holder},
            ).scalar_one():
                return
            assert not future.done(), f"{waiter} finished before blocking on {holder}"
            Event().wait(0.02)
    pytest.fail(f"{waiter} did not block on {holder}")


def test_delivery_waits_before_chunk_while_routine_waits_for_queue(database_url, monkeypatch):
    options = "-c statement_timeout=8000"
    engines = [
        sa.create_engine(database_url, connect_args={"application_name": name, "options": options})
        for name in ("cycle_seed", "cycle_queue", "cycle_run", "cycle_delivery")
    ]
    seed, queue, run_engine, delivery = engines
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    baseline_held = Event()
    original = routine_run_participants.lock_finding_set_baselines

    def signal_baseline(conn, pairs):
        original(conn, pairs)
        baseline_held.set()

    monkeypatch.setattr(routine_run_participants, "lock_finding_set_baselines", signal_baseline)
    try:
        scope, project, _ = _seed(seed)
        tenant = scope.scope.tenant
        graph = make_graph("gr_cycle", "cycle")
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
        with scope.write("seed delivery") as conn:
            conn.execute(
                scope.insert(s.chunks).values(
                    chunk_id="ch_cycle", graph_id=graph.graph_id, project_id=project.project_id, minted_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.artifacts).values(
                    artifact_id="art_cycle",
                    chunk_id="ch_cycle",
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
        plan = DeliveryPlan(
            "ch_cycle",
            "delivery",
            "garden",
            1,
            _AT,
            RunContext(project_id=project.project_id, routine_name="nightly", scope_slug="fresh", mode="dry_run"),
            [DeltaMaterialization(NewFindingSet("set_cycle", "art_cycle", "fresh", {}, None))],
            [],
            {},
        )
        run_scope = scoped_connections(run_engine, tenant)
        delivery_scope = scoped_connections(delivery, tenant)
        queue_scope = scoped_connections(queue, tenant)

        # Remove tenant admission only on these actors, so it cannot mask the cycle.
        monkeypatch.setattr(run_scope, "_lock_open_tenant", lambda conn: None)
        monkeypatch.setattr(delivery_scope, "_lock_open_tenant", lambda conn: None)

        def admit():
            with RoutineRunParticipants(run_scope, clock).open() as service:
                current = service.get_routine(routine.routine_id)
                assert current is not None
                target = service.get_scope(current.project_id, "fresh")
                assert target is not None
                return service.run(
                    current, scope=target, mode=RunMode.DELTA, note=None, author=WorkItemAuthor.user("test")
                )

        def deliver():
            return GardenDeliveryStore(delivery_scope).deliver(plan, admission=EpochAdmission.AT_OR_ABOVE)

        with ThreadPoolExecutor(max_workers=2) as pool, queue_scope.read("queue roster") as conn:
            lock_queue_positions(conn)
            run_future = pool.submit(admit)
            if not baseline_held.wait(5):
                run_future.result(timeout=1)
                pytest.fail("routine did not acquire the baseline")
            _wait_blocked(observer, "cycle_run", "cycle_queue", run_future)
            delivery_future = pool.submit(deliver)
            _wait_blocked(observer, "cycle_delivery", "cycle_run", delivery_future)
            # The roster can take the delivery chunk while delivery waits on baseline.
            lock_chunk_row(conn, "ch_cycle")
            conn.commit()
            result = run_future.result(timeout=8)
            assert result.downgraded and result.baseline is None
            assert delivery_future.result(timeout=8) is DeliveryOutcome.RECORDED
        with scope.read("verify") as conn:
            assert (
                conn.execute(
                    scope.select(s.finding_sets, s.finding_sets.c.finding_set_id).where(
                        s.finding_sets.c.finding_set_id == "set_cycle"
                    )
                ).scalar_one()
                == "set_cycle"
            )
    finally:
        for engine in [*engines, observer]:
            engine.dispose()
