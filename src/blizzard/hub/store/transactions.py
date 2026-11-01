"""Tenant write units, opaque tokens, and validated adapter participation."""

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, event

from blizzard.hub.domain.kernel.transactions import (
    GraduationError,
    IHubTransactions,
    LockedTransaction,
    Transaction,
    TransactionMisuse,
)
from blizzard.hub.store.locking import lock_chunk_row, lock_keys
from blizzard.hub.store.scoped import ScopedConnections


class _Unit:
    def __init__(self, store: ScopedConnections, conn: Connection) -> None:
        self.store = store
        self.conn = conn
        self.active = True
        self.statements = False
        self.graduated = False
        self.chunks: frozenset[str] = frozenset()
        self.keys: frozenset[tuple[str, str]] = frozenset()

    def account(self, *args: Any, **kwargs: Any) -> None:
        self.statements = True


class _Transaction:
    def __init__(self, unit: _Unit) -> None:
        self.unit = unit

    def lock_chunks(self, chunk_ids: Sequence[str]) -> LockedTransaction:
        return self.lock_set(chunks=chunk_ids)

    def lock_keys(self, namespace: str, keys: Sequence[str]) -> LockedTransaction:
        return self.lock_set(keys=[(namespace, key) for key in keys])

    def lock_set(self, *, chunks: Sequence[str] = (), keys: Sequence[tuple[str, str]] = ()) -> LockedTransaction:
        unit = self.unit
        if not unit.active:
            raise TransactionMisuse("transaction is no longer active")
        if unit.statements or unit.graduated:
            raise GraduationError("locks must be declared once before repository statements")
        unit.graduated = True
        unit.chunks = frozenset(chunks)
        unit.keys = frozenset(keys)
        for chunk_id in sorted(unit.chunks):
            lock_chunk_row(unit.conn, chunk_id)
        for namespace, key in sorted(unit.keys):
            lock_keys(unit.conn, namespace, [key])
        return _LockedTransaction(unit)


class _LockedTransaction(_Transaction):
    @property
    def locked(self) -> bool:
        return True


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
    key: tuple[str, str] | None = None,
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
    return unit.conn


_transactions_conform: type[IHubTransactions] = HubTransactions
