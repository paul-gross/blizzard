"""Queue-shaping domain — ``ready``/``not_ready`` reordering and grouping.

Order derives from appended position facts; grouping folds work refs into the survivor
and discards the rest as ephemeral. Neither touches an acquired chunk, but their scopes
differ: grouping needs only an unheld chunk, while reordering ranks the
``ready`` queue and ``not_ready`` list independently (``bzh:ranking-is-per-list``)."""

from __future__ import annotations

import math

# The residual cycle-check lock — recorded debt, blizzard-context:/architecture/system-shape/exclusive-writes.md
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model, dto
from blizzard.hub.domain.chunk.dependencies import FoldEdgePlan, plan_fold, would_close_a_cycle
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb, DependencyEdge, verb_legal_from
from blizzard.hub.domain.chunk.ports.dependencies import FoldTarget, IWriteChunkDependenciesRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunk.ports.queue import IWriteChunkQueueRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.work_refs import IWriteChunkWorkRefsRepository
from blizzard.hub.domain.execution.eligibility import EligibilityCheck
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.kernel.pagination import MalformedCursor, decode_cursor, encode_cursor
from blizzard.hub.domain.runners.registration import RunnerCapability

_log = get_logger("blizzard.hub.queue")

# Grouping stays actor-less on the wire; every fold edge is stamped with this fixed actor.
FOLD_ACTOR = "fold"


class QueueList(Enum):
    """Which of the two independently-ranked lists a queue op targets
    (``bzh:ranking-is-per-list``) — never mixed into one order."""

    READY = "ready"
    NOT_READY = "not_ready"


class QueueMatchPolicy(Enum):
    """The matched fleet peek's hold-or-pass-over policy — applied to the
    capability-eligibility and blocked-dependency dimensions together, never one alone.
    :meth:`of` never raises: an unrecognized wire value reads as :attr:`PASS_OVER`
    (``docs/versioning.md``'s round-trip-the-unrecognized rule)."""

    HOLD = "hold"
    PASS_OVER = "pass-over"

    @classmethod
    def of(cls, value: str) -> QueueMatchPolicy:
        return cls.HOLD if value == cls.HOLD.value else cls.PASS_OVER


@dto
@dataclass(frozen=True)
class MatchedEntry:
    """The one ready chunk :func:`select_matched_entry` returns, at its own position in
    the unmutated ready order — the order itself is never reshaped, only scanned
    past."""

    chunk: Chunk
    position: int


def _capability_ineligible(
    chunk: Chunk, graph: Graph, facts: ChunkFacts | None, capabilities: Sequence[RunnerCapability]
) -> bool:
    """Whether ``capabilities`` cannot work ``chunk``'s current node. An empty
    snapshot applies no capability filter; pinned by
    ``test_no_capabilities_asserted_applies_no_capability_filter``."""
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
    can both work (capability-eligible) and claim (not dependency-blocked). Under
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
    """A group op named a chunk that is not free to be folded away — outside
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
    standing dependency graph — refused before any write, a set-level
    question over the whole fold rather than per edge."""

    def __init__(self, survivor_id: str, folded_chunk_ids: list[str]) -> None:
        super().__init__(
            f"folding {', '.join(folded_chunk_ids)} into {survivor_id} would close a cycle "
            "in the standing dependency graph"
        )
        self.survivor_id = survivor_id
        self.folded_chunk_ids = folded_chunk_ids


def _decode_queue_cursor(cursor: str) -> tuple[float, str]:
    """``QueueService.page``'s whole cursor format: an effective-position/chunk_id pair."""
    parts = decode_cursor(cursor)
    if (
        len(parts) != 2
        or isinstance(parts[0], bool)
        or not isinstance(parts[0], int | float)
        or not isinstance(parts[1], str)
    ):
        raise MalformedCursor(cursor)
    return float(parts[0]), parts[1]


@dto
@dataclass(frozen=True)
class QueueEntry:
    """One paged queue/backlog row — the chunk plus its absolute
    0-based whole-list position, so drained pages read ``0…n-1`` like an unpaginated peek."""

    chunk: Chunk
    position: int


@dto
@dataclass(frozen=True)
class QueuePage:
    """A bounded, keyset-paginated page of :meth:`QueueService.page` —
    ``next_cursor`` is ``None`` exactly when this page is the last one."""

    entries: list[QueueEntry]
    next_cursor: str | None


#: The verb a list's reorder admits its members by (``bzh:ranking-is-per-list``).
_REORDER_VERB: Mapping[QueueList, ChunkVerb] = {
    QueueList.READY: ChunkVerb.REORDER_READY,
    QueueList.NOT_READY: ChunkVerb.REORDER_BACKLOG,
}


def _other_list(list_: QueueList) -> QueueList:
    return QueueList.NOT_READY if list_ is QueueList.READY else QueueList.READY


class DuplicateReorderIds(ValueError):
    """A whole-order replacement named one chunk twice."""

    def __init__(self) -> None:
        super().__init__("chunk_ids must not repeat")


class SelfAnchoredMove(ValueError):
    """A single-chunk reorder named the chunk as its own anchor."""

    def __init__(self) -> None:
        super().__init__("after_chunk_id must not equal chunk_id")


class NotInList(Exception):
    """A reorder named a chunk outside the list it ranks (``bzh:ranking-is-per-list``).
    ``actual`` is the other list when the chunk stands there, else ``None``."""

    def __init__(self, chunk_id: str, expected: QueueList, actual: QueueList | None) -> None:
        detail = f"chunk {chunk_id} is not in the {expected.value} list"
        super().__init__(f"{detail} (it is {actual.value})" if actual is not None else detail)
        self.chunk_id = chunk_id
        self.expected = expected
        self.actual = actual

    @classmethod
    def of(cls, chunk_id: str, expected: QueueList, statuses: Mapping[str, ChunkStatus]) -> NotInList:
        """The refusal for ``chunk_id``, naming the other list when its status stands there."""
        status = statuses.get(chunk_id)
        other = _other_list(expected)
        in_other = status is not None and verb_legal_from(_REORDER_VERB[other], status)
        return cls(chunk_id, expected, other if in_other else None)


def replacement_order(
    list_: QueueList, current: Sequence[Chunk], chunk_ids: Sequence[str], statuses: Mapping[str, ChunkStatus]
) -> list[Chunk]:
    """The whole order a replacement of ``list_`` writes: the named chunks first, in the order
    named, then every unnamed member in its ``current`` order. Refuses a repeated id
    (:class:`DuplicateReorderIds`) before any id outside the list (:class:`NotInList`)."""
    if len(set(chunk_ids)) != len(chunk_ids):
        raise DuplicateReorderIds()
    by_id = {chunk.chunk_id: chunk for chunk in current}
    for chunk_id in chunk_ids:
        if chunk_id not in by_id:
            raise NotInList.of(chunk_id, list_, statuses)
    named = set(chunk_ids)
    return [by_id[chunk_id] for chunk_id in chunk_ids] + [c for c in current if c.chunk_id not in named]


def resolve_move(
    list_: QueueList,
    current: Sequence[Chunk],
    chunk_id: str,
    after_chunk_id: str | None,
    statuses: Mapping[str, ChunkStatus],
) -> tuple[Chunk, Chunk | None]:
    """The chunk a single-chunk reorder moves and the member it lands after (``None``: the top).
    Refuses a self-anchor (:class:`SelfAnchoredMove`) before either id outside the list."""
    if after_chunk_id == chunk_id:
        raise SelfAnchoredMove()
    by_id = {chunk.chunk_id: chunk for chunk in current}
    chunk = by_id.get(chunk_id)
    if chunk is None:
        raise NotInList.of(chunk_id, list_, statuses)
    if after_chunk_id is None:
        return chunk, None
    after = by_id.get(after_chunk_id)
    if after is None:
        raise NotInList.of(after_chunk_id, list_, statuses)
    return chunk, after


def require_anchor_among(
    list_: QueueList, candidates: Sequence[Chunk], after: Chunk | None, statuses: Mapping[str, ChunkStatus]
) -> None:
    """Refuse an ``after`` anchor that is not among ``candidates`` — the members of ``list_``
    other than the chunk being moved (:class:`NotInList`). ``None`` anchors at the top."""
    if after is not None and all(c.chunk_id != after.chunk_id for c in candidates):
        raise NotInList.of(after.chunk_id, list_, statuses)


@domain_model
@dataclass(frozen=True)
class QueueRanking:
    """The ranking inputs of one list's candidates — their newest explicit positions and
    promotion instants — and the order they derive (``bzh:ranking-is-per-list``)."""

    positions: Mapping[str, float]
    promoted_ats: Mapping[str, datetime]

    def effective_position(self, chunk: Chunk) -> float:
        """A chunk's sort key: its newest explicit position, else its promotion instant,
        else its mint instant. The fallback is a unix timestamp, so a chunk minted long ago
        but promoted late still sorts at the tail rather than mid-queue."""
        explicit = self.positions.get(chunk.chunk_id)
        if explicit is not None:
            return explicit
        promoted_at = self.promoted_ats.get(chunk.chunk_id)
        return promoted_at.timestamp() if promoted_at is not None else chunk.minted_at.timestamp()

    def ordered(self, candidates: Iterable[Chunk]) -> list[Chunk]:
        """``candidates`` ascending by effective position, ``chunk_id`` breaking a tie."""
        return sorted(candidates, key=lambda c: (self.effective_position(c), c.chunk_id))

    def tail(self, candidates: Sequence[Chunk]) -> float:
        """The position one past every candidate's effective position; ``0.0`` for none."""
        return max((self.effective_position(c) for c in candidates), default=-1.0) + 1.0

    def page(self, candidates: Iterable[Chunk], *, cursor: str | None, limit: int) -> QueuePage:
        """A keyset page of the ordered candidates after ``cursor``, ``limit`` long. Each
        entry carries its absolute index in the whole order, so a since-repositioned cursor
        chunk still resumes by key."""
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        keyed = sorted((self.effective_position(c), c.chunk_id, c) for c in candidates)
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
        next_cursor = encode_cursor(*entry_keys[limit - 1]) if len(entries) > limit else None
        return QueuePage(entries=entries[:limit], next_cursor=next_cursor)

    def slot_after(self, ordered: Sequence[Chunk], after: Chunk | None) -> float | None:
        """The fractional position landing a chunk right after ``after`` in ``ordered`` (the
        list without the moving chunk) — at the top when ``after`` is ``None``. ``None`` when
        the gap to the next member is exhausted in doubles: the list must be renormalized."""
        if after is None:
            return self.effective_position(ordered[0]) - 1.0 if ordered else 0.0
        after_index = next(i for i, c in enumerate(ordered) if c.chunk_id == after.chunk_id)
        after_pos = self.effective_position(after)
        if after_index == len(ordered) - 1:
            return after_pos + 1.0
        next_pos = self.effective_position(ordered[after_index + 1])
        if math.nextafter(after_pos, next_pos) >= next_pos:
            return None
        return (after_pos + next_pos) / 2


class QueueService:
    """Reorder the ``ready`` queue and the ``not_ready`` list, each as its own explicit
    hub-side property, ranked independently (``bzh:ranking-is-per-list``)."""

    def __init__(self, *, queue: IWriteChunkQueueRepository, record: IReadChunkRecordRepository, clock: IClock) -> None:
        self._queue = queue
        self._record = record
        self._clock = clock

    def ordered(self, list_: QueueList, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        """``list_``'s chunks in order — ascending by effective position, ``chunk_id``
        breaking a same-instant tie. ``statuses`` is the caller's own
        already-derived live fleet statuses (``load_live_statuses()``), never re-derived here."""
        candidates = self._candidates(list_, statuses=statuses)
        return self._ranking(candidates).ordered(candidates)

    def page(
        self,
        list_: QueueList,
        *,
        statuses: Mapping[str, ChunkStatus],
        cursor: str | None = None,
        limit: int,
    ) -> QueuePage:
        """``list_``'s chunks bounded and keyset-paginated (:meth:`QueueRanking.page`) over the
        already-materialized order, not a second SQL read. ``cursor`` is a prior
        :attr:`QueuePage.next_cursor`, else raising :class:`~blizzard.hub.domain.kernel.pagination.MalformedCursor`."""
        candidates = self._candidates(list_, statuses=statuses)
        return self._ranking(candidates).page(candidates, cursor=cursor, limit=limit)

    def ordered_ready(self, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        """Ready chunks in queue order — ascending by effective position."""
        return self.ordered(QueueList.READY, statuses=statuses)

    def ordered_not_ready(self, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        """``not_ready`` chunks in backlog order — ascending by effective position."""
        return self.ordered(QueueList.NOT_READY, statuses=statuses)

    def reorder(
        self, list_: QueueList, chunk_ids: Sequence[str], *, statuses: Mapping[str, ChunkStatus]
    ) -> list[Chunk]:
        """Replace ``list_``'s whole order with the named chunks first (:func:`replacement_order`
        decides it, resolving each id against the list itself) and return the order written."""
        ordered = replacement_order(list_, self.ordered(list_, statuses=statuses), chunk_ids, statuses)
        self.replace_order(list_, ordered)
        return ordered

    def move(
        self, list_: QueueList, chunk_id: str, after_chunk_id: str | None, *, statuses: Mapping[str, ChunkStatus]
    ) -> None:
        """Move ``chunk_id`` right after ``after_chunk_id`` (top when ``None``) within ``list_``,
        each id resolved against the list itself (:func:`resolve_move`)."""
        chunk, after = resolve_move(list_, self.ordered(list_, statuses=statuses), chunk_id, after_chunk_id, statuses)
        self.reposition(list_, chunk, after, statuses=statuses)

    def replace_order(self, list_: QueueList, ordered: list[Chunk]) -> None:
        """Idempotent whole-order replacement: one ascending explicit position fact per
        chunk in ``ordered``, front to back, in one write transaction. Takes
        already-resolved ``Chunk`` objects, never ids (``bzh:domain-takes-objects``), and
        trusts the order it is handed — :meth:`reorder` resolves it against the list."""
        write = self._write_fn(list_)
        write([(chunk.chunk_id, float(position)) for position, chunk in enumerate(ordered)], at=self._clock.now())
        _log.info("queue order replaced", list=list_.value, chunk_ids=[c.chunk_id for c in ordered])

    def reposition(
        self, list_: QueueList, chunk: Chunk, after: Chunk | None, *, statuses: Mapping[str, ChunkStatus]
    ) -> None:
        """Single-chunk fractional reorder within ``list_``: stamp ``chunk`` a new explicit position
        immediately after ``after`` (top when ``after is None``) without restamping every other chunk.
        An ``after`` outside ``list_`` is refused (:class:`NotInList`). Bisection exhausting the
        representable doubles renormalizes via :meth:`replace_order`."""
        write = self._write_fn(list_)
        candidates = [c for c in self._candidates(list_, statuses=statuses) if c.chunk_id != chunk.chunk_id]
        require_anchor_among(list_, candidates, after, statuses)
        ranking = self._ranking(candidates)
        ordered = ranking.ordered(candidates)
        new_position = ranking.slot_after(ordered, after)
        if new_position is None:
            assert after is not None  # the top slot is never exhausted
            after_index = next(i for i, c in enumerate(ordered) if c.chunk_id == after.chunk_id)
            self.replace_order(list_, [*ordered[: after_index + 1], chunk, *ordered[after_index + 1 :]])
            new_position = self._ranking(candidates).slot_after(ordered, after)
            assert new_position is not None  # whole-number positions always leave a gap
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
        :meth:`~blizzard.hub.domain.chunk.ports.queue.IWriteChunkQueueRepository.record_backlog_positions`,
        ``ready`` through
        :meth:`~blizzard.hub.domain.chunk.ports.queue.IWriteChunkQueueRepository.record_queue_positions`."""
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

    def _ranking(self, candidates: Sequence[Chunk]) -> QueueRanking:
        """The explicit positions and promotion instants of ``candidates`` alone — the
        ranking inputs, read bounded by the candidate set (``bzh:live-set-read``)."""
        chunk_ids = [c.chunk_id for c in candidates]
        return QueueRanking(
            positions=self._queue.queue_positions(chunk_ids), promoted_ats=self._queue.promoted_ats(chunk_ids)
        )


@dto
@dataclass(frozen=True)
class GroupResult:
    """A completed group: the survivor and the status it is left at.

    The status rides along because grouping does not imply ``ready``: folding backlog
    chunks yields a backlog survivor."""

    survivor: Chunk
    status: ChunkStatus
    # The last ``chunk_grouped.id`` this call wrote; ``None`` when ``merge_ids`` resolved to zero targets.
    grouped_id: int | None = None


def require_groupable(chunk_id: str, facts: ChunkFacts) -> ChunkStatus:
    """Refuse a group participant outside :attr:`ChunkVerb.GROUP`'s window with
    :class:`ChunkNotGroupable`; returns the status it is admitted at."""
    status = facts.status()
    if not verb_legal_from(ChunkVerb.GROUP, status):
        raise ChunkNotGroupable(chunk_id, status)
    return status


def merge_targets(survivor_id: str, merge_ids: Sequence[str]) -> list[str]:
    """The chunks a group folds, in first-named order: naming the survivor or one id twice is
    a no-op, not an error."""
    seen: set[str] = set()
    targets: list[str] = []
    for merge_id in merge_ids:
        if merge_id == survivor_id or merge_id in seen:
            continue
        seen.add(merge_id)
        targets.append(merge_id)
    return targets


def plan_group(standing: list[DependencyEdge], survivor_id: str, folded_ids: list[str]) -> FoldEdgePlan:
    """The edge plan folding ``folded_ids`` into ``survivor_id``, refused with
    :class:`FoldWouldCloseCycle` when the resulting standing graph would close a cycle — a
    set-level question over the whole fold, not per edge."""
    plan = plan_fold(standing, survivor_id, folded_ids)
    minted_pairs = [
        (m.dependent_chunk_id, m.prerequisite_chunk_id) for cid in folded_ids for m in plan.mint_by_target[cid]
    ]
    if would_close_a_cycle(plan.remaining, minted_pairs):
        raise FoldWouldCloseCycle(survivor_id, folded_ids)
    return plan


class GroupService:
    """Merge unacquired chunks — ``not_ready`` or ``ready`` — into one surviving chunk,
    carrying each folded chunk's standing dependency edges onto the survivor."""

    def __init__(
        self,
        *,
        work_refs: IWriteChunkWorkRefsRepository,
        dependencies: IWriteChunkDependenciesRepository,
        exclusive: IChunkExclusiveWrites,
        clock: IClock,
        cycle_lock: threading.Lock,
    ) -> None:
        self._work_refs = work_refs
        self._dependencies = dependencies
        # The locked-transaction seam (``bzh:store-exclusive-write``) ClaimService's own
        # CAS shares — the row lock over the survivor and every named merge id.
        self._exclusive = exclusive
        self._clock = clock
        # The residual fleet-wide lock DependencyService's own cycle check also shares —
        # closes the race a row lock over this fold's own chunks alone cannot.
        self._cycle_lock = cycle_lock

    def group(self, survivor_id: str, merge_ids: list[str]) -> GroupResult:
        """Fold ``merge_ids`` into ``survivor_id``; the survivor absorbs their pointers
        and each folded chunk's standing dependency edges. Refused before any
        write when the result would close a cycle (:class:`FoldWouldCloseCycle`)."""
        with self._cycle_lock, self._exclusive.locked([survivor_id, *merge_ids]) as handle:
            return self._group_locked(handle, survivor_id, merge_ids)

    def _group_locked(self, handle: ILockedChunkRead, survivor_id: str, merge_ids: list[str]) -> GroupResult:
        # One read each for the survivor and every merge id; the checks below run
        # against these maps in the same order the per-id reads did.
        ids = [survivor_id, *merge_ids]
        records = handle.records_for(ids)
        facts = handle.facts_for(ids)
        survivor, survivor_status = self._admitted(records, facts, survivor_id)
        targets = [self._admitted(records, facts, merge_id)[0] for merge_id in merge_targets(survivor_id, merge_ids)]
        folded_ids = [t.chunk_id for t in targets]
        plan = plan_group(handle.standing_edges(), survivor_id, folded_ids)
        now = self._clock.now()
        for target in targets:
            self._work_refs.add_work_refs_locked(handle, survivor_id, target.work_refs, at=now)

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
            # One call, one transaction across every target — a target's
            # own row can never commit ahead of a sibling's edge release/mint.
            grouped_ids = self._dependencies.record_fold_locked(
                handle, fold_targets, grouped_into=survivor_id, by=FOLD_ACTOR, at=now
            )
            grouped_id = grouped_ids[targets[-1].chunk_id]
        _log.info(
            "chunks grouped",
            survivor=survivor_id,
            status=survivor_status.value,
            merged=folded_ids,
            count=len(targets),
        )
        merged = handle.record(survivor_id)
        return GroupResult(
            survivor=merged if merged is not None else survivor, status=survivor_status, grouped_id=grouped_id
        )

    @staticmethod
    def _admitted(records: dict[str, Chunk], facts: dict[str, ChunkFacts], chunk_id: str) -> tuple[Chunk, ChunkStatus]:
        """``chunk_id``'s locked record and the status :func:`require_groupable` admits it at;
        :class:`ChunkNotFound` for one gone under the lock."""
        chunk = records.get(chunk_id)
        chunk_facts = facts.get(chunk_id)
        if chunk is None or chunk_facts is None:
            raise ChunkNotFound(chunk_id)
        return chunk, require_groupable(chunk_id, chunk_facts)
