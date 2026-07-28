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

from dataclasses import dataclass

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
#: Deliberately a status set rather than a route-liveness read. "No runner holds it" is
#: what motivated widening the gate past ``ready``, but it is **not** sufficient on its
#: own, and the set is not its synonym: ``paused``, ``waiting_on_human`` and
#: ``needs_human`` are all reachable with no live route at all — a never-claimed backlog
#: chunk can be paused (``PauseService`` refuses only ``done``/``stopped``/``delivering``),
#: and ``PAUSED`` outranks the un-promoted branch in the derivation, so it reads ``paused``
#: rather than ``not_ready``. Each of those is a standing human hold, and folding a chunk
#: out of existence under one is not this operation's call to make. Keying on the two
#: statuses keeps that correct without a second vocabulary of "available" to hold in
#: lockstep with the derivation.
GROUPABLE_STATUSES = frozenset({ChunkStatus.NOT_READY, ChunkStatus.READY})


class ChunkNotGroupable(ValueError):
    """A group op named a chunk that is not free to be folded away (issue #141).

    Grouping was gated on ``ready`` for every participant, so merging three backlog chunks
    meant promoting all three into the *claimable* queue first — making them claimable by
    any live runner mid-flow purely to satisfy a merge, and leaving the survivor ready as a
    side effect rather than by choice.

    The message names what is **actually enforced** rather than the motivating half of it.
    "No runner holds it" alone would be a false claim on a refused ``paused`` chunk, which
    is exactly the kind of wrong-invariant wording issue #141 set out to remove — one
    status over.
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
        ready = self._chunks.list_ready()
        return sorted(ready, key=lambda c: self._effective_position(c, positions))

    def replace_order(self, ordered: list[Chunk]) -> None:
        """Idempotent whole-order replacement: append one ascending explicit
        position fact per chunk in ``ordered``, front to back.

        Takes already-resolved ``Chunk`` objects, never ids (``bzh:domain-takes-objects``)
        — the caller (``PUT /api/queue``) has already validated every named id
        against the ready set and appended any unlisted ready chunk in its current
        order, so this method only has to record ranks, not resolve or validate
        anything. Ranking every chunk in ``ordered`` (not just the named ones)
        keeps the result deterministic regardless of positions left over from
        an earlier reorder."""
        at = self._clock.now()
        for position, chunk in enumerate(ordered):
            self._chunks.record_queue_position(chunk.chunk_id, position=float(position), at=at)
        _log.info("ready queue replaced", chunk_ids=[c.chunk_id for c in ordered])

    @staticmethod
    def _effective_position(chunk: Chunk, positions: dict[str, float]) -> float:
        """A chunk's sort key: its newest explicit position, else its mint instant.

        Before a chunk is ever moved, its position is its ``minted_at`` as a unix
        timestamp — so an un-reordered queue is plain FIFO, and any explicit move (a
        smaller float) lifts a chunk above the un-moved tail.
        """
        explicit = positions.get(chunk.chunk_id)
        return explicit if explicit is not None else chunk.minted_at.timestamp()


@dataclass(frozen=True)
class GroupResult:
    """A completed group: the survivor and the status it is left at (issue #141).

    The survivor's status rides along because grouping no longer implies ``ready``:
    folding backlog chunks yields a backlog survivor, so a caller that announces the
    change (the SSE ``chunk-changed`` frame) must publish what the survivor *is* rather
    than the ``"ready"`` the ready-only gate once made a safe constant.
    """

    survivor: Chunk
    status: ChunkStatus


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
        for target in targets:
            self._chunks.add_work_refs(survivor_id, target.work_refs, at=now)
            self._chunks.record_grouped(target.chunk_id, grouped_into=survivor_id, at=now)
        _log.info(
            "chunks grouped",
            survivor=survivor_id,
            status=survivor_status.value,
            merged=[t.chunk_id for t in targets],
            count=len(targets),
        )
        merged = self._chunks.get(survivor_id)
        return GroupResult(survivor=merged if merged is not None else survivor, status=survivor_status)

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
