"""Queue-shaping domain — ready-queue reordering and grouping.

The two operator actions that shape the ready queue rather than execute work
: **Prioritize** (reorder a ready chunk to a position) and
**Group** (merge unacquired chunks into one surviving chunk). Both are pure hub-side
properties over the fact store — order derives from appended position facts,
and grouping folds PM pointers into the survivor and discards the rest as ephemeral.
Neither touches an acquired chunk: reordering and grouping are legal
only in ``ready`` (the fill/peek surface), so a running chunk is never reshaped under a
runner's feet.

Pure-ish domain services (``bzh:controller-read-only``): they hold the write chunk
repository and the injected clock, validate against the **derived** status (never a
stored column), and raise typed errors the controller maps to HTTP.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    ChunkStatus,
    IWriteChunkRepository,
    derive_chunk_status,
)

_log = get_logger("blizzard.hub.queue")


class ChunkNotFound(LookupError):
    """A named chunk does not exist (or was grouped/discarded away)."""

    def __init__(self, chunk_id: str) -> None:
        super().__init__(f"unknown chunk {chunk_id}")
        self.chunk_id = chunk_id


class ChunkNotReady(ValueError):
    """A queue-shaping op named a chunk that is not in ``ready``."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not ready — queue shaping is ready-only")
        self.chunk_id = chunk_id
        self.status = status


class QueueService:
    """Reorder the ready queue as an explicit hub-side property."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def ordered_ready(self) -> list[Chunk]:
        """Ready chunks in queue order — ascending by effective position."""
        positions = self._chunks.queue_positions()
        ready = self._chunks.list_ready()
        return sorted(ready, key=lambda c: self._effective_position(c, positions))

    def reorder(self, chunk_id: str, *, to_index: int) -> None:
        """Move a ready chunk to ``to_index`` (0 = top); append its new position fact.

        The new position is computed between the target neighbours in the current order
        (excluding the moved chunk), so one appended float fact re-ranks the chunk without
        rewriting the rest — floats leave room to insert again between any two chunks.
        """
        self._require_ready(chunk_id)
        positions = self._chunks.queue_positions()
        others = [c for c in self.ordered_ready() if c.chunk_id != chunk_id]
        new_position = self._position_at(others, positions, to_index)
        self._chunks.record_queue_position(chunk_id, position=new_position, at=self._clock.now())
        _log.info("ready queue reordered", chunk_id=chunk_id, to_index=to_index, position=new_position)

    def _position_at(self, others: list[Chunk], positions: dict[str, float], to_index: int) -> float:
        if not others:
            return 0.0
        clamped = max(0, min(to_index, len(others)))
        if clamped <= 0:
            return self._effective_position(others[0], positions) - 1.0
        if clamped >= len(others):
            return self._effective_position(others[-1], positions) + 1.0
        before = self._effective_position(others[clamped - 1], positions)
        after = self._effective_position(others[clamped], positions)
        return (before + after) / 2.0

    @staticmethod
    def _effective_position(chunk: Chunk, positions: dict[str, float]) -> float:
        """A chunk's sort key: its newest explicit position, else its mint instant.

        Before a chunk is ever moved, its position is its ``minted_at`` as a unix
        timestamp — so an un-reordered queue is plain FIFO, and any explicit move (a
        smaller float) lifts a chunk above the un-moved tail.
        """
        explicit = positions.get(chunk.chunk_id)
        return explicit if explicit is not None else chunk.minted_at.timestamp()

    def _require_ready(self, chunk_id: str) -> None:
        facts = self._chunks.load_facts(chunk_id)
        if facts is None:
            raise ChunkNotFound(chunk_id)
        status = derive_chunk_status(facts)
        if status is not ChunkStatus.READY:
            raise ChunkNotReady(chunk_id, status)


class GroupService:
    """Merge unacquired (ready) chunks into one surviving chunk."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def group(self, survivor_id: str, merge_ids: list[str]) -> Chunk:
        """Fold ``merge_ids`` into ``survivor_id``; the survivor absorbs their pointers.

        The survivor and every merged chunk must be ``ready`` (unacquired) — grouping is
        not batching and never reshapes running work. The merged
        chunks' PM pointers are appended to the survivor (union), and each merged
        chunk records a ``chunk.grouped`` fact, becoming ephemeral.
        """
        survivor = self._require_ready_chunk(survivor_id)
        targets = self._resolve_targets(survivor_id, merge_ids)

        now = self._clock.now()
        for target in targets:
            self._chunks.add_pm_pointers(survivor_id, target.pm_pointers, at=now)
            self._chunks.record_grouped(target.chunk_id, grouped_into=survivor_id, at=now)
        _log.info("chunks grouped", survivor=survivor_id, merged=[t.chunk_id for t in targets], count=len(targets))
        merged = self._chunks.get(survivor_id)
        return merged if merged is not None else survivor

    def _resolve_targets(self, survivor_id: str, merge_ids: list[str]) -> list[Chunk]:
        seen: set[str] = set()
        targets: list[Chunk] = []
        for merge_id in merge_ids:
            if merge_id == survivor_id or merge_id in seen:
                continue  # self and duplicates are no-ops, not errors
            seen.add(merge_id)
            targets.append(self._require_ready_chunk(merge_id))
        return targets

    def _require_ready_chunk(self, chunk_id: str) -> Chunk:
        chunk = self._chunks.get(chunk_id)
        facts = self._chunks.load_facts(chunk_id)
        if chunk is None or facts is None:
            raise ChunkNotFound(chunk_id)
        status = derive_chunk_status(facts if facts is not None else ChunkFacts(minted=True))
        if status is not ChunkStatus.READY:
            raise ChunkNotReady(chunk_id, status)
        return chunk
