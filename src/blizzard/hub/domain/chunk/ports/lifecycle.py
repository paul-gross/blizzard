"""The chunk-lifecycle repository seam — the terminal and paused states an
operator or the fleet itself drives the chunk to outside its graph's own transitions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.chunk.flow_writes import LeverWrite
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction


class IReadChunkLifecycleRepository(Protocol):
    """Read-only chunk-lifecycle access — no ``get``: it would be ``record``'s
    byte-identical query, so a caller needing it depends on that seam instead."""

    def is_ephemeral(self, chunk_id: str, *, transaction: Transaction | None = None) -> bool:
        """Whether ``chunk_id`` names a grouped-away or deleted chunk — the
        read that tells an ephemeral prerequisite apart from one never minted at all,
        since :meth:`~blizzard.hub.domain.chunk.ports.record.IReadChunkRecordRepository.get`
        answers ``None`` for both."""
        ...


class IWriteChunkLifecycleRepository(IReadChunkLifecycleRepository, Protocol):
    """Read-write chunk-lifecycle access."""

    def record_pause(
        self, chunk_id: str, *, paused: bool, by: str, at: datetime, transaction: Transaction | None = None
    ) -> int:
        """Append a ``chunk.paused``/``chunk.resumed`` fact — newest-fact-wins.

        Always writes a fresh row (never a no-op — "newest fact wins" reads, it does not
        skip writes), so the ``chunk_pause_facts.id`` comes back unconditionally. A pause passes the
        ``transaction`` that locks the chunk (``bzh:store-exclusive-write``), so the status the pause
        was admitted at cannot move between the check and this write; a resume is admitted at every
        status and writes alone."""
        ...

    def record_stop(
        self, chunk_id: str, *, by: str, at: datetime, waiting: Sequence[str], transaction: LockedTransaction
    ) -> LeverWrite:
        """Append the ``chunk.stopped`` fact — terminal operator abandonment —
        and, atomically in the locked ``transaction``
        (``bzh:store-exclusive-write``), release any live route and close ``stopped`` each of
        the flow submissions the caller names in ``waiting``, in that order.
        ``at`` is passed in, stamped by the caller after the row lock, so a
        claim that wins the row lock first can never mint a route sorting newer than this
        release. Returns the freshly-written
        ``chunk_stopped.id``, not the ``route_released.id`` this same transaction may also
        write, with the ``withdrawn`` flow facts it wrote."""
        ...

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
        """Append the ``chunk.completed`` fact — an operator's manual completion, including from
        ``stopped`` — and, atomically in the locked ``transaction``
        (``bzh:store-exclusive-write``), release any live route and close ``completed`` each of
        the flow submissions the caller names in ``waiting``, mirroring :meth:`record_stop`, and enqueue
        the chunk's close intents. The caller has already
        checked the chunk is not already ``done``. ``at`` is passed in, stamped by the
        caller after the row lock — see :meth:`record_stop`. Returns the freshly-written
        ``chunk_completed.id``, with the ``withdrawn`` flow facts it wrote."""
        ...
