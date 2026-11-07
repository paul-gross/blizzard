"""SQLAlchemy adapter for the lease-locked unit seam (package-private)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from blizzard.runner.leases.lease_lock import ILeaseUnit, IWriteLeaseLock
from blizzard.runner.store.internal.base import LeaseUnit
from blizzard.runner.store.schema import leases
from blizzard.runner.store.scoped import RunnerStoreConnections


class LeaseLockStore:
    """Lease-row lock adapter over the runner store engine."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    @contextmanager
    def locked(self, lease_id: str) -> Generator[ILeaseUnit, None, None]:
        # A no-op update of the lease's row as the first statement: on SQLite it takes the writer lock, and on Postgres
        # every writer of this lease queues on the row (``bzh:store-exclusive-write``).
        with self._store.begin() as conn:
            conn.execute(
                self._store.update(leases).where(leases.c.lease_id == lease_id).values(lease_id=leases.c.lease_id)
            )
            yield LeaseUnit(conn)


_lease_lock_store_conforms: type[IWriteLeaseLock] = LeaseLockStore
