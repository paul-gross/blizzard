"""SQLAlchemy adapter for the locked-transaction seam (package-private,
``bzh:store-exclusive-write``).

:class:`LockedChunkTransaction` is the one place a :class:`~sqlalchemy.Connection`
backs an object the domain layer holds — through :class:`ILockedChunkRead`, which
declares no connection access. A sibling write repository's own ``*_locked`` method
recovers the real connection through :func:`conn_of`, a package-private cast: only
:class:`ChunkExclusiveWrites` ever constructs one, and it always wraps a connection
already locked, in sorted chunk-id order, as the transaction's first statements."""

from __future__ import annotations

import json
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Connection

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, DependencyEdge, WorkRef
from blizzard.hub.domain.chunk.ports.exclusive import (
    IChunkExclusiveWrites,
    ILockedChunkRead,
    ILockedFlowAdmissionRead,
    ILockedWithdrawal,
    ILockedWorkRefRead,
)
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.runners.registration import RunnerRegistration
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_dependencies_store import (
    DEPENDENCY_GRAPH_LOCK_KEY,
    DEPENDENCY_GRAPH_LOCK_NAMESPACE,
    ChunkDependenciesStore,
)
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from blizzard.hub.store.internal.chunk_rows import is_ephemeral_id, lock_chunk_row, lock_keys, route_of_conn
from blizzard.hub.store.internal.chunk_work_refs_store import ChunkWorkRefsStore
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.host_registry_store import lock_host_registry
from blizzard.hub.store.internal.project_link_store import (
    PROJECT_SOURCE_LINKS_LOCK_NAMESPACE,
    projects_linking_sources_conn,
)
from blizzard.hub.store.internal.queue_lock import lock_queue_positions
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.scoped import ScopedConnections


@dataclass(frozen=True)
class LockedChunkTransaction:
    """The concrete read handle — every read resolves on :attr:`conn`, the connection
    every named chunk's row was already locked on. ``conn`` is a plain attribute, not
    hidden, because every reader of it is store-internal code sharing this package;
    :class:`ILockedChunkRead` is what the domain layer actually holds it as."""

    conn: Connection
    _facts: ChunkFactsStore
    _record: ChunkRecordStore
    _dependencies: ChunkDependenciesStore
    _registry: RunnerRegistryStore
    locked_ids: frozenset[str]

    def facts(self, chunk_id: str) -> ChunkFacts | None:
        return self._facts.load_facts_conn(self.conn, chunk_id)

    def live_statuses(self) -> dict[str, ChunkStatus]:
        return self._facts.load_live_statuses_conn(self.conn)

    def facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        return self._facts.load_facts_for_conn(self.conn, chunk_ids)

    def record(self, chunk_id: str) -> Chunk | None:
        return self._record.get_conn(self.conn, chunk_id)

    def records_for(self, chunk_ids: Sequence[str]) -> dict[str, Chunk]:
        return self._record.get_many_conn(self.conn, chunk_ids)

    def route_of(self, chunk_id: str) -> Route | None:
        return route_of_conn(self.conn, chunk_id)

    def is_ephemeral(self, chunk_id: str) -> bool:
        return is_ephemeral_id(self.conn, chunk_id)

    def standing_edges(self) -> list[DependencyEdge]:
        return self._dependencies.list_standing_edges_conn(self.conn)

    def standing_edges_touching(self, chunk_id: str) -> list[DependencyEdge]:
        return self._dependencies.standing_edges_for_conn(self.conn, chunk_id)

    def runner_registration(self, runner_id: str) -> RunnerRegistration | None:
        return self._registry.get_runner_conn(self.conn, runner_id)


class LockedFlowAdmissionTransaction(LockedChunkTransaction):
    """A chunk transaction preceded by the config-authoring lock."""

    def flow_admission_locked(self) -> None:
        pass


@dataclass(frozen=True)
class LockedWithdrawalTransaction(LockedChunkTransaction):
    _holder: str | None

    def holder(self) -> str | None:
        return self._holder


@dataclass(frozen=True)
class LockedWorkRefTransaction:
    """The concrete pointer-lock handle — :class:`ILockedWorkRefRead`'s adapter, every read
    resolving on :attr:`conn`, the connection every named pointer's lock row was taken on."""

    conn: Connection
    _work_refs: ChunkWorkRefsStore
    _store: ScopedConnections

    def live_holders(self, pointers: Sequence[WorkRef]) -> dict[WorkRef, str]:
        return self._work_refs.live_holders_conn(self.conn, pointers)

    def source_links(self, sources: Sequence[str]) -> dict[str, list[ProjectId]]:
        found = projects_linking_sources_conn(self.conn, sources)
        return {source: found.get(source, []) for source in dict.fromkeys(sources)}

    def item_projects(self, pointers: Sequence[WorkRef]) -> dict[WorkRef, ProjectId]:
        refs = [p.ref for p in pointers if p.source == RESERVED_HUB_SOURCE_NAME]
        result: dict[WorkRef, ProjectId] = {}
        for batch in id_batches(refs):
            rows = self.conn.execute(
                self._store.select(s.work_items, s.work_items.c.ref, s.work_items.c.project_id).where(
                    s.work_items.c.source == RESERVED_HUB_SOURCE_NAME, s.work_items.c.ref.in_(batch)
                )
            )
            for row in rows:
                if row.project_id is not None:
                    result[WorkRef(source=RESERVED_HUB_SOURCE_NAME, ref=row.ref)] = ProjectId(row.project_id)
        return result

    def config_authoring_locked(self) -> None:
        pass

    def withdrawn_items(self, pointers: Sequence[WorkRef]) -> set[WorkRef]:
        refs = [p.ref for p in pointers if p.source == RESERVED_HUB_SOURCE_NAME]
        result: set[WorkRef] = set()
        for batch in id_batches(refs):
            rows = self.conn.execute(
                self._store.select(s.work_items, s.work_items.c.ref).where(
                    s.work_items.c.source == RESERVED_HUB_SOURCE_NAME,
                    s.work_items.c.ref.in_(batch),
                    s.work_items.c.closure == "withdrawn",
                )
            )
            result.update(WorkRef(source=RESERVED_HUB_SOURCE_NAME, ref=row.ref) for row in rows)
        return result


_POINTER_LOCK_NAMESPACE = "work_ref"


def pointer_lock_key(pointer: WorkRef) -> str:
    """The injective ``keyed_locks`` key of ``pointer`` — a JSON array, so no ``source``/``ref``
    pair can collide with another by where a separator falls."""
    return json.dumps([pointer.source, pointer.ref])


class ChunkExclusiveWrites:
    """Opens the locked write transaction the chunk claim and its excluded writers
    share (``bzh:store-exclusive-write``)."""

    def __init__(
        self,
        store: ScopedConnections,
        *,
        facts: ChunkFactsStore,
        record: ChunkRecordStore,
        dependencies: ChunkDependenciesStore,
        registry: RunnerRegistryStore,
        work_refs: ChunkWorkRefsStore,
    ) -> None:
        self._store = store
        self._facts = facts
        self._record = record
        self._dependencies = dependencies
        self._registry = registry
        self._work_refs = work_refs

    @contextmanager
    def locked(self, chunk_ids: Sequence[str]) -> Generator[ILockedChunkRead, None, None]:
        with self._store.write("locked_chunk_transaction") as conn:
            ids = frozenset(chunk_ids)
            for chunk_id in sorted(ids):
                lock_chunk_row(conn, chunk_id)
            yield LockedChunkTransaction(conn, self._facts, self._record, self._dependencies, self._registry, ids)

    @contextmanager
    def locked_flow_admission(self, chunk_ids: Sequence[str]) -> Generator[ILockedFlowAdmissionRead, None, None]:
        with self._store.write("locked_flow_admission") as conn:
            lock_config_authoring(conn)
            ids = frozenset(chunk_ids)
            for chunk_id in sorted(ids):
                lock_chunk_row(conn, chunk_id)
            yield LockedFlowAdmissionTransaction(
                conn, self._facts, self._record, self._dependencies, self._registry, ids
            )

    @contextmanager
    def locked_queue(self, chunk_ids: Sequence[str]) -> Generator[ILockedChunkRead, None, None]:
        with self._store.write("locked_queue") as conn:
            lock_queue_positions(conn)
            # The key excludes queue mutations and ingest; the conservative live roster
            # also includes chunks whose status may enter either list via a row-locked
            # writer such as claim, pause, or restart. Acquire every row in one order.
            # This deliberately costs one UPDATE per live chunk and can delay unrelated
            # chunk writers; the roster must stay stable until the queue decision commits.
            ids = frozenset([*chunk_ids, *self._facts.maybe_live_ids_conn(conn)])
            for chunk_id in sorted(ids):
                lock_chunk_row(conn, chunk_id)
            yield LockedChunkTransaction(conn, self._facts, self._record, self._dependencies, self._registry, ids)

    @contextmanager
    def locked_dependency_graph(self, chunk_ids: Sequence[str]) -> Generator[ILockedChunkRead, None, None]:
        with self._store.write("locked_dependency_graph") as conn:
            lock_keys(conn, DEPENDENCY_GRAPH_LOCK_NAMESPACE, [DEPENDENCY_GRAPH_LOCK_KEY])
            ids = frozenset(chunk_ids)
            for chunk_id in sorted(ids):
                lock_chunk_row(conn, chunk_id)
            yield LockedChunkTransaction(conn, self._facts, self._record, self._dependencies, self._registry, ids)

    @contextmanager
    def locked_withdrawal(self, pointer: WorkRef) -> Generator[ILockedWithdrawal, None, None]:
        with self._store.write("locked_withdrawal") as conn:
            lock_keys(conn, DEPENDENCY_GRAPH_LOCK_NAMESPACE, [DEPENDENCY_GRAPH_LOCK_KEY])
            ids = frozenset(self._work_refs.holder_candidates_conn(conn, pointer))
            for chunk_id in sorted(ids):
                lock_chunk_row(conn, chunk_id)
            holder = self._work_refs.live_holders_conn(conn, [pointer]).get(pointer)
            transaction = LockedWithdrawalTransaction(
                conn, self._facts, self._record, self._dependencies, self._registry, ids, holder
            )
            yield transaction

    @contextmanager
    def locked_claim(self, chunk_id: str) -> Generator[ILockedChunkRead, None, None]:
        with self._store.write("locked_claim") as conn:
            lock_keys(conn, DEPENDENCY_GRAPH_LOCK_NAMESPACE, [DEPENDENCY_GRAPH_LOCK_KEY])
            edges = self._dependencies.standing_edges_for_conn(conn, chunk_id)
            ids = {chunk_id, *(e.prerequisite_chunk_id for e in edges if e.dependent_chunk_id == chunk_id)}
            for locked_id in sorted(ids):
                lock_chunk_row(conn, locked_id)
            # Registry writers never acquire chunk rows while holding this key.
            lock_host_registry(conn)
            yield LockedChunkTransaction(
                conn, self._facts, self._record, self._dependencies, self._registry, frozenset(ids)
            )

    @contextmanager
    def locked_work_refs(
        self, pointers: Sequence[WorkRef], *, project: ProjectId | None = None
    ) -> Generator[ILockedWorkRefRead, None, None]:
        with self._store.write("locked_work_ref_transaction") as conn:
            # All source-link authoring takes this key, including links to other projects.
            lock_config_authoring(conn)
            # Ingest creates a backlog member. Take the queue key before the graph,
            # project and pointer keys so a queue roster cannot miss a concurrent mint.
            lock_queue_positions(conn)
            # Group moves refs while holding this key. Take it before any project or
            # pointer key so admission sees either side of the entire fold.
            lock_keys(conn, DEPENDENCY_GRAPH_LOCK_NAMESPACE, [DEPENDENCY_GRAPH_LOCK_KEY])
            if project is not None:
                lock_keys(conn, PROJECT_SOURCE_LINKS_LOCK_NAMESPACE, [project])
            lock_keys(conn, _POINTER_LOCK_NAMESPACE, [pointer_lock_key(p) for p in pointers])
            yield LockedWorkRefTransaction(conn, self._work_refs, self._store)


_exclusive_conforms: type[IChunkExclusiveWrites] = ChunkExclusiveWrites
