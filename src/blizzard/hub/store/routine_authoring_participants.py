"""Participants in an admitted standalone routine authoring transaction."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy.engine import Connection

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.garden.routines import Routine, RoutineAuthoring
from blizzard.hub.domain.garden.scopes import ScopeMint, ScopeRegistry
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.routine_store import RoutineStore, insert_routine, update_routine
from blizzard.hub.store.internal.scope_store import ScopeStore, ensure_scope
from blizzard.hub.store.scoped import ScopedConnections


class _AdmittedRoutines(RoutineStore):
    def __init__(self, scoped: ScopedConnections, conn: Connection) -> None:
        super().__init__(scoped)
        self._conn = conn

    def create(self, routine: Routine, *, change: ConfigChange, scope_mint: ScopeMint | None) -> Routine:
        if scope_mint is not None:
            ensure_scope(self._conn, scope_mint)
        insert_routine(self._conn, routine, change)
        return routine

    def update(
        self, routine: Routine, *, from_revision: int, change: ConfigChange, scope_mint: ScopeMint | None
    ) -> Routine:
        if scope_mint is not None:
            ensure_scope(self._conn, scope_mint)
        update_routine(self._conn, routine, from_revision, change)
        return routine


class RoutineAuthoringParticipants:
    def __init__(self, connections: ScopedConnections) -> None:
        self._connections = connections

    @contextmanager
    def open(self, *, clock: IClock) -> Generator[RoutineAuthoring, None, None]:
        with self._connections.write("routine authoring") as conn:
            lock_config_authoring(conn)
            scoped = self._connections.borrow(conn)
            yield RoutineAuthoring(
                routines=_AdmittedRoutines(scoped, conn),
                graphs=GraphStore(scoped),
                scope_registry=ScopeRegistry(scopes=ScopeStore(scoped), clock=clock),
                clock=clock,
            )
