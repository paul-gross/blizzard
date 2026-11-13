"""Locked read handles over the shared transaction service."""

from __future__ import annotations

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Connection

from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, DependencyEdge
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.kernel.locks import LockKey
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction
from blizzard.hub.domain.runners.registration import RunnerRegistration
from blizzard.hub.domain.runners.route import Route

# Shares the chunk_dependencies_store writes on the same connection; debt pending a domain seam.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_dependencies_store import ChunkDependenciesStore

# Shares the chunk facts status-family tables and loader.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore

# Shares the chunk_record_store writes on the same connection; debt pending a domain seam.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from blizzard.hub.store.internal.chunk_rows import is_ephemeral_id, route_of_conn

# Shares the runner_registry_store writes on the same connection; debt pending a domain seam.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import ChunkHandle, connection_for


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
    ) -> None:
        self._store = store
        self._transactions = transactions
        self._facts = facts
        self._record = record
        self._dependencies = dependencies
        self._registry = registry

    @property
    def registry(self) -> RunnerRegistryStore:
        return self._registry

    @contextmanager
    def locked(
        self, chunk_ids: Sequence[str], *, keys: Sequence[LockKey] = ()
    ) -> Generator[ILockedChunkRead, None, None]:
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(chunks=chunk_ids, keys=keys)
            yield LockedChunkTransaction(
                self._store, locked, self._facts, self._record, self._dependencies, self._registry
            )


_exclusive_conforms: type[IChunkExclusiveWrites] = ChunkExclusiveWrites
