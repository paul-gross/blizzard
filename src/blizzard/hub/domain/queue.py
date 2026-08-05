"""Queue-shaping domain — ready-queue reordering and grouping.

Order derives from appended position facts; grouping folds work refs into the survivor
and discards the rest as ephemeral. Neither touches an acquired chunk, but their scopes
differ (issue #141): reordering is ready-only, grouping needs only an unheld chunk.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

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


#: The statuses a chunk may be **grouped** at (issue #141) — a status set, not a
#: route-liveness read (pinned by ``tests/test_queue_shaping.py``).
GROUPABLE_STATUSES = frozenset({ChunkStatus.NOT_READY, ChunkStatus.READY})


class ChunkNotGroupable(ValueError):
    """A group op named a chunk that is not free to be folded away (issue #141)."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(
            f"chunk {chunk_id} is {status.value} — grouping needs a chunk at "
            f"{' or '.join(sorted(s.value for s in GROUPABLE_STATUSES))}: "
            "no runner holding it, and no human hold or terminal on it either"
        )
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
        promoted_ats = self._chunks.promoted_ats()
        ready = self._chunks.list_ready()
        return sorted(ready, key=lambda c: self._effective_position(c, positions, promoted_ats))

    def replace_order(self, ordered: list[Chunk]) -> None:
        """Idempotent whole-order replacement: one ascending explicit position fact per
        chunk in ``ordered``, front to back. Takes already-resolved ``Chunk`` objects,
        never ids (``bzh:domain-takes-objects``); ranking every chunk keeps the result
        deterministic regardless of positions left over from an earlier reorder."""
        at = self._clock.now()
        for position, chunk in enumerate(ordered):
            self._chunks.record_queue_position(chunk.chunk_id, position=float(position), at=at)
        _log.info("ready queue replaced", chunk_ids=[c.chunk_id for c in ordered])

    def reposition(self, chunk: Chunk, after: Chunk | None) -> None:
        """Single-chunk fractional reorder: stamp ``chunk`` a new explicit position
        immediately after ``after`` (top when ``after is None``), without restamping
        every other ready chunk (issue #137). Repeated midpoint bisection eventually
        exhausts the representable doubles between two neighbours; that case
        renormalizes via :meth:`replace_order` and recomputes the midpoint."""
        positions = self._chunks.queue_positions()
        promoted_ats = self._chunks.promoted_ats()
        ready = [c for c in self._chunks.list_ready() if c.chunk_id != chunk.chunk_id]
        ordered = sorted(ready, key=lambda c: self._effective_position(c, positions, promoted_ats))

        if after is None:
            new_position = self._effective_position(ordered[0], positions, promoted_ats) - 1.0 if ordered else 0.0
        else:
            after_index = next(i for i, c in enumerate(ordered) if c.chunk_id == after.chunk_id)
            after_pos = self._effective_position(after, positions, promoted_ats)
            if after_index == len(ordered) - 1:
                new_position = after_pos + 1.0
            else:
                next_chunk = ordered[after_index + 1]
                next_pos = self._effective_position(next_chunk, positions, promoted_ats)
                if math.nextafter(after_pos, next_pos) >= next_pos:
                    renormalized = [*ordered[: after_index + 1], chunk, *ordered[after_index + 1 :]]
                    self.replace_order(renormalized)
                    positions = self._chunks.queue_positions()
                    promoted_ats = self._chunks.promoted_ats()
                    after_pos = self._effective_position(after, positions, promoted_ats)
                    next_pos = self._effective_position(next_chunk, positions, promoted_ats)
                new_position = (after_pos + next_pos) / 2

        self._chunks.record_queue_position(chunk.chunk_id, position=new_position, at=self._clock.now())
        _log.info(
            "ready queue chunk repositioned",
            chunk_id=chunk.chunk_id,
            after_chunk_id=after.chunk_id if after is not None else None,
            position=new_position,
        )

    @staticmethod
    def _effective_position(chunk: Chunk, positions: dict[str, float], promoted_ats: dict[str, datetime]) -> float:
        """A chunk's sort key: its newest explicit position, else its promotion instant,
        else its mint instant (issue #137). The fallback is a unix timestamp, so a chunk
        minted long ago but promoted late still sorts at the tail rather than mid-queue.
        """
        explicit = positions.get(chunk.chunk_id)
        if explicit is not None:
            return explicit
        promoted_at = promoted_ats.get(chunk.chunk_id)
        return promoted_at.timestamp() if promoted_at is not None else chunk.minted_at.timestamp()


@dataclass(frozen=True)
class GroupResult:
    """A completed group: the survivor and the status it is left at (issue #141).

    The status rides along because grouping does not imply ``ready``: folding backlog
    chunks yields a backlog survivor."""

    survivor: Chunk
    status: ChunkStatus
    # The last ``chunk_grouped.id`` this call wrote (issue #213) — ``None`` when
    # ``merge_ids`` resolved to zero targets.
    grouped_id: int | None = None


class GroupService:
    """Merge unacquired chunks — ``not_ready`` or ``ready`` — into one surviving chunk."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def group(self, survivor_id: str, merge_ids: list[str]) -> GroupResult:
        """Fold ``merge_ids`` into ``survivor_id``; the survivor absorbs their pointers.

        The survivor and every merged chunk must be **unacquired**
        (:data:`GROUPABLE_STATUSES`); ``ready`` is not required (issue #141). Merged work
        refs union into the survivor, whose own status is unchanged."""
        survivor, survivor_status = self._require_unacquired_chunk(survivor_id)
        targets = self._resolve_targets(survivor_id, merge_ids)

        now = self._clock.now()
        grouped_id: int | None = None
        for target in targets:
            self._chunks.add_work_refs(survivor_id, target.work_refs, at=now)
            grouped_id = self._chunks.record_grouped(target.chunk_id, grouped_into=survivor_id, at=now)
        _log.info(
            "chunks grouped",
            survivor=survivor_id,
            status=survivor_status.value,
            merged=[t.chunk_id for t in targets],
            count=len(targets),
        )
        merged = self._chunks.get(survivor_id)
        return GroupResult(
            survivor=merged if merged is not None else survivor, status=survivor_status, grouped_id=grouped_id
        )

    def _resolve_targets(self, survivor_id: str, merge_ids: list[str]) -> list[Chunk]:
        seen: set[str] = set()
        targets: list[Chunk] = []
        for merge_id in merge_ids:
            if merge_id == survivor_id or merge_id in seen:
                continue  # self and duplicates are no-ops, not errors
            seen.add(merge_id)
            targets.append(self._require_unacquired_chunk(merge_id)[0])
        return targets

    def _require_unacquired_chunk(self, chunk_id: str) -> tuple[Chunk, ChunkStatus]:
        chunk = self._chunks.get(chunk_id)
        facts = self._chunks.load_facts(chunk_id)
        if chunk is None or facts is None:
            raise ChunkNotFound(chunk_id)
        status = derive_chunk_status(facts if facts is not None else ChunkFacts(minted=True))
        if status not in GROUPABLE_STATUSES:
            raise ChunkNotGroupable(chunk_id, status)
        return chunk, status
