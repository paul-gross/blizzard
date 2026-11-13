"""Tenant write units, opaque tokens, and validated adapter participation."""

from collections.abc import Callable, Generator, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Any

from sqlalchemy import Connection, Select, event
from sqlalchemy.exc import IntegrityError

from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.kernel.locks import HubLock, LockKey, LockNamespace
from blizzard.hub.domain.kernel.transactions import (
    GraduationError,
    IHubTransactions,
    LockedTransaction,
    Transaction,
    TransactionMisuse,
)
from blizzard.hub.store.locking import lock_chunk_row, lock_hub, lock_keys
from blizzard.hub.store.scoped import ScopedConnections


class _Unit:
    def __init__(self, store: ScopedConnections, conn: Connection) -> None:
        self.store = store
        self.conn = conn
        self.active = True
        self.statements = False
        self.graduated = False
        self.chunks: frozenset[str] = frozenset()
        self.keys: frozenset[LockKey] = frozenset()
        self.hub: frozenset[HubLock] = frozenset()

    def account(self, *args: Any, **kwargs: Any) -> None:
        self.statements = True


class _Transaction:
    def __init__(self, unit: _Unit) -> None:
        self.unit = unit

    def lock_chunks(self, chunk_ids: Sequence[str]) -> LockedTransaction:
        return self.lock_set(chunks=chunk_ids)

    def lock_keys(self, namespace: LockNamespace, keys: Sequence[str]) -> LockedTransaction:
        return self.lock_set(keys=[LockKey(namespace, key) for key in keys])

    def lock_set(
        self,
        *,
        chunks: Sequence[str] = (),
        keys: Sequence[LockKey] = (),
        hub: Sequence[HubLock] = (),
    ) -> LockedTransaction:
        unit = self.unit
        if not unit.active:
            raise TransactionMisuse("transaction is no longer active")
        if unit.statements or unit.graduated:
            raise GraduationError("locks must be declared once before repository statements")
        if any(lock is not HubLock.TENANT_LIFECYCLE for lock in hub):
            raise TransactionMisuse("a tenant unit may name only the tenant lifecycle lock")
        unit.graduated = True
        unit.hub = frozenset(hub)
        unit.chunks = frozenset(chunks)
        unit.keys = frozenset(keys)
        for chunk_id in sorted(unit.chunks):
            lock_chunk_row(unit.conn, chunk_id)
        for lock_key in sorted(unit.keys):
            lock_keys(unit.conn, lock_key.namespace, [lock_key.key])
        for lock in sorted(unit.hub):
            lock_hub(unit.conn, lock)
        return _LockedTransaction(unit)


class _LockedTransaction(_Transaction):
    @property
    def locked(self) -> bool:
        return True


class ChunkHandle:
    transaction: LockedTransaction


def chunk_handle_connection(handle: ILockedChunkRead, store: ScopedConnections, *chunks: str) -> Connection:
    if not isinstance(handle, ChunkHandle):
        raise TransactionMisuse("invalid chunk lock handle")
    conn = connection_for(store, handle.transaction)
    for chunk in chunks:
        connection_for(store, handle.transaction, chunk=chunk)
    return conn


class HubTransactions:
    """Open ordinary scoped writes; the tenant-open guard precedes statement accounting."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    @contextmanager
    def begin(self) -> Generator[Transaction, None, None]:
        with self._store.write("hub_transaction") as conn:
            unit = _Unit(self._store, conn)
            event.listen(conn, "before_cursor_execute", unit.account)
            try:
                yield _Transaction(unit)
            finally:
                unit.active = False
                event.remove(conn, "before_cursor_execute", unit.account)


def connection_for(
    store: ScopedConnections,
    transaction: Transaction,
    *,
    chunk: str | None = None,
    key: LockKey | None = None,
    hub: HubLock | None = None,
) -> Connection:
    """Resolve a token only for its originating store, lifetime, and requested lock coverage."""
    if not isinstance(transaction, _Transaction):
        raise TransactionMisuse("transaction was not created by the hub store")
    unit = transaction.unit
    if not unit.active or not unit.store.same_store_and_scope(store):
        raise TransactionMisuse("transaction is inactive or belongs to another store scope")
    if unit.graduated and not isinstance(transaction, _LockedTransaction):
        raise TransactionMisuse("the unlocked token was superseded by graduation")
    if chunk is not None and (not isinstance(transaction, _LockedTransaction) or chunk not in unit.chunks):
        raise TransactionMisuse("transaction does not lock this chunk")
    if key is not None and (not isinstance(transaction, _LockedTransaction) or key not in unit.keys):
        raise TransactionMisuse("transaction does not lock this key")
    if hub is not None and (not isinstance(transaction, _LockedTransaction) or hub not in unit.hub):
        raise TransactionMisuse("transaction does not lock this hub lock")
    return unit.conn


def connection_in(
    store: ScopedConnections, transaction: Transaction | None, operation: str, *, write: bool = False
) -> AbstractContextManager[Connection]:
    """The connection an operation runs on: the caller's unit when it passes one, else a fresh
    scoped ``read`` (or ``write``) that closes with the operation."""
    if transaction is not None:
        return nullcontext(connection_for(store, transaction))
    return store.write(operation) if write else store.read(operation)


def create_in_unit(
    conn: Connection, insert: Callable[[Connection], None], *, holder: Select[Any], taken: Exception
) -> None:
    """A create inside the caller's unit: a savepoint holds ``insert``, so a concurrent create that took the record's
    name first raises ``taken`` — when ``holder`` finds the row holding it — and the unit can still be read in."""
    try:
        with conn.begin_nested():
            insert(conn)
    except IntegrityError as exc:
        if conn.execute(holder).first() is not None:
            raise taken from exc
        raise


_transactions_conform: type[IHubTransactions] = HubTransactions
