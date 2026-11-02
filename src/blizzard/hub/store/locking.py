"""Portable, tenant-scoped decision-row locks shared by store transactions."""

from collections.abc import Sequence

from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import statements_of


def lock_chunk_row(conn: Connection, chunk_id: str) -> None:
    """Lock an existing chunk row before reading its decision state."""
    conn.execute(statements_of(conn).update(s.chunks).where(s.chunks.c.chunk_id == chunk_id).values(chunk_id=chunk_id))


def lock_keys(conn: Connection, namespace: str, keys: Sequence[str]) -> None:
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
