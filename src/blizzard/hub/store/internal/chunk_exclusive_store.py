"""SQLAlchemy adapter for the locked-transaction seam (package-private,
``bzh:store-exclusive-write``).

:class:`LockedChunkTransaction` is the one place a :class:`~sqlalchemy.Connection`
backs an object the domain layer holds — through :class:`ILockedChunkRead`, which
declares no connection access. A sibling write repository's own ``*_locked`` method
recovers the real connection through :func:`conn_of`, a package-private cast: only
:class:`ChunkExclusiveWrites` ever constructs one, and it always wraps a connection
already locked, in sorted chunk-id order, as the transaction's first statements."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Connection

from blizzard.hub.domain.chunks.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.registry import RunnerRegistration
from blizzard.hub.domain.work import Chunk, ChunkFacts, DependencyEdge
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_dependencies_store import ChunkDependenciesStore
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from blizzard.hub.store.internal.chunk_rows import is_ephemeral_id, lock_chunk_row, route_of_conn
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore


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

    def facts(self, chunk_id: str) -> ChunkFacts | None:
        return self._facts.load_facts_conn(self.conn, chunk_id)

    def facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        return self._facts.load_facts_for_conn(self.conn, chunk_ids)

    def record(self, chunk_id: str) -> Chunk | None:
        return self._record.get_conn(self.conn, chunk_id)

    def route_of(self, chunk_id: str) -> Route | None:
        return route_of_conn(self.conn, chunk_id)

    def is_ephemeral(self, chunk_id: str) -> bool:
        return is_ephemeral_id(self.conn, chunk_id)

    def standing_edges(self) -> list[DependencyEdge]:
        return self._dependencies.list_standing_edges_conn(self.conn)

    def runner_registration(self, runner_id: str) -> RunnerRegistration | None:
        return self._registry.get_runner_conn(self.conn, runner_id)


class ChunkExclusiveWrites:
    """Opens the locked write transaction the chunk claim and its excluded writers
    share (``bzh:store-exclusive-write``)."""

    def __init__(
        self,
        store: HubStoreConnections,
        *,
        facts: ChunkFactsStore,
        record: ChunkRecordStore,
        dependencies: ChunkDependenciesStore,
        registry: RunnerRegistryStore,
    ) -> None:
        self._store = store
        self._facts = facts
        self._record = record
        self._dependencies = dependencies
        self._registry = registry

    @contextmanager
    def locked(self, chunk_ids: Sequence[str]) -> Iterator[ILockedChunkRead]:
        with self._store.write("locked_chunk_transaction") as conn:
            for chunk_id in sorted(set(chunk_ids)):
                lock_chunk_row(conn, chunk_id)
            yield LockedChunkTransaction(conn, self._facts, self._record, self._dependencies, self._registry)


def _conforms_exclusive(x: ChunkExclusiveWrites) -> IChunkExclusiveWrites:
    return x
