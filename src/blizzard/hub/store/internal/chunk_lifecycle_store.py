"""SQLAlchemy adapter for the chunk lifecycle seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row that happened; nothing here derives
status. Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import update

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunks.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import (
    conn_of,
    enqueue_close_intents,
    is_ephemeral_id,
    next_route_seq,
    route_of_conn,
)


class ChunkLifecycleStore:
    """The chunk's terminal and paused/resumed facts."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def is_ephemeral(self, chunk_id: str) -> bool:
        with self._store.read("is_ephemeral") as conn:
            return is_ephemeral_id(conn, chunk_id)

    def record_pause(self, chunk_id: str, *, paused: bool, by: str, at: datetime) -> int:
        """Append a ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins."""
        with self._store.write("record_pause") as conn:
            result = conn.execute(
                s.chunk_pause_facts.insert().values(chunk_id=chunk_id, paused=paused, set_at=at, set_by=by)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_stop_locked(self, handle: ILockedChunkRead, chunk_id: str, *, by: str) -> int:
        """Append the ``chunk.stopped`` fact, release any live route, and release any
        held fleet-wide hub-exec slot — all on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``), so a ``kill -9`` cannot leave the chunk durably
        ``stopped`` with its route still live. The route check runs against this same
        connection (:func:`route_of_conn`), so there is no read-then-write race. ``at`` is
        stamped from the injected clock after the row lock the caller already took — a
        claim that wins the row lock first still mints a route sorting newer than this
        release, since the release's own timestamp is never older than the wait it just
        cleared. The slot release is unconditional."""
        conn = conn_of(handle)
        at = self._clock.now()
        result = conn.execute(s.chunk_stopped.insert().values(chunk_id=chunk_id, stopped_at=at, stopped_by=by))
        if route_of_conn(conn, chunk_id) is not None:
            conn.execute(
                s.route_released.insert().values(chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id))
            )
        conn.execute(
            update(s.hub_exec_slot)
            .where((s.hub_exec_slot.c.holder_chunk_id == chunk_id) & (s.hub_exec_slot.c.released_at.is_(None)))
            .values(released_at=at)
        )
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def record_completion_locked(self, handle: ILockedChunkRead, chunk_id: str, *, by: str) -> int:
        """Append the ``chunk.completed`` fact, release any live route, and release any
        held fleet-wide hub-exec slot — all on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``), mirroring :meth:`record_stop_locked`, so a
        ``kill -9`` cannot leave the chunk durably ``done`` with its route still live. The
        caller has already checked the chunk is not already ``done`` — this always writes
        a fresh row. ``at`` is stamped from the injected clock after the row lock the
        caller already took — see :meth:`record_stop_locked` for why the ordering matters."""
        conn = conn_of(handle)
        at = self._clock.now()
        result = conn.execute(s.chunk_completed.insert().values(chunk_id=chunk_id, completed_at=at, completed_by=by))
        if route_of_conn(conn, chunk_id) is not None:
            conn.execute(
                s.route_released.insert().values(chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id))
            )
        conn.execute(
            update(s.hub_exec_slot)
            .where((s.hub_exec_slot.c.holder_chunk_id == chunk_id) & (s.hub_exec_slot.c.released_at.is_(None)))
            .values(released_at=at)
        )
        enqueue_close_intents(conn, chunk_id, at=at)
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0


def _conforms_lifecycle(x: ChunkLifecycleStore) -> IWriteChunkLifecycleRepository:
    return x
