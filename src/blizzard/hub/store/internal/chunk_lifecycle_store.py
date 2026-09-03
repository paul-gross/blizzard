"""SQLAlchemy adapter for the chunk lifecycle seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row that happened; nothing here derives
status. Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import update

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import (
    enqueue_close_intents,
    ephemeral_ids,
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
            return chunk_id in ephemeral_ids(conn)

    def record_pause(self, chunk_id: str, *, paused: bool, by: str, at: datetime) -> int:
        """Append a ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins (issue #46)."""
        with self._store.write("record_pause") as conn:
            result = conn.execute(
                s.chunk_pause_facts.insert().values(chunk_id=chunk_id, paused=paused, set_at=at, set_by=by)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_stop(self, chunk_id: str, *, by: str, at: datetime) -> int:
        """Append the ``chunk.stopped`` fact, release any live route, and release any
        held fleet-wide hub-exec slot — all in **one** transaction (issue #118), so a
        ``kill -9`` cannot leave the chunk durably ``stopped`` with its route still live.
        The route check runs against this same connection (:func:`route_of_conn`), so
        there is no read-then-write race. The slot release is unconditional."""
        with self._store.write("record_stop") as conn:
            result = conn.execute(s.chunk_stopped.insert().values(chunk_id=chunk_id, stopped_at=at, stopped_by=by))
            if route_of_conn(conn, chunk_id) is not None:
                conn.execute(
                    s.route_released.insert().values(
                        chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id)
                    )
                )
            conn.execute(
                update(s.hub_exec_slot)
                .where((s.hub_exec_slot.c.holder_chunk_id == chunk_id) & (s.hub_exec_slot.c.released_at.is_(None)))
                .values(released_at=at)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_completion(self, chunk_id: str, *, by: str, at: datetime) -> int:
        """Append the ``chunk.completed`` fact, release any live route, and release any
        held fleet-wide hub-exec slot — all in **one** transaction (issue #294), mirroring
        :meth:`record_stop`, so a ``kill -9`` cannot leave the chunk durably ``done`` with
        its route still live. The caller has already checked the chunk is not already
        ``done`` — this always writes a fresh row."""
        with self._store.write("record_completion") as conn:
            result = conn.execute(
                s.chunk_completed.insert().values(chunk_id=chunk_id, completed_at=at, completed_by=by)
            )
            if route_of_conn(conn, chunk_id) is not None:
                conn.execute(
                    s.route_released.insert().values(
                        chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id)
                    )
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
