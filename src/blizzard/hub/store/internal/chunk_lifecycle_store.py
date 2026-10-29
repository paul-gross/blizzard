"""SQLAlchemy adapter for the chunk lifecycle seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row that happened; nothing here derives
status. Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from datetime import datetime

from blizzard.hub.domain.chunk.flow_writes import LeverWrite
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import (
    conn_of,
    enqueue_close_intents,
    is_ephemeral_id,
    next_route_seq,
    route_of_conn,
)
from blizzard.hub.store.internal.flow_rows import withdraw_waiting
from blizzard.hub.store.scoped import ScopedConnections


class ChunkLifecycleStore:
    """The chunk's terminal and paused/resumed facts."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def is_ephemeral(self, chunk_id: str) -> bool:
        with self._store.read("is_ephemeral") as conn:
            return is_ephemeral_id(conn, chunk_id)

    def record_pause(self, chunk_id: str, *, paused: bool, by: str, at: datetime) -> int:
        """Append a ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins."""
        with self._store.write("record_pause") as conn:
            result = conn.execute(
                self._store.insert(s.chunk_pause_facts).values(chunk_id=chunk_id, paused=paused, set_at=at, set_by=by)
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_pause_locked(self, handle: ILockedChunkRead, chunk_id: str, *, by: str, at: datetime) -> int:
        """Append a ``chunk.paused`` fact on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``)."""
        result = conn_of(handle).execute(
            self._store.insert(s.chunk_pause_facts).values(chunk_id=chunk_id, paused=True, set_at=at, set_by=by)
        )
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def record_stop_locked(self, handle: ILockedChunkRead, chunk_id: str, *, by: str, at: datetime) -> LeverWrite:
        """`src/blizzard/hub/domain/chunk/ports/lifecycle.py`'s `IWriteChunkLifecycleRepository.record_stop_locked`.

        The route check runs on the same connection (:func:`route_of_conn`), so there is no
        read-then-write race; a submission mid-turn is left to the turn's end."""
        conn = conn_of(handle)
        result = conn.execute(
            self._store.insert(s.chunk_stopped).values(chunk_id=chunk_id, stopped_at=at, stopped_by=by)
        )
        if route_of_conn(conn, chunk_id) is not None:
            conn.execute(
                self._store.insert(s.route_released).values(
                    chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id)
                )
            )
        withdrawn = withdraw_waiting(conn, chunk_id, outcome="stopped", at=at)
        key = result.inserted_primary_key
        return LeverWrite(int(key[0]) if key is not None else 0, withdrawn)

    def record_completion_locked(self, handle: ILockedChunkRead, chunk_id: str, *, by: str, at: datetime) -> LeverWrite:
        """See ``src/blizzard/hub/domain/chunk/ports/lifecycle.py``'s
        ``IWriteChunkLifecycleRepository.record_completion_locked``.

        The route check runs on the same connection (:func:`route_of_conn`)."""
        conn = conn_of(handle)
        result = conn.execute(
            self._store.insert(s.chunk_completed).values(chunk_id=chunk_id, completed_at=at, completed_by=by)
        )
        if route_of_conn(conn, chunk_id) is not None:
            conn.execute(
                self._store.insert(s.route_released).values(
                    chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id)
                )
            )
        withdrawn = withdraw_waiting(conn, chunk_id, outcome="completed", at=at)
        enqueue_close_intents(conn, chunk_id, at=at)
        key = result.inserted_primary_key
        return LeverWrite(int(key[0]) if key is not None else 0, withdrawn)


_lifecycle_conforms: type[IWriteChunkLifecycleRepository] = ChunkLifecycleStore
