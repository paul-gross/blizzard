"""Compatibility read handles backed by the shared transaction service."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Connection

from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, DependencyEdge, WorkRef
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead, ILockedWorkRefRead
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.runners.registration import RunnerRegistration
from blizzard.hub.domain.runners.route import Route

# Legacy exclusive handle composes dependency reads on its locked connection.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_dependencies_store import ChunkDependenciesStore

# Legacy exclusive handle composes fact reads on its locked connection.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore

# Legacy exclusive handle composes record reads on its locked connection.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore

# Legacy exclusive reads share chunk route and ephemeral-row queries.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_rows import is_ephemeral_id, route_of_conn

# Legacy exclusive handle composes work-reference reads on its locked connection.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_work_refs_store import ChunkWorkRefsStore

# Legacy pointer locking also reads project-source links in the same transaction.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.project_link_store import (
    PROJECT_SOURCE_LINKS_LOCK_NAMESPACE,
    projects_linking_sources_conn,
)

# Legacy exclusive handle composes registry reads on its locked connection.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import ChunkHandle, WorkRefHandle, connection_for, pointer_lock_key


@dataclass(frozen=True)
class LockedChunkTransaction(ChunkHandle):
    """Store-internal handle backed by a lifetime-checked locked transaction."""

    _store: ScopedConnections
    transaction: LockedTransaction
    _facts: ChunkFactsStore
    _record: ChunkRecordStore
    _dependencies: ChunkDependenciesStore
    _registry: RunnerRegistryStore

    @property
    def conn(self) -> Connection:
        return connection_for(self._store, self.transaction)

    def facts(self, chunk_id: str) -> ChunkFacts | None:
        return self._facts.load_facts_conn(self.conn, chunk_id)

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


@dataclass(frozen=True)
class LockedWorkRefTransaction(WorkRefHandle):
    """The concrete pointer-lock handle — :class:`ILockedWorkRefRead`'s adapter, every read
    resolving on :attr:`conn`, the connection every named pointer's lock row was taken on."""

    _store: ScopedConnections
    transaction: LockedTransaction
    _work_refs: ChunkWorkRefsStore

    @property
    def conn(self) -> Connection:
        return connection_for(self._store, self.transaction)

    def live_holders(self, pointers: Sequence[WorkRef]) -> dict[WorkRef, str]:
        return self._work_refs.live_holders_conn(self.conn, pointers)

    def source_links(self, sources: Sequence[str]) -> dict[str, list[ProjectId]]:
        found = projects_linking_sources_conn(self.conn, sources)
        return {source: found.get(source, []) for source in dict.fromkeys(sources)}


_POINTER_LOCK_NAMESPACE = "work_ref"


class ChunkExclusiveWrites:
    """Opens the locked write transaction the chunk claim and its excluded writers
    share (``bzh:store-exclusive-write``)."""

    def __init__(
        self,
        store: ScopedConnections,
        *,
        transactions: IHubTransactions,
        facts: ChunkFactsStore,
        record: ChunkRecordStore,
        dependencies: ChunkDependenciesStore,
        registry: RunnerRegistryStore,
        work_refs: ChunkWorkRefsStore,
    ) -> None:
        self._store = store
        self._transactions = transactions
        self._facts = facts
        self._record = record
        self._dependencies = dependencies
        self._registry = registry
        self._work_refs = work_refs

    @contextmanager
    def locked(self, chunk_ids: Sequence[str]) -> Generator[ILockedChunkRead, None, None]:
        with self._transactions.begin() as transaction:
            locked = transaction.lock_chunks(chunk_ids)
            yield LockedChunkTransaction(
                self._store, locked, self._facts, self._record, self._dependencies, self._registry
            )

    @contextmanager
    def locked_work_refs(
        self, pointers: Sequence[WorkRef], *, project: ProjectId | None = None
    ) -> Generator[ILockedWorkRefRead, None, None]:
        with self._transactions.begin() as transaction:
            keys = [(_POINTER_LOCK_NAMESPACE, pointer_lock_key(p)) for p in pointers]
            if project is not None:
                keys.append((PROJECT_SOURCE_LINKS_LOCK_NAMESPACE, project))
            locked = transaction.lock_set(keys=keys)
            yield LockedWorkRefTransaction(self._store, locked, self._work_refs)


_exclusive_conforms: type[IChunkExclusiveWrites] = ChunkExclusiveWrites
