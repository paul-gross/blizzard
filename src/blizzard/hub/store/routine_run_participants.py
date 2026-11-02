"""Transaction-bound participants for routine-run admission."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.model import Chunk, HubWorkItem, WorkItemAuthor, WorkRef
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.garden.runs.run import RunService
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_rows import insert_chunk_rows
from blizzard.hub.store.internal.chunk_work_refs_store import ChunkWorkRefsStore
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.finding_store import FindingSetStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.queue_lock import lock_queue_positions
from blizzard.hub.store.internal.routine_scope_store import RoutineScopeStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.run_context_store import insert_run_context_row
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.store.scoped import ScopedConnections


class _AdmittedItems(WorkItemStore):
    def __init__(self, scoped: ScopedConnections, conn: Connection) -> None:
        super().__init__(scoped)
        self._conn = conn

    def allocate_ref(self, source: str) -> str:
        try:
            with self._conn.begin_nested():
                self._conn.execute(self._store.insert(s.work_item_sequence).values(source=source, next_ref=2))
            return "1"
        except IntegrityError:
            row = self._conn.execute(
                self._store.update(s.work_item_sequence)
                .where(s.work_item_sequence.c.source == source)
                .values(next_ref=s.work_item_sequence.c.next_ref + 1)
                .returning(s.work_item_sequence.c.next_ref)
            ).one()
            return str(row.next_ref - 1)

    def create_run_with_chunk(
        self,
        *,
        pointer: WorkRef,
        title: str,
        body: str,
        author: WorkItemAuthor,
        routine_name: str,
        scope_slug: str,
        run_mode: str,
        project_id: str,
        at: datetime,
        chunk: Chunk,
    ) -> HubWorkItem:
        conn = self._conn
        lock_queue_positions(conn)
        self._admit_chunk(conn, chunk, at)
        work_item_id = self._insert_item(
            conn,
            source=pointer.source,
            ref=pointer.ref,
            title=title,
            body=body,
            author=author,
            stated_priority=None,
            at=at,
            project_id=chunk.project_id,
            routine_name=routine_name,
            scope_slug=scope_slug,
            run_mode=run_mode,
        )
        insert_chunk_rows(conn, chunk)
        insert_run_context_row(
            conn,
            work_item_id,
            RunContext(routine_name=routine_name, scope_slug=scope_slug, mode=run_mode, project_id=project_id),
        )
        return HubWorkItem(
            work_item_id=work_item_id,
            project_id=chunk.project_id,
            source=pointer.source,
            ref=pointer.ref,
            title=title,
            body=body,
            author=author,
            stated_priority=None,
            created_at=at,
            edited_at=at,
            routine_name=routine_name,
            scope_slug=scope_slug,
            run_mode=run_mode,
        )


class RoutineRunParticipants:
    def __init__(self, connections: ScopedConnections, clock: IClock) -> None:
        self._connections = connections
        self._clock = clock

    @contextmanager
    def open(self) -> Generator[RunService, None, None]:
        with self._connections.write("routine run admission") as conn:
            lock_config_authoring(conn)
            scoped = self._connections.borrow(conn)
            yield RunService(
                routines=RoutineStore(scoped),
                scopes=ScopeStore(scoped),
                routine_scopes=RoutineScopeStore(scoped),
                graphs=GraphStore(scoped),
                finding_sets=FindingSetStore(scoped),
                items=_AdmittedItems(scoped, conn),
                work_refs=ChunkWorkRefsStore(scoped, self._clock, facts=ChunkFactsStore(scoped)),
                clock=self._clock,
            )
