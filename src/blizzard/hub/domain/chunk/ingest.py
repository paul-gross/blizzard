"""Chunk ingest — wrap ``{source, ref}`` pointers into a chunk pinned to a graph, storing the pointer
and never the contents.

**Batch = one chunk.** :func:`decide_ingest` owns what an ingest decides: a chunk wraps one or more
work refs, a ref named twice is wrapped once, and a pointer already held by a live chunk rejects the
whole ingest ``409`` — re-ingest is legal once its holder is finished."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.model import Chunk, WorkRef, mint_chunk
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.record import IWriteChunkRecordRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.graph.model import Graph


class IngestConflict(Exception):
    """A submitted pointer is already held by a live chunk — the 409 carrier."""

    def __init__(self, *, existing_chunk_id: str, pointer: WorkRef) -> None:
        super().__init__(f"pointer {pointer.source}#{pointer.ref} already held by live chunk {existing_chunk_id}")
        self.existing_chunk_id = existing_chunk_id
        self.pointer = pointer


class EmptyIngest(Exception):
    """An ingest named no work ref — a chunk wraps one or more."""

    def __init__(self) -> None:
        super().__init__("at least one token required")


def ingest_work_refs(pointers: Iterable[WorkRef]) -> list[WorkRef]:
    """The work refs one ingest wraps: ``pointers`` with each repeat dropped, in first-seen order — two
    tokens naming one item (its id and its URL, say) wrap it once. Raises :class:`EmptyIngest` when
    none remain."""
    work_refs = list(dict.fromkeys(pointers))
    if not work_refs:
        raise EmptyIngest()
    return work_refs


def require_unheld(pointers: Iterable[WorkRef], live_holders: Mapping[WorkRef, str]) -> None:
    """Raise :class:`IngestConflict` for the first of ``pointers`` a live chunk holds — the
    at-most-one-live-holder rule every pointer-minting path shares. ``live_holders`` maps each held
    pointer to its live holder's id, as
    :meth:`~blizzard.hub.domain.chunk.ports.work_refs.IReadChunkWorkRefsRepository.live_holders` reads it."""
    for pointer in pointers:
        holder = live_holders.get(pointer)
        if holder is not None:
            raise IngestConflict(existing_chunk_id=holder, pointer=pointer)


def decide_ingest(
    pointers: Sequence[WorkRef], *, live_holders: Mapping[WorkRef, str], graph: Graph, at: datetime
) -> Chunk:
    """The resting chunk an ingest of ``pointers`` mints on ``graph`` at ``at``, or the refusal:
    :class:`EmptyIngest` for no work ref, :class:`IngestConflict` for one a live chunk holds."""
    work_refs = ingest_work_refs(pointers)
    require_unheld(work_refs, live_holders)
    return mint_chunk(work_refs, graph_id=graph.graph_id, at=at)


def require_no_live_holder(work_refs: IReadChunkWorkRefsRepository, pointer: WorkRef) -> None:
    """Read ``pointer``'s live holder and apply :func:`require_unheld` — the single-pointer read the
    item-minting paths share."""
    holder = work_refs.find_live_holder(pointer)
    require_unheld([pointer], {pointer: holder} if holder is not None else {})


class IngestService:
    """Mint a chunk from work refs, pinned to the default graph."""

    def __init__(self, *, record: IWriteChunkRecordRepository, exclusive: IChunkExclusiveWrites, clock: IClock) -> None:
        self._record = record
        # The locked-transaction seam (``bzh:store-exclusive-write``): whether a pointer is held
        # is read under the pointer's lock, on the connection the mint then writes on.
        self._exclusive = exclusive
        self._clock = clock

    def ingest(self, pointers: Sequence[WorkRef], *, graph: Graph) -> str:
        """Mint a chunk wrapping ``pointers``; raises :class:`EmptyIngest` for none and
        :class:`IngestConflict` for one a live chunk holds. Of two overlapping ingests of one
        pointer, exactly one mints."""
        work_refs = ingest_work_refs(pointers)
        with self._exclusive.locked_work_refs(work_refs) as handle:
            chunk = decide_ingest(
                work_refs, live_holders=handle.live_holders(work_refs), graph=graph, at=self._clock.now()
            )
            self._record.mint_locked(handle, chunk)
        return chunk.chunk_id
