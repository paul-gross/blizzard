"""Queue-shaping domain — ``ready``/``not_ready`` reordering and grouping.

Order derives from appended position facts; grouping folds work refs into the survivor
and discards the rest as ephemeral. Neither touches an acquired chunk, but their scopes
differ (issue #141): grouping needs only an unheld chunk, while reordering ranks the
``ready`` queue and ``not_ready`` list independently (``bzh:ranking-is-per-list``)."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.chunks.dependencies import IWriteChunkDependenciesRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.queue import IWriteChunkQueueRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunks.work_refs import IWriteChunkWorkRefsRepository
from blizzard.hub.domain.dependencies import plan_fold, would_close_a_cycle
from blizzard.hub.domain.work import Chunk

_log = get_logger("blizzard.hub.queue")

# The fold's own fixed dependency-edge actor (D5, issue #460) — grouping stays
# actor-less on the wire; every edge a fold carries is stamped with this constant.
FOLD_ACTOR = "fold"


class QueueList(Enum):
    """Which of the two independently-ranked lists a queue op targets
    (``bzh:ranking-is-per-list``) — never mixed into one order."""

    READY = "ready"
    NOT_READY = "not_ready"


class ChunkNotFound(LookupError):
    """A named chunk does not exist (or was grouped or deleted away)."""

    def __init__(self, chunk_id: str) -> None:
        super().__init__(f"unknown chunk {chunk_id}")
        self.chunk_id = chunk_id


class ChunkNotGroupable(ValueError):
    """A group op named a chunk that is not free to be folded away (issue #141) — outside
    :data:`~blizzard.foundation.chunk_status.PRE_CLAIM_STATUSES`, the pre-claim window."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(
            f"chunk {chunk_id} is {status.value} — grouping needs a chunk at "
            f"{' or '.join(sorted(s.value for s in PRE_CLAIM_STATUSES))}: "
            "no runner holding it, and no human hold or terminal on it either"
        )
        self.chunk_id = chunk_id
        self.status = status


class FoldWouldCloseCycle(Exception):
    """Folding ``merge_ids`` into ``survivor_id`` would close a cycle in the resulting
    standing dependency graph (issue #460) — refused before any write, a set-level
    question over the whole fold rather than per edge."""

    def __init__(self, survivor_id: str, folded_chunk_ids: list[str]) -> None:
        super().__init__(
            f"folding {', '.join(folded_chunk_ids)} into {survivor_id} would close a cycle "
            "in the standing dependency graph"
        )
        self.survivor_id = survivor_id
        self.folded_chunk_ids = folded_chunk_ids


class QueueService:
    """Reorder the ``ready`` queue and the ``not_ready`` list, each as its own explicit
    hub-side property, ranked independently (``bzh:ranking-is-per-list``)."""

    def __init__(self, *, queue: IWriteChunkQueueRepository, record: IReadChunkRecordRepository, clock: IClock) -> None:
        self._queue = queue
        self._record = record
        self._clock = clock

    def ordered(self, list_: QueueList) -> list[Chunk]:
        """``list_``'s chunks in order — ascending by effective position."""
        positions = self._queue.queue_positions()
        promoted_ats = self._queue.promoted_ats()
        candidates = self._candidates(list_)
        return sorted(candidates, key=lambda c: self._effective_position(c, positions, promoted_ats))

    def ordered_ready(self) -> list[Chunk]:
        """Ready chunks in queue order — ascending by effective position."""
        return self.ordered(QueueList.READY)

    def ordered_not_ready(self) -> list[Chunk]:
        """``not_ready`` chunks in backlog order — ascending by effective position."""
        return self.ordered(QueueList.NOT_READY)

    def replace_order(self, list_: QueueList, ordered: list[Chunk]) -> None:
        """Idempotent whole-order replacement: one ascending explicit position fact per
        chunk in ``ordered``, front to back. Takes already-resolved ``Chunk`` objects,
        never ids (``bzh:domain-takes-objects``). ``list_`` only selects which store
        write routes the position (guarded for ``not_ready``, see :meth:`_write_fn`) —
        the list itself is never read here."""
        write = self._write_fn(list_)
        at = self._clock.now()
        for position, chunk in enumerate(ordered):
            write(chunk.chunk_id, position=float(position), at=at)
        _log.info("queue order replaced", list=list_.value, chunk_ids=[c.chunk_id for c in ordered])

    def reposition(self, list_: QueueList, chunk: Chunk, after: Chunk | None) -> None:
        """Single-chunk fractional reorder within ``list_``: stamp ``chunk`` a new
        explicit position immediately after ``after`` (top when ``after is None``),
        without restamping every other chunk in the list (issue #137). Repeated midpoint
        bisection eventually exhausts the representable doubles between two neighbours;
        that case renormalizes via :meth:`replace_order` and recomputes the midpoint."""
        write = self._write_fn(list_)
        positions = self._queue.queue_positions()
        promoted_ats = self._queue.promoted_ats()
        candidates = [c for c in self._candidates(list_) if c.chunk_id != chunk.chunk_id]
        ordered = sorted(candidates, key=lambda c: self._effective_position(c, positions, promoted_ats))

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
                    self.replace_order(list_, renormalized)
                    positions = self._queue.queue_positions()
                    promoted_ats = self._queue.promoted_ats()
                    after_pos = self._effective_position(after, positions, promoted_ats)
                    next_pos = self._effective_position(next_chunk, positions, promoted_ats)
                new_position = (after_pos + next_pos) / 2

        write(chunk.chunk_id, position=new_position, at=self._clock.now())
        _log.info(
            "queue chunk repositioned",
            list=list_.value,
            chunk_id=chunk.chunk_id,
            after_chunk_id=after.chunk_id if after is not None else None,
            position=new_position,
        )

    def _write_fn(self, list_: QueueList) -> Callable[..., None]:
        """The one place :meth:`replace_order`/:meth:`reposition` pick which store write
        routes a position — ``not_ready`` through the promoted-guarded
        :meth:`~blizzard.hub.domain.chunks.queue.IWriteChunkQueueRepository.record_backlog_position`,
        ``ready`` through :meth:`~blizzard.hub.domain.chunks.queue.IWriteChunkQueueRepository.record_queue_position`."""
        if list_ is QueueList.NOT_READY:
            return self._queue.record_backlog_position
        return self._queue.record_queue_position

    def _candidates(self, list_: QueueList) -> list[Chunk]:
        """``list_``'s repository read — the one place :meth:`ordered`/:meth:`reposition`
        pick which of the two independently-ranked lists (``bzh:ranking-is-per-list``)
        they read candidates from."""
        return self._record.list_ready() if list_ is QueueList.READY else self._record.list_not_ready()

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
    """Merge unacquired chunks — ``not_ready`` or ``ready`` — into one surviving chunk,
    carrying each folded chunk's standing dependency edges onto the survivor (D1-D4,
    issue #460)."""

    def __init__(
        self,
        *,
        work_refs: IWriteChunkWorkRefsRepository,
        dependencies: IWriteChunkDependenciesRepository,
        record: IReadChunkRecordRepository,
        facts: IReadChunkFactsRepository,
        clock: IClock,
        claim_lock: threading.Lock,
    ) -> None:
        self._work_refs = work_refs
        self._dependencies = dependencies
        self._record = record
        self._facts = facts
        self._clock = clock
        # The same lock ClaimService/EditService/RestartService/DependencyService/
        # DeleteService already share (issue #120) — closes the residual GroupService
        # previously left open against a racing declare (D2, issue #460).
        self._claim_lock = claim_lock

    def group(self, survivor_id: str, merge_ids: list[str]) -> GroupResult:
        """Fold ``merge_ids`` into ``survivor_id``; the survivor absorbs their pointers
        and each folded chunk's standing dependency edges (D1-D3). Refused before any
        write when the result would close a cycle (:class:`FoldWouldCloseCycle`)."""
        with self._claim_lock:
            return self._group_locked(survivor_id, merge_ids)

    def _group_locked(self, survivor_id: str, merge_ids: list[str]) -> GroupResult:
        survivor, survivor_status = self._require_unacquired_chunk(survivor_id)
        targets = self._resolve_targets(survivor_id, merge_ids)
        folded_ids = [t.chunk_id for t in targets]

        standing = self._dependencies.list_standing_edges()
        plan = plan_fold(standing, survivor_id, folded_ids)
        minted_pairs = [pair for cid in folded_ids for pair in plan.mint_by_target[cid]]
        if would_close_a_cycle(plan.remaining, minted_pairs):
            raise FoldWouldCloseCycle(survivor_id, folded_ids)

        now = self._clock.now()
        grouped_id: int | None = None
        for target in targets:
            self._work_refs.add_work_refs(survivor_id, target.work_refs, at=now)
            grouped_id = self._dependencies.record_fold(
                target.chunk_id,
                grouped_into=survivor_id,
                release=plan.release_by_target[target.chunk_id],
                mint=plan.mint_by_target[target.chunk_id],
                by=FOLD_ACTOR,
                at=now,
            )
        _log.info(
            "chunks grouped",
            survivor=survivor_id,
            status=survivor_status.value,
            merged=folded_ids,
            count=len(targets),
        )
        merged = self._record.get(survivor_id)
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
        chunk = self._record.get(chunk_id)
        facts = self._facts.load_facts(chunk_id)
        if chunk is None or facts is None:
            raise ChunkNotFound(chunk_id)
        status = facts.status()
        if status not in PRE_CLAIM_STATUSES:
            raise ChunkNotGroupable(chunk_id, status)
        return chunk, status
