"""A lane's applied-seq high-water mark, advanced on the caller's connection by compare-and-set (package-private).

A mark table holds one row per owner — a runner, a host — whose ``seq`` only ever moves from the value the advancing
push last read or set. The conditional write is the check and the write in one statement, so it refuses a stale advance
(``bzh:store-refuses-stale``) and judges nothing else."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Table
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.hub.domain.kernel.high_water import HighWaterConflict
from blizzard.hub.store.scoped import statements_of


def advance_high_water(
    conn: Connection,
    mark: Table,
    owner: Column[str],
    owner_id: str,
    *,
    seq: int,
    expected: int,
    at: datetime,
) -> None:
    """Move ``owner_id``'s mark in ``mark`` from ``expected`` to ``seq``: an ``UPDATE`` conditioned on ``expected``, or,
    at ``expected`` 0, the owner's first row. Raises :class:`HighWaterConflict` when no row stands at ``expected`` or
    the first row's insert collides with the owner's row another push inserted, having moved nothing. A collision
    that leaves the owner no row in this scope is no stale advance, and raises as the store fault it is."""
    stmts = statements_of(conn)
    if expected == 0:
        try:
            # A savepoint, so the unit can still read which row the insert collided with.
            with conn.begin_nested():
                conn.execute(stmts.insert(mark).values({owner.name: owner_id, "seq": seq, "updated_at": at}))
        except IntegrityError as exc:
            if conn.execute(stmts.select(mark, owner).where(owner == owner_id)).first() is None:
                raise
            raise HighWaterConflict(owner_id, expected=expected) from exc
        return
    moved = conn.execute(
        stmts.update(mark).where(owner == owner_id, mark.c.seq == expected).values(seq=seq, updated_at=at)
    ).rowcount
    if moved != 1:
        raise HighWaterConflict(owner_id, expected=expected)
