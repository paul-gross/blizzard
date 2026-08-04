"""Queue-shaping domain — ready-queue reordering and grouping.

The two operator actions that shape the queue rather than execute work
: **Prioritize** (replace the whole ready order) and
**Group** (merge unacquired chunks into one surviving chunk). Both are pure hub-side
properties over the fact store — order derives from appended position facts,
and grouping folds work refs into the survivor and discards the rest as ephemeral.
Neither touches an acquired chunk, but their scopes differ (issue #141): reordering *is*
the ready queue's own order, so it is ready-only by definition, while grouping needs only
that **no runner holds** the chunk — a merge is not an execution, and a backlog chunk
should not have to become claimable to take part in one.

Pure-ish domain services (``bzh:controller-read-only``): they hold the write chunk
repository and the injected clock, validate against the **derived** status (never a
stored column), and raise typed errors the controller maps to HTTP.
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


#: The statuses a chunk may be **grouped** at (issue #141): the two
#: :func:`~blizzard.hub.domain.work.derive_chunk_status` falls through to when a chunk
#: is neither claimed nor parked nor finished.
#:
#: Deliberately a status set rather than a route-liveness read: ``paused``,
#: ``waiting_on_human`` and ``needs_human`` are all reachable with no live route at all,
#: and each is a standing human hold a group must not fold a chunk out from under
#: (pinned by
#: tests/test_queue_shaping.py::test_group_refuses_a_paused_backlog_chunk_without_claiming_a_runner_holds_it).
GROUPABLE_STATUSES = frozenset({ChunkStatus.NOT_READY, ChunkStatus.READY})


class ChunkNotGroupable(ValueError):
    """A group op named a chunk that is not free to be folded away (issue #141).

    The message names what is **actually enforced** rather than the motivating half of
    it: "no runner holds it" alone would be a false claim on a refused ``paused`` chunk.
    """

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
        """Idempotent whole-order replacement: append one ascending explicit
        position fact per chunk in ``ordered``, front to back.

        Takes already-resolved ``Chunk`` objects, never ids (``bzh:domain-takes-objects``);
        the caller has already validated them against the ready set, so this records
        ranks only. Ranking every chunk in ``ordered`` (not just the named ones)
        keeps the result deterministic regardless of positions left over from
        an earlier reorder."""
        at = self._clock.now()
        for position, chunk in enumerate(ordered):
            self._chunks.record_queue_position(chunk.chunk_id, position=float(position), at=at)
        _log.info("ready queue replaced", chunk_ids=[c.chunk_id for c in ordered])

    def reposition(self, chunk: Chunk, after: Chunk | None) -> None:
        """Single-chunk fractional reorder: stamp ``chunk`` a new explicit position that
        lands it immediately after ``after`` (or at the very top when ``after is None``),
        without restamping every other ready chunk (issue #137's board drag-and-drop).

        Takes already-resolved ``Chunk`` objects, never ids (``bzh:domain-takes-objects``);
        the caller has already resolved and validated both ``chunk`` and ``after``
        against the current ready set.

        Reads the same effective-position machinery :meth:`ordered_ready` does, then
        excludes ``chunk`` itself from the ordering it anchors against — a chunk being
        moved must never count as its own neighbour.

        Floats are finite: repeated midpoint bisection between the same two neighbours
        eventually has no representable double strictly between them
        (:func:`math.nextafter` guards this). When that happens, this renormalizes —
        restamps every currently-ready chunk with dense ascending positions via
        :meth:`replace_order`, ``chunk`` included at its new logical slot — and then
        recomputes the midpoint against the freshly-spread neighbour values, which are
        always a whole float apart and so always have room.
        """
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
        else its mint instant (issue #137).

        Before a chunk is ever moved, its position falls back to its
        ``chunk_promoted.promoted_at`` as a unix timestamp if it has been promoted, or its
        ``minted_at`` otherwise — so a chunk minted long ago but promoted late still
        sorts at the tail rather than mid-queue, and a never-promoted chunk still falls
        back to plain FIFO by mint order. Any explicit move (a smaller float) lifts a
        chunk above the un-moved tail either way.

        :class:`~blizzard.hub.domain.promote.PromoteService` reuses this same fallback
        for its own tail-stamp arithmetic.
        """
        explicit = positions.get(chunk.chunk_id)
        if explicit is not None:
            return explicit
        promoted_at = promoted_ats.get(chunk.chunk_id)
        return promoted_at.timestamp() if promoted_at is not None else chunk.minted_at.timestamp()


@dataclass(frozen=True)
class GroupResult:
    """A completed group: the survivor and the status it is left at (issue #141).

    The survivor's status rides along because grouping does not imply ``ready``:
    folding backlog chunks yields a backlog survivor, so a caller announcing the change
    must publish what the survivor *is* rather than a constant.
    """

    survivor: Chunk
    status: ChunkStatus
    # The last ``chunk_grouped.id`` this call wrote (issue #213) — ``None`` when
    # ``merge_ids`` resolved to zero targets (every id was the survivor itself or a
    # duplicate, a no-op). A multi-chunk merge writes one row per target; this names
    # the newest.
    grouped_id: int | None = None


class GroupService:
    """Merge unacquired chunks — ``not_ready`` or ``ready`` — into one surviving chunk."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def group(self, survivor_id: str, merge_ids: list[str]) -> GroupResult:
        """Fold ``merge_ids`` into ``survivor_id``; the survivor absorbs their pointers.

        The survivor and every merged chunk must be **unacquired**
        (:data:`GROUPABLE_STATUSES`) — grouping is not batching and never reshapes work a
        runner holds. It does not require ``ready``: an operator merging three backlog
        chunks should not have to promote them into the claimable queue first, which
        exposes each of them to a live runner mid-flow purely to satisfy a merge
        (issue #141). The merged chunks' work refs are appended to the survivor (union),
        and each merged chunk records a ``chunk.grouped`` fact, becoming ephemeral.

        The survivor's own status is **unchanged** by grouping: no promote fact is
        written here, so a ``not_ready`` survivor stays ``not_ready`` and a ``ready`` one
        stays ``ready``. Promotion stays a separate, deliberate act.
        """
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
