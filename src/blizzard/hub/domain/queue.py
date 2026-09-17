"""Queue-shaping domain — ``ready``/``not_ready`` reordering and grouping.

Order derives from appended position facts; grouping folds work refs into the survivor
and discards the rest as ephemeral. Neither touches an acquired chunk, but their scopes
differ (issue #141): grouping needs only an unheld chunk, while reordering ranks the
``ready`` queue and ``not_ready`` list independently (``bzh:ranking-is-per-list``)."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.chunks.dependencies import FoldTarget, IWriteChunkDependenciesRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.queue import IWriteChunkQueueRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunks.work_refs import IWriteChunkWorkRefsRepository
from blizzard.hub.domain.dependencies import plan_fold, would_close_a_cycle
from blizzard.hub.domain.eligibility import EligibilityCheck
from blizzard.hub.domain.errors import ChunkNotFound
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.pagination import MalformedCursor, decode_cursor, encode_cursor
from blizzard.hub.domain.registry import RunnerCapability
from blizzard.hub.domain.work import Chunk, ChunkFacts

_log = get_logger("blizzard.hub.queue")

# Grouping stays actor-less on the wire; every fold edge is stamped with this fixed actor (D5, issue #460).
FOLD_ACTOR = "fold"


class QueueList(Enum):
    """Which of the two independently-ranked lists a queue op targets
    (``bzh:ranking-is-per-list``) — never mixed into one order."""

    READY = "ready"
    NOT_READY = "not_ready"


class QueueMatchPolicy(Enum):
    """The matched fleet peek's hold-or-pass-over policy (D8) — applied to the
    capability-eligibility and blocked-dependency dimensions together, never one alone.
    :meth:`of` never raises: an unrecognized wire value reads as :attr:`PASS_OVER`
    (``docs/versioning.md``'s round-trip-the-unrecognized rule)."""

    HOLD = "hold"
    PASS_OVER = "pass-over"

    @classmethod
    def of(cls, value: str) -> QueueMatchPolicy:
        return cls.HOLD if value == cls.HOLD.value else cls.PASS_OVER


@dataclass(frozen=True)
class MatchedEntry:
    """The one ready chunk :func:`select_matched_entry` returns, at its own position in
    the unmutated ready order (D8) — the order itself is never reshaped, only scanned
    past."""

    chunk: Chunk
    position: int


def _capability_ineligible(
    chunk: Chunk, graph: Graph, facts: ChunkFacts | None, capabilities: Sequence[RunnerCapability]
) -> bool:
    """Whether ``capabilities`` cannot work ``chunk``'s current node. Asserting no
    capabilities at all — an empty snapshot, or a request declaring none — applies no
    filter, matching the legacy peek's unfiltered reach-ahead for the head entry; this is
    a deliberate divergence from :class:`EligibilityCheck` itself, which reads an empty
    snapshot as satisfying nothing."""
    if not capabilities:
        return False
    node_id = (facts.current_node_id() if facts is not None else None) or graph.entry_node_id
    node = graph.node_by_id(node_id)
    if node is None:  # pragma: no cover - a pinned graph always resolves its own node
        return True
    return not EligibilityCheck(chunk, graph, node, capabilities).eligible


def select_matched_entry(
    chunks: Sequence[Chunk],
    *,
    graphs: Mapping[str, Graph],
    facts: Mapping[str, ChunkFacts],
    capabilities: Sequence[RunnerCapability],
    blocked: Mapping[str, list[str]],
    policy: QueueMatchPolicy,
) -> MatchedEntry | None:
    """The matched peek's own selection: the first entry in ``chunks``'s order the caller
    can both work (capability-eligible) and claim (not dependency-blocked). Moved hub-side
    so a runner passing over an entry locally and re-peeking isn't handed it again. Under
    :attr:`QueueMatchPolicy.HOLD` only the head is examined; :attr:`PASS_OVER` scans the
    whole order."""
    for position, chunk in enumerate(chunks):
        graph = graphs.get(chunk.graph_id)
        if graph is None:  # pragma: no cover - a pinned graph always resolves
            unusable = True
        else:
            unusable = chunk.chunk_id in blocked or _capability_ineligible(
                chunk, graph, facts.get(chunk.chunk_id), capabilities
            )
        if not unusable:
            return MatchedEntry(chunk=chunk, position=position)
        if policy is QueueMatchPolicy.HOLD:
            return None
    return None


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


def _decode_queue_cursor(cursor: str) -> tuple[float, str]:
    """``QueueService.page``'s whole cursor format: an effective-position/chunk_id pair
    (blizzard#526 D4)."""
    parts = decode_cursor(cursor)
    if (
        len(parts) != 2
        or isinstance(parts[0], bool)
        or not isinstance(parts[0], int | float)
        or not isinstance(parts[1], str)
    ):
        raise MalformedCursor(cursor)
    return float(parts[0]), parts[1]


@dataclass(frozen=True)
class QueueEntry:
    """One paged queue/backlog row (blizzard#526 D4) — the chunk plus its absolute
    0-based whole-list position, so drained pages read ``0…n-1`` like an unpaginated peek."""

    chunk: Chunk
    position: int


@dataclass(frozen=True)
class QueuePage:
    """A bounded, keyset-paginated page of :meth:`QueueService.page` (blizzard#526 D4) —
    ``next_cursor`` is ``None`` exactly when this page is the last one."""

    entries: list[QueueEntry]
    next_cursor: str | None


class QueueService:
    """Reorder the ``ready`` queue and the ``not_ready`` list, each as its own explicit
    hub-side property, ranked independently (``bzh:ranking-is-per-list``)."""

    def __init__(self, *, queue: IWriteChunkQueueRepository, record: IReadChunkRecordRepository, clock: IClock) -> None:
        self._queue = queue
        self._record = record
        self._clock = clock

    def ordered(self, list_: QueueList, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        """``list_``'s chunks in order — ascending by effective position, ``chunk_id``
        breaking a same-instant tie (blizzard#526 D4). ``statuses`` is the caller's own
        already-derived fleet statuses (``load_all_statuses()``), never re-derived here."""
        positions = self._queue.queue_positions()
        promoted_ats = self._queue.promoted_ats()
        candidates = self._candidates(list_, statuses=statuses)
        return sorted(candidates, key=lambda c: (self._effective_position(c, positions, promoted_ats), c.chunk_id))

    def page(
        self,
        list_: QueueList,
        *,
        statuses: Mapping[str, ChunkStatus],
        cursor: str | None = None,
        limit: int,
    ) -> QueuePage:
        """``list_``'s chunks bounded and keyset-paginated (blizzard#526 D4/D7); the
        keyset applies over :meth:`ordered`'s already-materialized order, not a second
        SQL read. ``position`` is each entry's absolute index in the whole list, so a
        since-repositioned cursor chunk still resumes by key. ``cursor`` is a prior
        :attr:`QueuePage.next_cursor`, else raising :class:`~blizzard.hub.domain.pagination.MalformedCursor`."""
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        positions = self._queue.queue_positions()
        promoted_ats = self._queue.promoted_ats()
        candidates = self._candidates(list_, statuses=statuses)
        keyed = sorted((self._effective_position(c, positions, promoted_ats), c.chunk_id, c) for c in candidates)
        after = _decode_queue_cursor(cursor) if cursor is not None else None
        entries: list[QueueEntry] = []
        entry_keys: list[tuple[float, str]] = []
        for index, (effective_position, chunk_id, chunk) in enumerate(keyed):
            if after is not None and (effective_position, chunk_id) <= after:
                continue
            entries.append(QueueEntry(chunk=chunk, position=index))
            entry_keys.append((effective_position, chunk_id))
            if len(entries) == limit + 1:
                break
        page_entries = entries[:limit]
        next_cursor = encode_cursor(*entry_keys[limit - 1]) if len(entries) > limit else None
        return QueuePage(entries=page_entries, next_cursor=next_cursor)

    def ordered_ready(self, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        """Ready chunks in queue order — ascending by effective position."""
        return self.ordered(QueueList.READY, statuses=statuses)

    def ordered_not_ready(self, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        """``not_ready`` chunks in backlog order — ascending by effective position."""
        return self.ordered(QueueList.NOT_READY, statuses=statuses)

    def replace_order(self, list_: QueueList, ordered: list[Chunk]) -> None:
        """Idempotent whole-order replacement: one ascending explicit position fact per
        chunk in ``ordered``, front to back, in one write transaction. Takes
        already-resolved ``Chunk`` objects, never ids (``bzh:domain-takes-objects``).
        ``list_`` only selects which store write routes the positions (guarded for
        ``not_ready``, see :meth:`_write_fn`) — the list itself is never read here."""
        write = self._write_fn(list_)
        write([(chunk.chunk_id, float(position)) for position, chunk in enumerate(ordered)], at=self._clock.now())
        _log.info("queue order replaced", list=list_.value, chunk_ids=[c.chunk_id for c in ordered])

    def reposition(
        self, list_: QueueList, chunk: Chunk, after: Chunk | None, *, statuses: Mapping[str, ChunkStatus]
    ) -> None:
        """Single-chunk fractional reorder within ``list_``: stamp ``chunk`` a new
        explicit position immediately after ``after`` (top when ``after is None``),
        without restamping every other chunk (issue #137). Bisection exhausting the
        representable doubles renormalizes via :meth:`replace_order`; ``statuses`` is
        the caller's own already-derived fleet statuses, reused as-is throughout."""
        write = self._write_fn(list_)
        positions = self._queue.queue_positions()
        promoted_ats = self._queue.promoted_ats()
        candidates = [c for c in self._candidates(list_, statuses=statuses) if c.chunk_id != chunk.chunk_id]
        ordered = sorted(candidates, key=lambda c: (self._effective_position(c, positions, promoted_ats), c.chunk_id))

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

        write([(chunk.chunk_id, new_position)], at=self._clock.now())
        _log.info(
            "queue chunk repositioned",
            list=list_.value,
            chunk_id=chunk.chunk_id,
            after_chunk_id=after.chunk_id if after is not None else None,
            position=new_position,
        )

    def _write_fn(self, list_: QueueList) -> Callable[..., None]:
        """The one place :meth:`replace_order`/:meth:`reposition` pick which store write
        routes a batch of positions — ``not_ready`` through the promoted-guarded
        :meth:`~blizzard.hub.domain.chunks.queue.IWriteChunkQueueRepository.record_backlog_positions`,
        ``ready`` through
        :meth:`~blizzard.hub.domain.chunks.queue.IWriteChunkQueueRepository.record_queue_positions`."""
        if list_ is QueueList.NOT_READY:
            return self._queue.record_backlog_positions
        return self._queue.record_queue_positions

    def _candidates(self, list_: QueueList, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        """``list_``'s repository read — the one place :meth:`ordered`/:meth:`reposition`
        pick which of the two independently-ranked lists (``bzh:ranking-is-per-list``)
        they read candidates from."""
        if list_ is QueueList.READY:
            return self._record.list_ready(statuses=statuses)
        return self._record.list_not_ready(statuses=statuses)

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
    # The last ``chunk_grouped.id`` this call wrote; ``None`` when ``merge_ids`` resolved to zero targets (issue #213).
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
        # The same lock ClaimService/EditService/RestartService/DependencyService/DeleteService already share — closes
        # the residual GroupService previously left open against a racing declare (D2).
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
        for target in targets:
            self._work_refs.add_work_refs(survivor_id, target.work_refs, at=now)

        grouped_id: int | None = None
        if targets:
            fold_targets = [
                FoldTarget(
                    chunk_id=target.chunk_id,
                    release=plan.release_by_target[target.chunk_id],
                    mint=plan.mint_by_target[target.chunk_id],
                )
                for target in targets
            ]
            # One call, one transaction across every target (D4, issue #460) — a target's
            # own row can never commit ahead of a sibling's edge release/mint.
            grouped_ids = self._dependencies.record_fold(fold_targets, grouped_into=survivor_id, by=FOLD_ACTOR, at=now)
            grouped_id = grouped_ids[targets[-1].chunk_id]
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
