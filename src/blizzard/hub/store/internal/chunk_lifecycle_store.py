"""SQLAlchemy adapter for the chunk lifecycle seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row that happened; nothing here derives
status. Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection

from blizzard.hub.domain.chunk.flow_writes import LeverWrite
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import enqueue_close_intents, is_ephemeral_id, next_route_seq, route_of_conn
from blizzard.hub.store.internal.flow_rows import withdraw_waiting
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import connection_for, connection_in


class ChunkLifecycleStore:
    """The chunk's terminal and paused/resumed facts."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def is_ephemeral(self, chunk_id: str, *, transaction: Transaction | None = None) -> bool:
        with connection_in(self._store, transaction, "is_ephemeral") as conn:
            return is_ephemeral_id(conn, chunk_id)

    def record_pause(
        self, chunk_id: str, *, paused: bool, by: str, at: datetime, transaction: Transaction | None = None
    ) -> int:
        """Append a ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins. With a ``transaction``,
        on its connection, which must lock the chunk (``bzh:store-exclusive-write``)."""
        if transaction is None:
            with self._store.write("record_pause") as conn:
                return self._record_pause_conn(conn, chunk_id, paused=paused, by=by, at=at)
        conn = connection_for(self._store, transaction, chunk=chunk_id)
        return self._record_pause_conn(conn, chunk_id, paused=paused, by=by, at=at)

    def _record_pause_conn(self, conn: Connection, chunk_id: str, *, paused: bool, by: str, at: datetime) -> int:
        result = conn.execute(
            self._store.insert(s.chunk_pause_facts).values(chunk_id=chunk_id, paused=paused, set_at=at, set_by=by)
        )
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def record_stop(
        self, chunk_id: str, *, by: str, at: datetime, waiting: Sequence[str], transaction: LockedTransaction
    ) -> LeverWrite:
        """`src/blizzard/hub/domain/chunk/ports/lifecycle.py`'s `IWriteChunkLifecycleRepository.record_stop`.

        The route check runs on the same connection (:func:`route_of_conn`), so there is no
        read-then-write race."""
        conn = connection_for(self._store, transaction, chunk=chunk_id)
        result = conn.execute(
            self._store.insert(s.chunk_stopped).values(chunk_id=chunk_id, stopped_at=at, stopped_by=by)
        )
        if route_of_conn(conn, chunk_id) is not None:
            conn.execute(
                self._store.insert(s.route_released).values(
                    chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id)
                )
            )
        withdrawn = withdraw_waiting(conn, waiting, outcome="stopped", at=at)
        key = result.inserted_primary_key
        return LeverWrite(int(key[0]) if key is not None else 0, withdrawn)

    def record_completion(
        self,
        chunk_id: str,
        *,
        by: str,
        at: datetime,
        close_decision: CloseEnqueueDecision,
        waiting: Sequence[str],
        transaction: LockedTransaction,
    ) -> LeverWrite:
        """See ``src/blizzard/hub/domain/chunk/ports/lifecycle.py``'s
        ``IWriteChunkLifecycleRepository.record_completion``.

        The route check runs on the same connection (:func:`route_of_conn`)."""
        conn = connection_for(self._store, transaction, chunk=chunk_id)
        result = conn.execute(
            self._store.insert(s.chunk_completed).values(chunk_id=chunk_id, completed_at=at, completed_by=by)
        )
        if route_of_conn(conn, chunk_id) is not None:
            conn.execute(
                self._store.insert(s.route_released).values(
                    chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id)
                )
            )
        withdrawn = withdraw_waiting(conn, waiting, outcome="completed", at=at)
        close_skipped = enqueue_close_intents(conn, chunk_id, at=at, close_decision=close_decision)
        key = result.inserted_primary_key
        return LeverWrite(int(key[0]) if key is not None else 0, withdrawn, close_skipped)


_lifecycle_conforms: type[IWriteChunkLifecycleRepository] = ChunkLifecycleStore
