"""The chunk-lifecycle repository seam — the terminal and paused states an
operator or the fleet itself drives the chunk to outside its graph's own transitions."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead


class IReadChunkLifecycleRepository(Protocol):
    """Read-only chunk-lifecycle access — no ``get``: it would be ``record``'s
    byte-identical query, so a caller needing it depends on that seam instead."""

    def is_ephemeral(self, chunk_id: str) -> bool:
        """Whether ``chunk_id`` names a grouped-away or deleted chunk — the
        read that tells an ephemeral prerequisite apart from one never minted at all,
        since :meth:`~blizzard.hub.domain.chunk.ports.record.IReadChunkRecordRepository.get`
        answers ``None`` for both. The record seam's exclusion of ephemeral ids from
        every other read is not widened by this."""
        ...


class IWriteChunkLifecycleRepository(IReadChunkLifecycleRepository, Protocol):
    """Read-write chunk-lifecycle access."""

    def record_pause(self, chunk_id: str, *, paused: bool, by: str, at: datetime) -> int:
        """Append a ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins.

        Always writes a fresh row (never a no-op — "newest fact wins" reads, it does not
        skip writes), so the ``chunk_pause_facts.id`` comes back unconditionally."""
        ...

    def record_stop_locked(self, handle: ILockedChunkRead, chunk_id: str, *, by: str, at: datetime) -> int:
        """Append the ``chunk.stopped`` fact — terminal operator abandonment —
        and, atomically on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``), release any live route and any held fleet-wide
        hub-exec slot. ``at`` is passed in, stamped by the caller after the row lock, so a
        claim that wins the row lock first can never mint a route sorting newer than this
        release. Returns the freshly-written
        ``chunk_stopped.id``, not the ``route_released.id`` this same transaction may also
        write."""
        ...

    def record_completion_locked(self, handle: ILockedChunkRead, chunk_id: str, *, by: str, at: datetime) -> int:
        """Append the ``chunk.completed`` fact — an operator's manual completion, including from
        ``stopped`` — and, atomically on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``), release any live route and any held fleet-wide
        hub-exec slot, mirroring :meth:`record_stop_locked`. The caller has already
        checked the chunk is not already ``done``. ``at`` is passed in, stamped by the
        caller after the row lock — see :meth:`record_stop_locked`. Returns the freshly-written
        ``chunk_completed.id``."""
        ...
