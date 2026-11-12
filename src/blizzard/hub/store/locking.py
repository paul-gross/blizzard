"""Portable, tenant-scoped decision-row locks shared by store transactions."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.hub.domain.kernel.locks import HubLock, LockNamespace
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import statements_of


def lock_chunk_row(conn: Connection, chunk_id: str) -> None:
    """Lock an existing chunk row before reading its decision state."""
    conn.execute(statements_of(conn).update(s.chunks).where(s.chunks.c.chunk_id == chunk_id).values(chunk_id=chunk_id))


def lock_keys(conn: Connection, namespace: LockNamespace, keys: Sequence[str]) -> None:
    """Create absent keyed lock rows under savepoints, then lock them in sorted order."""
    stmts = statements_of(conn)
    lock = s.keyed_locks
    for key in sorted(set(keys)):
        named = (lock.c.namespace == namespace) & (lock.c.key == key)
        if conn.execute(stmts.select(lock, lock.c.key).where(named)).first() is None:
            try:
                with conn.begin_nested():
                    conn.execute(stmts.insert(lock).values(namespace=namespace, key=key))
            except IntegrityError:
                pass
        conn.execute(stmts.update(lock).where(named).values(key=key))


class HubLockMissing(RuntimeError):
    """Raised when a hub lock's seeded row is absent: the store was never migrated to the revision that seeds it."""

    def __init__(self, lock: HubLock) -> None:
        super().__init__(f"the hub store holds no {lock.value} lock row; run `blizzard hub migrate` before writing")
        self.lock = lock


def lock_hub(conn: Connection, lock: HubLock) -> None:
    """Take a hub-level lock: a no-op ``UPDATE`` of its seeded row. Never inserts — a missing row means the store was
    not migrated (``bzh:manual-migrations``)."""
    locked = conn.execute(sa.update(s.hub_locks).where(s.hub_locks.c.name == lock.value).values(name=lock.value))
    if locked.rowcount == 0:
        raise HubLockMissing(lock)
